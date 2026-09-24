"""HTTP API for optional neural audio render jobs (egress only).

Routes live here — not in main.py export handlers. Never mutates composition.v2.
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from app.neural_audio_schemas import (
    NeuralAudioEnqueueRequest,
    NeuralAudioError,
    NeuralAudioJobListResponse,
    NeuralAudioJobResponse,
    NeuralAudioStemEnqueueRequest,
    NeuralAudioStemRerenderRequest,
    NeuralAudioStemSetListResponse,
    NeuralAudioStemSetResponse,
)
from app.services.neural_audio_render import (
    delete_neural_audio_job,
    enqueue_neural_audio_render,
    get_neural_audio_job,
    list_neural_audio_jobs,
    resolve_audio_file_path,
)
from app.services.neural_audio_stems import (
    delete_stem_set_job,
    enqueue_stem_set,
    get_stem_set,
    list_stem_sets,
    rerender_stem,
    resolve_stem_audio_file_path,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/neural-audio", tags=["neural-audio"])


def _map_error(exc: NeuralAudioError) -> HTTPException:
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


@router.post("/renders", response_model=NeuralAudioJobResponse)
async def enqueue_render(body: NeuralAudioEnqueueRequest) -> NeuralAudioJobResponse:
    started = time.perf_counter()
    logger.info(
        "POST /neural-audio/renders",
        extra={
            "has_project_id": bool(body.project_id),
            "has_revision": bool(body.source_revision_id),
            "has_composition": bool(body.composition),
            "model_id": body.model_id,
            "adapter_kind": body.adapter_kind,
            "instruction_chars": len(body.instructions or ""),
        },
    )
    try:
        job = enqueue_neural_audio_render(body, run_inline=True)
    except NeuralAudioError as exc:
        raise _map_error(exc) from exc
    duration_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "POST /neural-audio/renders done",
        extra={
            "render_id": job.id,
            "status": job.status,
            "duration_ms": duration_ms,
            "model_id": job.model_id,
            "fidelity_class": job.fidelity_class,
        },
    )
    return job


@router.get("/renders", response_model=NeuralAudioJobListResponse)
async def list_renders(
    project_id: str = Query(..., min_length=1, max_length=64),
) -> NeuralAudioJobListResponse:
    logger.info("GET /neural-audio/renders", extra={"project_id": project_id})
    try:
        return list_neural_audio_jobs(project_id)
    except NeuralAudioError as exc:
        raise _map_error(exc) from exc


@router.get("/renders/{render_id}", response_model=NeuralAudioJobResponse)
async def get_render(render_id: str) -> NeuralAudioJobResponse:
    logger.info("GET /neural-audio/renders/{id}", extra={"render_id": render_id})
    try:
        return get_neural_audio_job(render_id)
    except NeuralAudioError as exc:
        raise _map_error(exc) from exc


@router.get("/renders/{render_id}/audio")
async def download_render_audio(render_id: str) -> FileResponse:
    logger.info("GET /neural-audio/renders/{id}/audio", extra={"render_id": render_id})
    try:
        path, content_type, job = resolve_audio_file_path(render_id)
    except NeuralAudioError as exc:
        raise _map_error(exc) from exc
    filename = f"neural-audio-{render_id}.wav"
    if job.audio_relpath and job.audio_relpath.endswith(".flac"):
        filename = f"neural-audio-{render_id}.flac"
    return FileResponse(
        path,
        media_type=content_type,
        filename=filename,
    )


@router.delete("/renders/{render_id}", status_code=204)
async def delete_render(render_id: str) -> None:
    logger.info("DELETE /neural-audio/renders/{id}", extra={"render_id": render_id})
    try:
        delete_neural_audio_job(render_id)
    except NeuralAudioError as exc:
        raise _map_error(exc) from exc


@router.post("/stem-sets", response_model=NeuralAudioStemSetResponse)
async def enqueue_stem_set_route(
    body: NeuralAudioStemEnqueueRequest,
) -> NeuralAudioStemSetResponse:
    started = time.perf_counter()
    logger.info(
        "POST /neural-audio/stem-sets",
        extra={
            "has_project_id": bool(body.project_id),
            "has_revision": bool(body.source_revision_id),
            "has_composition": bool(body.composition),
            "model_id": body.model_id,
            "engine": body.engine,
            "explicit_stems": len(body.stems or []),
            "has_bar_range": body.bar_range is not None,
        },
    )
    try:
        result = enqueue_stem_set(body, run_inline=True)
    except NeuralAudioError as exc:
        raise _map_error(exc) from exc
    duration_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "POST /neural-audio/stem-sets done",
        extra={
            "stem_set_id": result.id,
            "status": result.status,
            "stem_count": len(result.stems),
            "duration_ms": duration_ms,
            "model_id": result.model_id,
            "engine": result.engine,
        },
    )
    return result


@router.get("/stem-sets", response_model=NeuralAudioStemSetListResponse)
async def list_stem_sets_route(
    project_id: str = Query(..., min_length=1, max_length=64),
) -> NeuralAudioStemSetListResponse:
    logger.info("GET /neural-audio/stem-sets", extra={"project_id": project_id})
    try:
        return list_stem_sets(project_id)
    except NeuralAudioError as exc:
        raise _map_error(exc) from exc


@router.get("/stem-sets/{stem_set_id}", response_model=NeuralAudioStemSetResponse)
async def get_stem_set_route(stem_set_id: str) -> NeuralAudioStemSetResponse:
    logger.info("GET /neural-audio/stem-sets/{id}", extra={"stem_set_id": stem_set_id})
    try:
        return get_stem_set(stem_set_id)
    except NeuralAudioError as exc:
        raise _map_error(exc) from exc


@router.post(
    "/stem-sets/{stem_set_id}/stems/{stem_id}/rerender",
    response_model=NeuralAudioStemSetResponse,
)
async def rerender_stem_route(
    stem_set_id: str,
    stem_id: str,
    body: NeuralAudioStemRerenderRequest,
) -> NeuralAudioStemSetResponse:
    started = time.perf_counter()
    logger.info(
        "POST /neural-audio/stem-sets/{id}/stems/{stem_id}/rerender",
        extra={
            "stem_set_id": stem_set_id,
            "stem_id": stem_id,
            "has_composition": bool(body.composition),
            "engine": body.engine,
            "has_bar_range": body.bar_range is not None,
        },
    )
    try:
        result = rerender_stem(stem_set_id, stem_id, body, run_inline=True)
    except NeuralAudioError as exc:
        raise _map_error(exc) from exc
    duration_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "Stem rerender done",
        extra={
            "stem_set_id": result.id,
            "status": result.status,
            "stem_count": len(result.stems),
            "duration_ms": duration_ms,
        },
    )
    return result


@router.get("/stems/{stem_id}/audio")
async def download_stem_audio(stem_id: str) -> FileResponse:
    logger.info("GET /neural-audio/stems/{id}/audio", extra={"stem_id": stem_id})
    try:
        path, content_type, stem = resolve_stem_audio_file_path(stem_id)
    except NeuralAudioError as exc:
        raise _map_error(exc) from exc
    role = stem.stem_role or "stem"
    filename = f"{role}-{stem_id}.wav"
    if stem.audio_relpath and stem.audio_relpath.endswith(".flac"):
        filename = f"{role}-{stem_id}.flac"
    return FileResponse(
        path,
        media_type=content_type,
        filename=filename,
    )


@router.delete("/stem-sets/{stem_set_id}", status_code=204)
async def delete_stem_set_route(stem_set_id: str) -> None:
    logger.info("DELETE /neural-audio/stem-sets/{id}", extra={"stem_set_id": stem_set_id})
    try:
        delete_stem_set_job(stem_set_id)
    except NeuralAudioError as exc:
        raise _map_error(exc) from exc
