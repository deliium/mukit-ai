"""Motif transformation HTTP routes."""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from app.llm_settings import load_llm_settings
from app.motif_schemas import (
    MotifApplyError,
    MotifApplyRequest,
    MotifApplyResponse,
    validate_motif_apply_request_limits,
)
from app.services.composition_motif_editor import apply_motif_operation
from app.services.composition_projection import projection_issues_as_warnings
from app.services.llm_music_generator import (
    InvalidLLMOutputError,
    LLMGenerationError,
    NoLLMProviderConfiguredError,
    UnsupportedLLMProviderError,
)
from app.services.music_json_renderer import MusicJsonRenderError, render_musicxml


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/motifs", tags=["motifs"])


def _safe_export_filename(composition, extension: str) -> str:
    raw = f"composition-{composition.key}-{composition.tempo}bpm".lower()
    safe = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in raw)
    while "--" in safe:
        safe = safe.replace("--", "-")
    return f"{safe.strip('-') or 'composition'}.{extension}"


def _map_motif_error(exc: MotifApplyError) -> HTTPException:
    detail: dict[str, Any] = {"code": exc.code, "message": exc.message}
    if exc.details:
        safe = {
            key: value
            for key, value in exc.details.items()
            if isinstance(value, (str, int, float, bool)) or value is None
        }
        if safe:
            detail["details"] = safe
    return HTTPException(status_code=exc.http_status, detail=detail)


@router.post("/apply", response_model=MotifApplyResponse)
async def apply_motif_route(request: MotifApplyRequest) -> MotifApplyResponse:
    """Apply a motif transformation to a destination section/track."""
    started = time.perf_counter()
    event_count = sum(len(track.events) for track in request.composition.tracks)
    logger.info(
        "Motif apply request started",
        extra={
            "motif_id": request.source.motif_id,
            "source_occurrence_id": request.source.occurrence_id,
            "destination_track_id": request.destination.track_id,
            "destination_start_bar": request.destination.start_bar,
            "operation": request.operation,
            "provider": request.selection.provider,
            "model": request.selection.model,
            "event_count": event_count,
        },
    )
    logger.debug(
        "Motif apply request shape",
        extra={
            "motif_count": len(request.composition.motifs),
            "track_count": len(request.composition.tracks),
            "destination_section_id": request.destination.section_id,
            "destination_start_tick": request.destination.start_tick,
            "variation_strength": request.variation_strength,
        },
    )

    try:
        validate_motif_apply_request_limits(request)
        settings = load_llm_settings()
        outcome = await apply_motif_operation(request, settings)
        musicxml, render_report = render_musicxml(outcome.composition)
        all_warnings = [*outcome.warnings, *projection_issues_as_warnings(render_report)]
        elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
        logger.info(
            "Motif apply request completed",
            extra={
                "motif_id": outcome.result.motif_id,
                "source_occurrence_id": outcome.result.source_occurrence_id,
                "new_occurrence_id": outcome.result.new_occurrence_id,
                "operation": outcome.result.relationship,
                "provider": outcome.result.provider,
                "model": outcome.result.model,
                "created_event_count": len(outcome.result.created_event_ids),
                "identity_score": outcome.result.identity_score,
                "warning_count": len(all_warnings),
                "elapsed_ms": elapsed_ms,
            },
        )
        return MotifApplyResponse(
            composition=outcome.composition,
            result=outcome.result,
            warnings=all_warnings,
            musicxml=musicxml,
            musicxml_filename=_safe_export_filename(outcome.composition, "musicxml"),
        )
    except ValidationError as exc:
        logger.warning(
            "Motif apply request validation failed",
            extra={"error_count": exc.error_count()},
        )
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    except MotifApplyError as exc:
        logger.warning(
            "Motif apply request rejected",
            extra={"error_code": exc.code, "http_status": exc.http_status},
        )
        raise _map_motif_error(exc) from exc
    except NoLLMProviderConfiguredError as exc:
        logger.warning(
            "Motif apply provider unavailable",
            extra={"reason": "no_configured_providers", "operation": request.operation},
        )
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except UnsupportedLLMProviderError as exc:
        logger.warning(
            "Motif apply provider unavailable",
            extra={"reason": "unsupported_provider", "operation": request.operation},
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except InvalidLLMOutputError as exc:
        logger.error(
            "Motif apply LLM output invalid",
            extra={"error_type": type(exc).__name__, "operation": request.operation},
        )
        raise HTTPException(
            status_code=502,
            detail={"code": "motif_identity_failed", "message": str(exc)[:400]},
        ) from exc
    except LLMGenerationError as exc:
        logger.error(
            "Motif apply LLM failure",
            extra={"error_type": type(exc).__name__, "operation": request.operation},
        )
        raise HTTPException(status_code=502, detail=str(exc)[:500]) from exc
    except MusicJsonRenderError as exc:
        logger.error(
            "MusicXML rendering failed after motif apply",
            extra={"error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=500,
            detail="Applied composition could not be rendered as MusicXML",
        ) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Motif apply unexpected failure",
            extra={"error_type": type(exc).__name__, "operation": request.operation},
        )
        raise HTTPException(
            status_code=500,
            detail={"code": "motif_internal_error", "message": "Unexpected motif apply failure"},
        ) from exc
