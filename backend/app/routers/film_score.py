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
from app.film_score_adapt_schemas import (
    FilmAdaptError,
    FilmScoreAdaptCommitRequest,
    FilmScoreAdaptPreviewRequest,
    FilmTimelineSnapshot,
)
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
from app.services.composition_snapshot_encoding import composition_snapshot_fingerprint
from app.services.composition_timeline import compile_timeline
from app.services.film_score_adapt import compile_film_score_adaptation
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
_PATH_ADAPT_PREVIEW = "/projects/{project_id}/film-score/adapt/preview"
_PATH_ADAPT_COMMIT = "/projects/{project_id}/film-score/adapt/commit"


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


def _adapt_http(exc: FilmAdaptError, *, project_id: str) -> HTTPException:
    if exc.code in {"film_adapt_conflict", "film_adapt_timeline_mismatch", "film_adapt_span_too_large"}:
        logger.warning("%s project_id=%s", exc.code, project_id)
    return HTTPException(
        status_code=exc.http_status,
        detail={"code": exc.code, "message": exc.message},
    )


def _adapt_picture(project_id: str):
    """Load the score and the stored picture. Cue rows are not rewritten."""
    record = _project(project_id)
    db_path = get_project_db_path()
    try:
        asset = get_video_asset(project_id, db_path=db_path)
    except VideoScoringError as exc:
        if exc.code == "video_asset_missing":
            raise FilmAdaptError("film_adapt_asset_missing") from exc
        raise FilmAdaptError("film_adapt_invalid") from exc
    scoring = get_video_scoring(project_id, db_path=db_path)
    if (
        scoring.frame_rate_numerator is None
        or scoring.frame_rate_denominator is None
        or not is_closed_frame_rate(scoring.frame_rate_numerator, scoring.frame_rate_denominator)
    ):
        raise FilmAdaptError("film_adapt_frame_rate_required")
    try:
        composition = _composition(record)
    except FilmScoreError as exc:
        raise FilmAdaptError("film_adapt_invalid") from exc
    stored = FilmTimelineSnapshot(
        duration_seconds=float(asset.duration_seconds),
        frame_rate_numerator=int(scoring.frame_rate_numerator),
        frame_rate_denominator=int(scoring.frame_rate_denominator),
        video_origin_seconds=float(scoring.video_origin_seconds),
        musical_origin_tick=int(scoring.musical_origin_tick),
        cues=_snapshots(scoring.hit_points),
    )
    return record, db_path, scoring, composition, stored


@router.post(_PATH_ADAPT_PREVIEW)
def preview_film_score_adapt(project_id: str, body: FilmScoreAdaptPreviewRequest):
    enforce_current(project_id, "write_score")
    try:
        _record, _db_path, scoring, composition, stored = _adapt_picture(project_id)
        source_fingerprint = composition_snapshot_fingerprint(composition)
        if source_fingerprint != body.expected_source_fingerprint:
            raise FilmAdaptError("film_adapt_conflict")
        preview = compile_film_score_adaptation(
            project_id=project_id,
            composition=composition,
            source_fingerprint=source_fingerprint,
            scoring_document_revision=scoring.document_revision,
            previous=body.previous,
            edits=body.edits,
            next_timeline=stored,
        )
    except FilmAdaptError as exc:
        raise _adapt_http(exc, project_id=project_id) from exc
    strategies = [operation.strategy for operation in preview.proposal.operations]
    logger.info(
        "film adapt preview finished project_id=%s edit_count=%s strategies=%s "
        "events_unchanged=%s events_shifted=%s events_removed=%s events_added=%s warnings=%s",
        project_id,
        len(body.edits),
        ",".join(strategies),
        preview.proposal.counts.events_unchanged,
        preview.proposal.counts.events_shifted,
        preview.proposal.counts.events_removed,
        preview.proposal.counts.events_added,
        ",".join(preview.proposal.warnings),
    )
    return preview.model_dump(mode="json")


@router.post(_PATH_ADAPT_COMMIT)
def commit_film_score_adapt(project_id: str, body: FilmScoreAdaptCommitRequest):
    enforce_current(project_id, "write_score")
    try:
        _record, db_path, scoring, composition, stored = _adapt_picture(project_id)
        source_fingerprint = composition_snapshot_fingerprint(composition)
        if (
            source_fingerprint != body.expected_source_fingerprint
            or scoring.document_revision != body.expected_document_revision
        ):
            raise FilmAdaptError("film_adapt_conflict")
        preview = compile_film_score_adaptation(
            project_id=project_id,
            composition=composition,
            source_fingerprint=source_fingerprint,
            scoring_document_revision=scoring.document_revision,
            previous=body.previous,
            edits=body.edits,
            next_timeline=stored,
        )
        if preview.candidate_fingerprint != body.candidate_fingerprint or preview.candidate is None:
            raise FilmAdaptError("film_adapt_conflict")
        candidate = CompositionV2.model_validate(preview.candidate)
        strategies = [operation.strategy for operation in preview.proposal.operations]
        commit = DurableCommitRequest(
            branch_id=body.branch_id,
            expected_active_branch_id=body.expected_active_branch_id,
            expected_working_version=body.expected_working_version,
            expected_head_revision_id=body.expected_head_revision_id,
            expected_source_fingerprint=body.expected_source_fingerprint,
            composition=candidate,
            operation_type=RevisionOperationType.FILM_SCORE_ADAPT_APPLY,
            ai=AiProvenance(
                provider="film-score",
                operation="film-score-adapt-apply",
                candidate_fingerprint=preview.candidate_fingerprint,
                generation_parameters={
                    "strategies": strategies,
                    "warning_codes": list(preview.proposal.warnings),
                },
                warning_codes=list(preview.proposal.warnings),
            ),
        )
        commit_revision(project_id, commit, db_path=db_path)
    except FilmAdaptError as exc:
        raise _adapt_http(exc, project_id=project_id) from exc
    except (ProjectRevisionConflictError, ProjectHistoryValidationError) as exc:
        raise _adapt_http(FilmAdaptError("film_adapt_conflict"), project_id=project_id) from exc
    logger.info(
        "film adapt commit finished project_id=%s fingerprint_prefix=%s",
        project_id,
        (preview.candidate_fingerprint or "")[:12],
    )
    warnings = list(preview.proposal.warnings)
    if "film_origin_unchanged" not in warnings:
        warnings.append("film_origin_unchanged")
    return {"committed": True, "warnings": warnings}
