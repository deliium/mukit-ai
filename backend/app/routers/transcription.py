"""Multipart monophonic audio transcription HTTP routes.

Returns transcription.preview.v1 only — never mutates or returns composition.
"""

from __future__ import annotations

import logging
import time
from pathlib import PurePosixPath

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.audio_transcription_schemas import (
    AudioTranscriptionError,
    AudioTranscriptionResponse,
)
from app.audio_transcription_settings import load_audio_transcription_settings
from app.audio_upload import UploadTooLargeError, read_upload_bounded
from app.services.audio_transcription import transcribe_audio_bytes


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/transcription", tags=["transcription"])


async def _read_upload_bounded(upload: UploadFile, *, max_bytes: int) -> bytes:
    try:
        return await read_upload_bounded(upload, max_bytes=max_bytes)
    except UploadTooLargeError as exc:
        raise AudioTranscriptionError(
            "audio_payload_too_large",
            "Upload exceeds configured audio byte limit",
            http_status=413,
            details={"limit_bytes": exc.limit_bytes},
        ) from exc


def _sanitize_display_filename(filename: str | None, *, fallback: str) -> str:
    raw = (filename or fallback).replace("\\", "/")
    name = PurePosixPath(raw).name.strip() or fallback
    return name[:120]


def _map_audio_error(exc: AudioTranscriptionError) -> HTTPException:
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


@router.post("/audio", response_model=AudioTranscriptionResponse)
async def transcribe_audio(
    file: UploadFile = File(...),
    tempo_bpm: int | None = Form(default=None),
    ticks_per_quarter: int | None = Form(default=None),
    origin_tick: int = Form(default=0),
) -> AudioTranscriptionResponse:
    settings = load_audio_transcription_settings()
    started = time.perf_counter()
    display_name = _sanitize_display_filename(file.filename, fallback="upload.wav")
    logger.info(
        "Audio transcription request started",
        extra={
            "endpoint": "transcription/audio",
            "configured_limit_bytes": settings.max_upload_bytes,
            "engine_setting": settings.engine,
            "fake_mode": settings.fake_mode,
            "has_tempo_bpm": tempo_bpm is not None,
            "has_ticks_per_quarter": ticks_per_quarter is not None,
        },
    )
    try:
        data = await _read_upload_bounded(file, max_bytes=settings.max_upload_bytes)
        response = transcribe_audio_bytes(
            data,
            display_filename=display_name,
            settings=settings,
            tempo_bpm=tempo_bpm,
            ticks_per_quarter=ticks_per_quarter,
            origin_tick=origin_tick or 0,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        logger.info(
            "Audio transcription request completed",
            extra={
                "endpoint": "transcription/audio",
                "duration_ms": round(elapsed_ms, 3),
                "upload_bytes": len(data),
                "engine_id": response.engine.id,
                "note_count": response.preview.summary.note_count,
                "low_confidence_count": response.preview.summary.low_confidence_count,
                "retention_deleted": response.retention.deleted,
            },
        )
        return response
    except AudioTranscriptionError as exc:
        logger.warning(
            "Audio transcription rejected",
            extra={
                "endpoint": "transcription/audio",
                "error_code": exc.code,
                "http_status": exc.http_status,
            },
        )
        raise _map_audio_error(exc) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Audio transcription unexpected failure",
            extra={
                "endpoint": "transcription/audio",
                "error_type": type(exc).__name__,
            },
        )
        raise HTTPException(
            status_code=500,
            detail={
                "code": "audio_internal_error",
                "message": "Unexpected audio transcription failure",
            },
        ) from exc
