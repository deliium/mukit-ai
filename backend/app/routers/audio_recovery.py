"""HTTP API for V4 mixed audio recovery jobs (ingress).

Routes live here — not in main.py. Never mutates composition.v2.
Durable assets require Bind with project_id after client Apply.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.audio_alignment_schemas import AudioAlignmentBoundDiscoveryV1
from app.audio_recovery_schemas import (
    AudioRecoveryBindRequestV1,
    AudioRecoveryBindResponseV1,
    AudioRecoveryError,
    AudioRecoveryJobV1,
)
from app.services.audio_recovery.pipeline import (
    bind_audio_recovery_job,
    delete_audio_recovery_job,
    discover_bound_recovery_for_project,
    enqueue_audio_recovery_job,
    get_audio_recovery_job,
    resolve_asset_file_path,
)
from app.routers.collaboration_guard import enforce_current


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/audio-recovery", tags=["audio-recovery"])


def _path_basename(name: str) -> str:
    """Basename-only for logging (never full user paths)."""
    return Path(name).name


def _map_error(exc: AudioRecoveryError) -> HTTPException:
    detail: dict = {"code": exc.code, "message": exc.message}
    if exc.details:
        safe = {
            key: value
            for key, value in exc.details.items()
            if isinstance(value, (str, int, float, bool)) or value is None
        }
        if safe:
            detail["details"] = safe
    return HTTPException(status_code=exc.http_status, detail=detail)


@router.post("/jobs", response_model=AudioRecoveryJobV1)
async def create_recovery_job(
    file: UploadFile = File(...),
    project_id: str | None = Form(default=None),
    disable_separation: bool = Form(default=False),
) -> AudioRecoveryJobV1:
    started = time.perf_counter()
    filename = file.filename or "upload.wav"
    logger.info(
        "POST /audio-recovery/jobs",
        extra={
            "basename": _path_basename(filename),
            "has_project_id": bool(project_id),
            "disable_separation": disable_separation,
        },
    )
    payload = await file.read()
    enforce_current(project_id, "write_score")
    try:
        job = enqueue_audio_recovery_job(
            payload,
            display_filename=filename,
            project_id=project_id or None,
            disable_separation=disable_separation,
            run_inline=True,
        )
    except AudioRecoveryError as exc:
        raise _map_error(exc) from exc
    duration_ms = int((time.perf_counter() - started) * 1000)
    note_count = job.preview.summary.note_count if job.preview else 0
    logger.info(
        "POST /audio-recovery/jobs done",
        extra={
            "job_id": job.id,
            "status": job.status,
            "duration_ms": duration_ms,
            "stem_list": (
                [s.stem for s in job.preview.stems] if job.preview else []
            ),
            "note_count": note_count,
        },
    )
    return job


@router.get("/jobs/{job_id}", response_model=AudioRecoveryJobV1)
async def get_job(job_id: str) -> AudioRecoveryJobV1:
    logger.info("GET /audio-recovery/jobs/{id}", extra={"job_id": job_id})
    try:
        job = get_audio_recovery_job(job_id)
    except AudioRecoveryError as exc:
        raise _map_error(exc) from exc
    enforce_current(job.project_id, "read")
    return job


@router.delete("/jobs/{job_id}", status_code=204)
async def cancel_job(job_id: str) -> None:
    logger.info("DELETE /audio-recovery/jobs/{id}", extra={"job_id": job_id})
    try:
        job = get_audio_recovery_job(job_id)
        enforce_current(job.project_id, "write_score")
        delete_audio_recovery_job(job_id)
    except AudioRecoveryError as exc:
        raise _map_error(exc) from exc


@router.post("/jobs/{job_id}/bind", response_model=AudioRecoveryBindResponseV1)
async def bind_job(job_id: str, body: AudioRecoveryBindRequestV1) -> AudioRecoveryBindResponseV1:
    logger.info(
        "POST /audio-recovery/jobs/{id}/bind",
        extra={
            "job_id": job_id,
            "project_id": body.project_id,
            "event_map_count": len(body.event_map),
        },
    )
    enforce_current(body.project_id, "write_score")
    try:
        result = bind_audio_recovery_job(job_id, body)
    except AudioRecoveryError as exc:
        logger.warning(
            "Audio recovery bind rejected",
            extra={"job_id": job_id, "error_code": exc.code},
        )
        raise _map_error(exc) from exc
    logger.info(
        "POST /audio-recovery/jobs/{id}/bind done",
        extra={
            "job_id": job_id,
            "source_audio_asset_id": result.source_audio_asset_id,
            "result_asset_id": result.result_asset_id,
            "alignment_asset_id": result.alignment_asset_id,
            "overlay_entry_count": result.overlay_entry_count,
        },
    )
    return result


@router.get(
    "/projects/{project_id}/bound",
    response_model=AudioAlignmentBoundDiscoveryV1,
)
async def get_project_bound(project_id: str) -> AudioAlignmentBoundDiscoveryV1:
    logger.info(
        "GET /audio-recovery/projects/{id}/bound",
        extra={"project_id_prefix": project_id[:8] if project_id else None},
    )
    enforce_current(project_id, "read")
    try:
        discovery = discover_bound_recovery_for_project(project_id)
    except AudioRecoveryError as exc:
        raise _map_error(exc) from exc
    logger.info(
        "GET /audio-recovery/projects/{id}/bound done",
        extra={
            "project_id_prefix": project_id[:8] if project_id else None,
            "bound": discovery.bound,
            "job_count": len(discovery.jobs),
        },
    )
    return discovery


@router.get("/assets/{asset_id}")
async def download_asset(asset_id: str) -> FileResponse:
    logger.info("GET /audio-recovery/assets/{id}", extra={"asset_id": asset_id})
    try:
        abs_path, content_type, basename = resolve_asset_file_path(asset_id)
    except AudioRecoveryError as exc:
        raise _map_error(exc) from exc
    return FileResponse(
        path=str(abs_path),
        media_type=content_type,
        filename=basename,
    )
