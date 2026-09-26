"""HTTP routes for non-destructive mix plans (preview, apply, reject, undo)."""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from app.mix_plan_schemas import (
    MixPlanApplyRequest,
    MixPlanApplyResponse,
    MixPlanError,
    MixPlanPreviewRequest,
    MixPlanPreviewResponse,
    MixPlanRevisionGetResponse,
    MixPlanRevisionListResponse,
    MixPlanUndoResponse,
    map_mix_plan_error_to_http,
)
from app.services.mix_plan.pipeline import (
    apply_mix,
    get_revision_detail,
    list_revisions,
    preview_mix,
    preview_wav_path,
    reject_preview,
    revision_wav_path,
    undo_mix,
)
from app.routers.collaboration_guard import enforce_current

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mix-plan", tags=["mix-plan"])


@router.post("/preview", response_model=MixPlanPreviewResponse)
async def preview_mix_plan(request: MixPlanPreviewRequest) -> MixPlanPreviewResponse:
    started = time.perf_counter()
    logger.info(
        "POST /mix-plan/preview",
        extra={
            "stem_set_id": request.stem_set_id,
            "project_id": request.project_id,
            "master_target": request.master_target,
            "char_count": len(request.phrase or ""),
            "include_audio_preview": request.include_audio_preview,
        },
    )
    enforce_current(request.project_id, "read")
    try:
        body = preview_mix(request)
    except MixPlanError as exc:
        _raise(exc, stem_set_id=request.stem_set_id)
    duration_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "POST /mix-plan/preview ok",
        extra={
            "preview_id": body.preview_id,
            "stem_set_id": request.stem_set_id,
            "op_count": len(body.plan.ops),
            "http_status": 200,
            "duration_ms": duration_ms,
        },
    )
    return body


@router.delete("/previews/{preview_id}")
async def reject_mix_plan_preview(preview_id: str) -> dict[str, bool]:
    logger.info("DELETE /mix-plan/previews", extra={"preview_id": preview_id})
    try:
        reject_preview(preview_id)
    except MixPlanError as exc:
        _raise(exc, preview_id=preview_id)
    return {"rejected": True}


@router.post("/apply", response_model=MixPlanApplyResponse)
async def apply_mix_plan(request: MixPlanApplyRequest) -> MixPlanApplyResponse:
    started = time.perf_counter()
    logger.info(
        "POST /mix-plan/apply",
        extra={"preview_id": request.preview_id, "stem_set_id": request.plan.stem_set_id},
    )
    enforce_current(request.plan.project_id, "write_audio")
    try:
        body = apply_mix(request)
    except MixPlanError as exc:
        _raise(exc, preview_id=request.preview_id)
    duration_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "POST /mix-plan/apply ok",
        extra={
            "revision_id": body.revision.id,
            "preview_id": request.preview_id,
            "op_count": len(request.plan.ops),
            "http_status": 200,
            "duration_ms": duration_ms,
        },
    )
    return body


@router.post("/revisions/{revision_id}/undo", response_model=MixPlanUndoResponse)
async def undo_mix_plan(revision_id: str) -> MixPlanUndoResponse:
    logger.info("POST /mix-plan/revisions/undo", extra={"revision_id": revision_id})
    try:
        meta, _plan = get_revision_detail(revision_id)
        enforce_current(meta.project_id, "write_audio")
        body = undo_mix(revision_id)
    except MixPlanError as exc:
        _raise(exc, revision_id=revision_id)
    logger.info(
        "POST /mix-plan/revisions/undo ok",
        extra={"revision_id": revision_id, "head_revision_id": body.head_revision_id, "http_status": 200},
    )
    return body


@router.get("/revisions", response_model=MixPlanRevisionListResponse)
async def list_mix_plan_revisions(
    project_id: str = Query(min_length=1),
    stem_set_id: str | None = Query(default=None),
) -> MixPlanRevisionListResponse:
    logger.info(
        "GET /mix-plan/revisions",
        extra={"project_id": project_id, "stem_set_id": stem_set_id},
    )
    enforce_current(project_id, "read")
    try:
        items = list_revisions(project_id, stem_set_id)
    except MixPlanError as exc:
        _raise(exc, project_id=project_id)
    return MixPlanRevisionListResponse(items=items)


@router.get("/revisions/{revision_id}", response_model=MixPlanRevisionGetResponse)
async def get_mix_plan_revision(revision_id: str) -> MixPlanRevisionGetResponse:
    try:
        meta, plan = get_revision_detail(revision_id)
    except MixPlanError as exc:
        _raise(exc, revision_id=revision_id)
    enforce_current(meta.project_id, "read")
    return MixPlanRevisionGetResponse(revision=meta, plan=plan)


@router.get("/revisions/{revision_id}/audio")
async def download_mix_revision_audio(revision_id: str) -> FileResponse:
    logger.info("GET mix plan revision audio", extra={"revision_id": revision_id})
    try:
        meta, _plan = get_revision_detail(revision_id)
        enforce_current(meta.project_id, "read")
        path = revision_wav_path(revision_id)
    except MixPlanError as exc:
        _raise(exc, revision_id=revision_id)
    return FileResponse(path, media_type="audio/wav", filename=f"mix-{revision_id}.wav")


@router.get("/previews/{preview_id}/audio")
async def download_mix_preview_audio(
    preview_id: str,
    which: str = Query(default="processed"),
) -> FileResponse:
    logger.info(
        "GET mix plan preview audio",
        extra={"preview_id": preview_id, "which": which},
    )
    try:
        path = preview_wav_path(preview_id, which)
    except MixPlanError as exc:
        _raise(exc, preview_id=preview_id)
    return FileResponse(path, media_type="audio/wav", filename=f"mix-preview-{which}.wav")


def _raise(exc: MixPlanError, **extra: object) -> None:
    status, detail = map_mix_plan_error_to_http(exc)
    logger.warning(
        "Mix plan request failed",
        extra={"code": exc.code, "http_status": status, **{k: v for k, v in extra.items() if v}},
    )
    raise HTTPException(status_code=status, detail=detail) from exc
