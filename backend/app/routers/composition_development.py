"""Composition development preview HTTP routes."""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from app.composition_development_schemas import (
    CompositionDevelopmentError,
    CompositionDevelopmentPreviewRequest,
    CompositionDevelopmentPreviewResponse,
)
from app.llm_settings import load_llm_settings
from app.services.composition_edit_fingerprint import edit_fingerprint_log_prefix
from app.services.llm_composition_development import run_composition_development_preview
from app.services.llm_music_generator import (
    InvalidLLMOutputError,
    LLMGenerationError,
    NoLLMProviderConfiguredError,
    UnsupportedLLMProviderError,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/composition/development", tags=["composition-development"])

_SAFE_DETAIL_LIST_MAX = 32


def _safe_details(details: dict[str, Any] | None) -> dict[str, Any]:
    if not details:
        return {}
    safe: dict[str, Any] = {}
    for key, value in details.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe[key] = value
        elif isinstance(value, list):
            clipped = []
            for item in value[:_SAFE_DETAIL_LIST_MAX]:
                if isinstance(item, (str, int, float, bool)) or item is None:
                    clipped.append(item)
            if clipped:
                safe[key] = clipped
    return safe


def _map_development_error(exc: CompositionDevelopmentError) -> HTTPException:
    detail: dict[str, Any] = {"code": exc.code, "message": exc.message}
    safe = _safe_details(exc.details)
    if safe:
        detail["details"] = safe
    return HTTPException(status_code=exc.http_status, detail=detail)


@router.post("/preview", response_model=CompositionDevelopmentPreviewResponse)
async def composition_development_preview_route(
    request: CompositionDevelopmentPreviewRequest,
) -> CompositionDevelopmentPreviewResponse:
    """Stateless multi-candidate development preview — never writes a project."""
    started = time.perf_counter()
    logger.info(
        "Composition development preview request started",
        extra={
            "operation": request.operation,
            "intent": request.development_intent,
            "strength": request.variation_strength,
            "requested_candidate_count": request.candidate_count,
            "output_bars": request.output_bars,
            "provider": request.selection.provider,
            "model": request.selection.model,
            "allow_modulation": request.allow_modulation,
            "instruction_len": len(request.instruction or ""),
            "source_bar_count": request.composition.bar_count,
        },
    )
    try:
        settings = load_llm_settings()
        response = await run_composition_development_preview(request, settings=settings)
    except CompositionDevelopmentError as exc:
        logger.warning(
            "Composition development preview domain failure",
            extra={
                "code": exc.code,
                "http_status": exc.http_status,
                "error_type": type(exc).__name__,
            },
        )
        raise _map_development_error(exc) from exc
    except NoLLMProviderConfiguredError as exc:
        logger.warning(
            "Composition development preview missing provider",
            extra={"error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=503,
            detail={"code": "development_provider_unavailable", "message": str(exc)},
        ) from exc
    except UnsupportedLLMProviderError as exc:
        logger.warning(
            "Composition development preview unsupported provider",
            extra={"error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=503,
            detail={"code": "development_provider_unavailable", "message": str(exc)},
        ) from exc
    except InvalidLLMOutputError as exc:
        logger.warning(
            "Composition development preview invalid provider output",
            extra={"error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=502,
            detail={
                "code": "development_candidate_exhausted",
                "message": "Provider returned invalid development output.",
            },
        ) from exc
    except LLMGenerationError as exc:
        logger.error(
            "Composition development preview provider failure",
            extra={"error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=502,
            detail={
                "code": "development_provider_error",
                "message": "Composition development provider failed.",
            },
        ) from exc
    except ValidationError as exc:
        logger.warning(
            "Composition development preview validation failure",
            extra={
                "error_type": type(exc).__name__,
                "error_count": exc.error_count() if hasattr(exc, "error_count") else None,
            },
        )
        raise HTTPException(
            status_code=422,
            detail={
                "code": "development_invalid_operation",
                "message": "Invalid composition development request.",
            },
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Composition development preview unexpected failure",
            extra={"error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=500,
            detail={
                "code": "development_internal_error",
                "message": "Unexpected composition development failure.",
            },
        ) from exc

    logger.info(
        "Composition development preview request completed",
        extra={
            "operation": response.operation,
            "intent": response.development_intent,
            "strength": response.variation_strength,
            "requested_candidate_count": response.requested_candidate_count,
            "returned_candidate_count": len(response.candidates),
            "provider": response.provider,
            "model": response.model,
            "fingerprint_prefix": edit_fingerprint_log_prefix(response.edit_source_fingerprint),
            "warning_code_count": len(response.warning_codes),
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
        },
    )
    return response
