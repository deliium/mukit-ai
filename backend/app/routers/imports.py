"""Multipart MIDI / MusicXML import HTTP routes."""

from __future__ import annotations

import logging
import time
from pathlib import PurePosixPath

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.import_schemas import CompositionImportError, CompositionImportResponse
from app.import_settings import load_import_settings
from app.services.composition_midi_import import import_midi_bytes
from app.services.composition_musicxml_import import import_musicxml_bytes
from app.services.music_json_renderer import MusicJsonRenderError, render_musicxml


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/imports", tags=["imports"])

_CHUNK_SIZE = 64 * 1024


async def _read_upload_bounded(upload: UploadFile, *, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    try:
        while True:
            chunk = await upload.read(_CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise CompositionImportError(
                    "import_payload_too_large",
                    "Upload exceeds configured byte limit",
                    http_status=413,
                    details={"limit_bytes": max_bytes},
                )
            chunks.append(chunk)
    finally:
        await upload.close()
    return b"".join(chunks)


def _sanitize_display_filename(filename: str | None, *, fallback: str) -> str:
    raw = (filename or fallback).replace("\\", "/")
    name = PurePosixPath(raw).name.strip() or fallback
    return name[:120]


def _map_import_error(exc: CompositionImportError) -> HTTPException:
    detail = {"code": exc.code, "message": exc.message}
    if exc.details:
        # Keep details bounded/scalar only.
        safe = {
            key: value
            for key, value in exc.details.items()
            if isinstance(value, (str, int, float, bool)) or value is None
        }
        if safe:
            detail["details"] = safe
    return HTTPException(status_code=exc.http_status, detail=detail)


def _notation_summary(report) -> dict:
    return {
        "status": report.status,
        "issue_codes": [issue.code for issue in report.issues],
        "approximated_count": report.approximated_count,
        "omitted_count": report.omitted_count,
        "exact_count": report.exact_count,
    }


@router.post("/midi", response_model=CompositionImportResponse)
async def import_midi(file: UploadFile = File(...)) -> CompositionImportResponse:
    settings = load_import_settings()
    started = time.perf_counter()
    display_name = _sanitize_display_filename(file.filename, fallback="upload.mid")
    logger.info(
        "MIDI import request started",
        extra={"endpoint": "midi", "configured_limit_bytes": settings.max_upload_bytes},
    )
    try:
        data = await _read_upload_bounded(file, max_bytes=settings.max_upload_bytes)
        if not data:
            raise CompositionImportError(
                "import_malformed_source",
                "MIDI upload is empty",
                http_status=422,
            )
        # Content signature must match the selected endpoint.
        if not data.startswith(b"MThd"):
            raise CompositionImportError(
                "import_unsupported_media_type",
                "Content signature does not match MIDI import endpoint",
                http_status=415,
            )
        imported = import_midi_bytes(data, display_filename=display_name, settings=settings)
        try:
            musicxml, notation_report = render_musicxml(imported.composition)
        except MusicJsonRenderError as exc:
            logger.error(
                "Notation render failed after MIDI import",
                extra={"error_type": type(exc).__name__},
            )
            raise HTTPException(
                status_code=500,
                detail={"code": "import_internal_error", "message": "Failed to render imported notation"},
            ) from exc
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        logger.info(
            "MIDI import request completed",
            extra={
                "endpoint": "midi",
                "input_bytes": len(data),
                "track_count": len(imported.composition.tracks),
                "note_count": sum(len(track.events) for track in imported.composition.tracks),
                "status": imported.import_report.status,
                "issue_codes": [issue.code for issue in imported.import_report.issues],
                "elapsed_ms": round(elapsed_ms, 3),
            },
        )
        return CompositionImportResponse(
            composition=imported.composition,
            musicxml=musicxml,
            import_report=imported.import_report,
            notation_report=_notation_summary(notation_report),
        )
    except CompositionImportError as exc:
        logger.warning(
            "MIDI import rejected",
            extra={"endpoint": "midi", "error_code": exc.code, "http_status": exc.http_status},
        )
        raise _map_import_error(exc) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "MIDI import unexpected failure",
            extra={"endpoint": "midi", "error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=500,
            detail={"code": "import_internal_error", "message": "Unexpected MIDI import failure"},
        ) from exc


@router.post("/musicxml", response_model=CompositionImportResponse)
async def import_musicxml(file: UploadFile = File(...)) -> CompositionImportResponse:
    settings = load_import_settings()
    started = time.perf_counter()
    display_name = _sanitize_display_filename(file.filename, fallback="upload.musicxml")
    logger.info(
        "MusicXML import request started",
        extra={"endpoint": "musicxml", "configured_limit_bytes": settings.max_upload_bytes},
    )
    try:
        data = await _read_upload_bounded(file, max_bytes=settings.max_upload_bytes)
        if not data:
            raise CompositionImportError(
                "import_malformed_source",
                "MusicXML upload is empty",
                http_status=422,
            )
        # Reject MIDI signatures on the MusicXML endpoint.
        if data.startswith(b"MThd"):
            raise CompositionImportError(
                "import_unsupported_media_type",
                "Content signature does not match MusicXML import endpoint",
                http_status=415,
            )
        imported = import_musicxml_bytes(data, display_filename=display_name, settings=settings)
        try:
            musicxml, notation_report = render_musicxml(imported.composition)
        except MusicJsonRenderError as exc:
            logger.error(
                "Notation render failed after MusicXML import",
                extra={"error_type": type(exc).__name__},
            )
            raise HTTPException(
                status_code=500,
                detail={"code": "import_internal_error", "message": "Failed to render imported notation"},
            ) from exc
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        logger.info(
            "MusicXML import request completed",
            extra={
                "endpoint": "musicxml",
                "input_bytes": len(data),
                "track_count": len(imported.composition.tracks),
                "note_count": sum(len(track.events) for track in imported.composition.tracks),
                "status": imported.import_report.status,
                "issue_codes": [issue.code for issue in imported.import_report.issues],
                "elapsed_ms": round(elapsed_ms, 3),
            },
        )
        return CompositionImportResponse(
            composition=imported.composition,
            musicxml=musicxml,
            import_report=imported.import_report,
            notation_report=_notation_summary(notation_report),
        )
    except CompositionImportError as exc:
        logger.warning(
            "MusicXML import rejected",
            extra={"endpoint": "musicxml", "error_code": exc.code, "http_status": exc.http_status},
        )
        raise _map_import_error(exc) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "MusicXML import unexpected failure",
            extra={"endpoint": "musicxml", "error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=500,
            detail={"code": "import_internal_error", "message": "Unexpected MusicXML import failure"},
        ) from exc
