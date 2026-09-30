"""Project picture upload, scoring document, and the video-time map.

The media route returns the original bytes. Handlers do not open the stored
file for write and do not change ``composition.v2``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response

from app.audio_upload import UploadTooLargeError, read_upload_bounded
from app.db.connection import get_project_db_path
from app.routers.collaboration_guard import enforce_current
from app.services.composition_timeline import compile_timeline
from app.services.project_store import ProjectNotFoundError, get_project
from app.services.video_scoring_map import map_bar_to_video, map_tick_to_video, map_video_to_music
from app.services.video_scoring_store import (
    delete_video_asset,
    get_video_asset,
    get_video_scoring,
    ingest_video_bytes,
    put_video_scoring,
    video_media_path,
)
from app.video_scoring_schemas import (
    VIDEO_SCORING_ERROR_MESSAGES,
    VideoAssetUploadResponseV1,
    VideoAssetV1,
    VideoScoringError,
    VideoScoringMapResponseV1,
    VideoScoringUpdateV1,
    VideoScoringV1,
)
from app.video_scoring_settings import load_video_scoring_settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["video-scoring"])

_PATH_ASSET = "/projects/{project_id}/video-asset"
_PATH_MEDIA = "/projects/{project_id}/video-asset/media"
_PATH_SCORING = "/projects/{project_id}/video-scoring"
_PATH_MAP = "/projects/{project_id}/video-scoring/map"


def _require_project(project_id: str):
    try:
        return get_project(project_id, db_path=get_project_db_path())
    except ProjectNotFoundError as exc:
        logger.info(
            "video scoring response",
            extra={"project_id": project_id, "status": 404, "error_code": "project_not_found"},
        )
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _reject(exc: VideoScoringError, *, project_id: str, method: str, path: str) -> HTTPException:
    if exc.http_status in {409, 422}:
        logger.warning(
            "video scoring request rejected",
            extra={
                "method": method,
                "path": path,
                "project_id": project_id,
                "status": exc.http_status,
                "error_code": exc.code,
            },
        )
    else:
        logger.info(
            "video scoring response",
            extra={
                "method": method,
                "path": path,
                "project_id": project_id,
                "status": exc.http_status,
                "error_code": exc.code,
            },
        )
    return HTTPException(
        status_code=exc.http_status,
        detail={"code": exc.code, "message": exc.message},
    )


def _done(method: str, path: str, project_id: str, status: int, asset_id: str | None) -> None:
    logger.info(
        "video scoring response",
        extra={
            "method": method,
            "path": path,
            "project_id": project_id,
            "status": status,
            "asset_id": asset_id,
        },
    )


def _timeline(project_id: str):
    record = _require_project(project_id)
    if not record.composition_json:
        raise VideoScoringError("video_composition_missing")
    try:
        payload = json.loads(record.composition_json)
    except json.JSONDecodeError as exc:
        raise VideoScoringError("video_composition_missing") from exc
    return compile_timeline(payload)


def _map_inputs(project_id: str):
    asset = get_video_asset(project_id, db_path=get_project_db_path())
    scoring = get_video_scoring(project_id, db_path=get_project_db_path())
    if scoring.frame_rate_numerator is None or scoring.frame_rate_denominator is None:
        raise VideoScoringError("video_frame_rate_required")
    timeline = _timeline(project_id)
    return asset, scoring, timeline


@router.post(_PATH_ASSET, status_code=201, response_model=VideoAssetUploadResponseV1)
async def upload_video_asset(project_id: str, file: UploadFile = File(...)) -> VideoAssetUploadResponseV1:
    enforce_current(project_id, "write_score")
    _require_project(project_id)
    settings = load_video_scoring_settings()
    basename = Path(file.filename or "video").name
    logger.info(
        "video scoring request",
        extra={
            "method": "POST",
            "path": _PATH_ASSET,
            "project_id": project_id,
            "basename": basename,
        },
    )
    try:
        payload = await read_upload_bounded(file, max_bytes=settings.max_upload_bytes)
    except UploadTooLargeError as exc:
        logger.warning(
            "video scoring request rejected",
            extra={
                "method": "POST",
                "path": _PATH_ASSET,
                "project_id": project_id,
                "status": 413,
                "error_code": "video_upload_too_large",
                "limit_bytes": exc.limit_bytes,
            },
        )
        raise HTTPException(
            status_code=413,
            detail={
                "code": "video_upload_too_large",
                "message": VIDEO_SCORING_ERROR_MESSAGES["video_upload_too_large"],
            },
        ) from exc
    try:
        asset, scoring = ingest_video_bytes(
            payload,
            project_id=project_id,
            db_path=get_project_db_path(),
            settings=settings,
        )
    except VideoScoringError as exc:
        raise _reject(exc, project_id=project_id, method="POST", path=_PATH_ASSET) from exc
    _done("POST", _PATH_ASSET, project_id, 201, asset.asset_id)
    return VideoAssetUploadResponseV1(asset=asset, scoring=scoring)


@router.get(_PATH_MEDIA)
def read_video_media(project_id: str) -> FileResponse:
    enforce_current(project_id, "read")
    _require_project(project_id)
    try:
        asset, path = video_media_path(project_id, db_path=get_project_db_path())
    except VideoScoringError as exc:
        raise _reject(exc, project_id=project_id, method="GET", path=_PATH_MEDIA) from exc
    _done("GET", _PATH_MEDIA, project_id, 200, asset.asset_id)
    return FileResponse(
        path,
        media_type=asset.content_type,
        filename=f"{asset.asset_id}.{asset.container}",
        content_disposition_type="inline",
    )


@router.get(_PATH_ASSET, response_model=VideoAssetV1)
def read_video_asset(project_id: str) -> VideoAssetV1:
    enforce_current(project_id, "read")
    _require_project(project_id)
    try:
        asset = get_video_asset(project_id, db_path=get_project_db_path())
    except VideoScoringError as exc:
        raise _reject(exc, project_id=project_id, method="GET", path=_PATH_ASSET) from exc
    _done("GET", _PATH_ASSET, project_id, 200, asset.asset_id)
    return asset


@router.delete(_PATH_ASSET, status_code=204)
def remove_video_asset(project_id: str) -> Response:
    enforce_current(project_id, "write_score")
    _require_project(project_id)
    try:
        scoring = delete_video_asset(project_id, db_path=get_project_db_path())
    except VideoScoringError as exc:
        raise _reject(exc, project_id=project_id, method="DELETE", path=_PATH_ASSET) from exc
    _done("DELETE", _PATH_ASSET, project_id, 204, scoring.asset_id)
    return Response(status_code=204)


@router.get(_PATH_SCORING, response_model=VideoScoringV1)
def read_video_scoring(project_id: str) -> VideoScoringV1:
    enforce_current(project_id, "read")
    _require_project(project_id)
    document = get_video_scoring(project_id, db_path=get_project_db_path())
    _done("GET", _PATH_SCORING, project_id, 200, document.asset_id)
    return document


@router.put(_PATH_SCORING, response_model=VideoScoringV1)
def write_video_scoring(project_id: str, body: VideoScoringUpdateV1) -> VideoScoringV1:
    enforce_current(project_id, "write_score")
    _require_project(project_id)
    try:
        timeline = _timeline(project_id)
    except VideoScoringError as exc:
        raise _reject(exc, project_id=project_id, method="PUT", path=_PATH_SCORING) from exc
    if body.musical_origin_tick > timeline.duration_ticks:
        raise _reject(
            VideoScoringError("video_musical_origin_outside"),
            project_id=project_id,
            method="PUT",
            path=_PATH_SCORING,
        )
    try:
        document = put_video_scoring(project_id, body, db_path=get_project_db_path())
    except VideoScoringError as exc:
        raise _reject(exc, project_id=project_id, method="PUT", path=_PATH_SCORING) from exc
    _done("PUT", _PATH_SCORING, project_id, 200, document.asset_id)
    return document


@router.get(_PATH_MAP, response_model=VideoScoringMapResponseV1)
def read_video_map(
    project_id: str,
    video_seconds: float | None = None,
    tick: int | None = Query(default=None),
    bar: int | None = Query(default=None),
) -> VideoScoringMapResponseV1:
    enforce_current(project_id, "read")
    _require_project(project_id)
    selected = [value is not None for value in (video_seconds, tick, bar)]
    if sum(selected) != 1:
        raise _reject(
            VideoScoringError("video_map_selector_invalid"),
            project_id=project_id,
            method="GET",
            path=_PATH_MAP,
        )
    try:
        asset, scoring, timeline = _map_inputs(project_id)
        common = {
            "timeline": timeline,
            "duration_seconds": asset.duration_seconds,
            "video_origin_seconds": scoring.video_origin_seconds,
            "musical_origin_tick": scoring.musical_origin_tick,
            "frame_rate_numerator": scoring.frame_rate_numerator,
            "frame_rate_denominator": scoring.frame_rate_denominator,
            "timecode_mode": scoring.timecode_mode,
            "start_timecode": scoring.start_timecode,
        }
        if video_seconds is not None:
            mapped = map_video_to_music(video_seconds, **common)
        elif tick is not None:
            mapped = map_tick_to_video(tick, **common)
        else:
            mapped = map_bar_to_video(int(bar or 0), **common)
    except VideoScoringError as exc:
        raise _reject(exc, project_id=project_id, method="GET", path=_PATH_MAP) from exc
    _done("GET", _PATH_MAP, project_id, 200, asset.asset_id)
    return VideoScoringMapResponseV1(
        video_seconds=mapped.video_seconds,
        tick=mapped.tick,
        bar=mapped.bar,
        timecode=mapped.timecode,
        warnings=list(mapped.warnings),
    )
