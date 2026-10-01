"""Explicit film-score preview and commit.

The router is the only video reader on this path. Preview writes nothing.
Commit stores the candidate and the sync origin, and leaves cue rows in place.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from app.ai_agents.errors import AgentError
from app.ai_agents.registry import ensure_registry
from app.composition_schemas import CompositionV2
from app.db.connection import get_project_db_path
from app.film_score_schemas import (
    FilmCueSnapshot,
    FilmScoreCommitRequest,
    FilmScoreError,
    FilmScorePreviewRequest,
)
from app.project_history_schemas import AiProvenance, DurableCommitRequest, RevisionOperationType
from app.routers.collaboration_guard import enforce_current
from app.services.artifact_role_map import validate_artifact_role_map
from app.services.composition_edit_fingerprint import composition_edit_fingerprint
from app.services.composition_timeline import compile_timeline
from app.services.film_score_tempo import music_window_start
from app.services.film_score_workflow import run_film_score_preview
from app.services.project_history import (
    ProjectHistoryValidationError,
    ProjectRevisionConflictError,
    commit_revision,
)
from app.services.project_store import ProjectNotFoundError, get_project
from app.services.video_scoring_store import get_video_asset, get_video_scoring, put_video_scoring
from app.video_scoring_schemas import VideoScoringError, VideoScoringUpdateV1, is_closed_frame_rate

logger = logging.getLogger(__name__)

router = APIRouter(tags=["film-score"])

_PATH_PREVIEW = "/projects/{project_id}/film-score/preview"
_PATH_COMMIT = "/projects/{project_id}/film-score/commit"


def _http(exc: FilmScoreError) -> HTTPException:
    body: dict[str, object] = {"code": exc.code, "message": exc.message}
    if exc.preview is not None and hasattr(exc.preview, "model_dump"):
        body["preview"] = exc.preview.model_dump(mode="json")
    return HTTPException(status_code=exc.http_status, detail=body)


def _project(project_id: str):
    try:
        return get_project(project_id, db_path=get_project_db_path())
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _composition(record) -> CompositionV2:
    if not record.composition_json:
        raise FilmScoreError("film_score_invalid")
    try:
        return CompositionV2.model_validate(json.loads(record.composition_json))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise FilmScoreError("film_score_invalid") from exc


def _snapshots(hits) -> list[FilmCueSnapshot]:
    return [
        FilmCueSnapshot(
            id=hit.id,
            kind=hit.kind,
            importance=hit.importance,
            video_seconds=hit.video_seconds,
            tolerance_frames=hit.tolerance_frames,
        )
        for hit in hits
    ]


def _has_notes(composition: CompositionV2) -> bool:
    return any(track.events for track in composition.tracks)


@router.post(_PATH_PREVIEW)
async def preview_film_score(project_id: str, body: FilmScorePreviewRequest):
    enforce_current(project_id, "write_score")
    record = _project(project_id)
    db_path = get_project_db_path()
    try:
        asset = get_video_asset(project_id, db_path=db_path)
    except VideoScoringError as exc:
        if exc.code == "video_asset_missing":
            raise _http(FilmScoreError("film_asset_missing")) from exc
        raise _http(FilmScoreError("film_score_invalid")) from exc
    scoring = get_video_scoring(project_id, db_path=db_path)
    if (
        scoring.frame_rate_numerator is None
        or scoring.frame_rate_denominator is None
        or not is_closed_frame_rate(scoring.frame_rate_numerator, scoring.frame_rate_denominator)
    ):
        raise _http(FilmScoreError("film_frame_rate_required"))
    composition = _composition(record)
    ensure_registry()
    try:
        preview = await run_film_score_preview(
            project_id=project_id,
            source=composition,
            source_fingerprint=composition_edit_fingerprint(composition),
            scoring_document_revision=scoring.document_revision,
            cues=_snapshots(scoring.hit_points),
            frame_rate_numerator=int(scoring.frame_rate_numerator),
            frame_rate_denominator=int(scoring.frame_rate_denominator),
            asset_duration_seconds=float(asset.duration_seconds),
            request=body,
            timecode_mode=scoring.timecode_mode,
            start_timecode=scoring.start_timecode,
        )
    except FilmScoreError as exc:
        raise _http(exc) from exc
    return preview.model_dump(mode="json")


@router.post(_PATH_COMMIT)
def commit_film_score(project_id: str, body: FilmScoreCommitRequest):
    enforce_current(project_id, "write_score")
    record = _project(project_id)
    db_path = get_project_db_path()
    try:
        candidate = CompositionV2.model_validate(body.candidate)
    except ValidationError as exc:
        raise _http(FilmScoreError("film_score_invalid")) from exc
    fingerprint = composition_edit_fingerprint(candidate)
    if fingerprint != body.candidate_fingerprint:
        raise _http(FilmScoreError("film_score_conflict"))
    source = _composition(record)
    if _has_notes(source) and not body.replace_existing:
        raise _http(FilmScoreError("film_score_replace_required"))
    try:
        validate_artifact_role_map(body.artifact_role_map, require_revision_plan=False)
    except AgentError as exc:
        raise _http(FilmScoreError("film_score_conflict")) from exc
    scoring = get_video_scoring(project_id, db_path=db_path)
    if scoring.document_revision != body.expected_document_revision:
        raise _http(FilmScoreError("film_score_conflict"))
    commit = DurableCommitRequest(
        branch_id=body.branch_id,
        expected_active_branch_id=body.expected_active_branch_id,
        expected_working_version=body.expected_working_version,
        expected_head_revision_id=body.expected_head_revision_id,
        expected_source_fingerprint=body.expected_source_fingerprint,
        composition=candidate,
        operation_type=RevisionOperationType.MULTI_AGENT_APPLY,
        ai=AiProvenance(
            provider="film-score",
            operation="film-score-commit",
            candidate_fingerprint=fingerprint,
            generation_parameters={
                "artifact_role_map": body.artifact_role_map,
                "artifact_envelopes": body.artifact_log,
            },
        ),
    )
    try:
        commit_revision(project_id, commit, db_path=db_path)
    except (ProjectRevisionConflictError, ProjectHistoryValidationError, AgentError) as exc:
        logger.warning("film_score_conflict project_id=%s", project_id)
        raise _http(FilmScoreError("film_score_conflict")) from exc
    prefix = fingerprint[:12]
    logger.info(
        "film score commit finished project_id=%s fingerprint_prefix=%s",
        project_id,
        prefix,
    )
    warnings: list[str] = []
    origin = music_window_start(_snapshots(scoring.hit_points))
    try:
        timeline = compile_timeline(candidate)
        hits = []
        for hit in scoring.hit_points:
            payload = hit.model_dump()
            payload["timecode"] = None
            hits.append(payload)
        put_video_scoring(
            project_id,
            VideoScoringUpdateV1.model_validate(
                {
                    "expected_document_revision": scoring.document_revision,
                    "frame_rate_numerator": scoring.frame_rate_numerator,
                    "frame_rate_denominator": scoring.frame_rate_denominator,
                    "frame_rate_source": scoring.frame_rate_source,
                    "timecode_mode": scoring.timecode_mode,
                    "start_timecode": scoring.start_timecode,
                    "video_origin_seconds": origin,
                    "musical_origin_tick": 0,
                    "hit_points": hits,
                }
            ),
            db_path=db_path,
            timeline=timeline,
        )
    except (VideoScoringError, ValidationError):
        warnings.append("film_origin_not_updated")
        logger.warning("film_origin_not_updated project_id=%s", project_id)
    updated = _project(project_id)
    return {
        "committed": True,
        "warnings": warnings,
        "video_origin_seconds": origin,
        "musical_origin_tick": 0,
        "composition": json.loads(updated.composition_json) if updated.composition_json else None,
    }
