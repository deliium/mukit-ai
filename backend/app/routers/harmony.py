"""Harmony timeline and reharmonization HTTP routes."""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from app.harmony_schemas import (
    ReharmonizeError,
    ReharmonizePreviewRequest,
    ReharmonizePreviewResponse,
)
from app.llm_settings import load_llm_settings
from app.services.llm_music_generator import (
    InvalidLLMOutputError,
    LLMGenerationError,
    NoLLMProviderConfiguredError,
    UnsupportedLLMProviderError,
)
from app.services.llm_reharmonizer import run_reharmonize_preview


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/harmony", tags=["harmony"])


def _map_reharmonize_error(exc: ReharmonizeError) -> HTTPException:
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


@router.post("/reharmonize/preview", response_model=ReharmonizePreviewResponse)
async def reharmonize_preview_route(request: ReharmonizePreviewRequest) -> ReharmonizePreviewResponse:
    """Stateless reharmonization preview — never writes a project."""
    started = time.perf_counter()
    event_count = sum(len(track.events) for track in request.composition.tracks)
    logger.info(
        "Harmony reharmonize preview request started",
        extra={
            "operation": request.operation,
            "engine": request.engine,
            "content_policy": request.content_policy,
            "start_bar": request.selection.start_bar,
            "end_bar": request.selection.end_bar,
            "target_count": len(request.target_track_ids),
            "provider": request.selection_options.provider,
            "model": request.selection_options.model,
            "event_count": event_count,
            "instruction_len": len(request.instruction or ""),
        },
    )
    try:
        settings = load_llm_settings()
        response = await run_reharmonize_preview(request, settings=settings)
    except ReharmonizeError as exc:
        logger.warning(
            "Harmony reharmonize preview domain failure",
            extra={"code": exc.code, "http_status": exc.http_status},
        )
        raise _map_reharmonize_error(exc) from exc
    except NoLLMProviderConfiguredError as exc:
        logger.warning("Harmony reharmonize preview missing provider")
        raise HTTPException(
            status_code=503,
            detail={"code": "llm_provider_unavailable", "message": str(exc)},
        ) from exc
    except UnsupportedLLMProviderError as exc:
        logger.warning("Harmony reharmonize preview unsupported provider")
        raise HTTPException(
            status_code=503,
            detail={"code": "llm_provider_unsupported", "message": str(exc)},
        ) from exc
    except InvalidLLMOutputError as exc:
        logger.warning(
            "Harmony reharmonize preview invalid provider output",
            extra={"error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=502,
            detail={"code": "llm_invalid_output", "message": "Provider returned invalid reharmonization output."},
        ) from exc
    except LLMGenerationError as exc:
        logger.error(
            "Harmony reharmonize preview LLM failure",
            extra={"error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=502,
            detail={"code": "llm_generation_failed", "message": "Reharmonization provider failed."},
        ) from exc
    except ValidationError as exc:
        logger.warning(
            "Harmony reharmonize preview validation failure",
            extra={"error_count": exc.error_count() if hasattr(exc, "error_count") else None},
        )
        raise HTTPException(
            status_code=422,
            detail={"code": "reharmonize_invalid_request", "message": "Invalid reharmonization request."},
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Harmony reharmonize preview unexpected failure",
            extra={"error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=500,
            detail={"code": "reharmonize_internal_error", "message": "Unexpected reharmonization failure."},
        ) from exc

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "Harmony reharmonize preview request completed",
        extra={
            "operation": request.operation,
            "engine": request.engine,
            "provider": response.provider,
            "compatibility_status": response.compatibility.status,
            "changed_span_count": len(response.harmony_changes),
            "changed_track_count": sum(1 for item in response.track_changes if item.events_changed > 0),
            "elapsed_ms": elapsed_ms,
        },
    )
    logger.debug(
        "Harmony reharmonize preview response shape",
        extra={
            "finding_count": len(response.compatibility.findings),
            "preservation_count": len(response.preservation),
            "warning_count": len(response.warnings),
        },
    )
    return response
