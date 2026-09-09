"""Composition arrangement catalog and preview HTTP routes."""

from __future__ import annotations

import logging
import time
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.arrangement_schemas import (
    ARRANGEMENT_CATALOG_VERSION,
    ARRANGEMENT_RANGE_POLICY_VERSION,
    CompositionArrangementError,
    CompositionArrangementPreviewRequest,
    CompositionArrangementPreviewResponse,
)
from app.llm_settings import load_llm_settings
from app.services.composition_edit_fingerprint import edit_fingerprint_log_prefix
from app.services.instrument_catalog import (
    InstrumentCatalogError,
    canonical_track_roles,
    get_catalog,
)
from app.services.llm_composition_arrangement import run_composition_arrangement_preview
from app.services.llm_music_generator import (
    InvalidLLMOutputError,
    LLMGenerationError,
    NoLLMProviderConfiguredError,
    UnsupportedLLMProviderError,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/composition/arrangement", tags=["composition-arrangement"])

_SAFE_DETAIL_LIST_MAX = 32


class ArrangementInstrumentProfileResponse(BaseModel):
    """Curated selectable instrument profile (not persisted on V2 tracks)."""

    model_config = ConfigDict(extra="forbid")

    instrument_id: str = Field(..., min_length=1, max_length=120)
    display_name: str = Field(..., min_length=1, max_length=120)
    aliases: list[str] = Field(default_factory=list, max_length=64)
    midi_program: int = Field(..., ge=0, le=127)
    gm_family: str = Field(..., min_length=1, max_length=64)
    compatibility_identity: str = Field(..., min_length=1, max_length=64)
    compatibility_family: str = Field(..., min_length=1, max_length=64)
    is_drum: bool
    range_policy: Literal["absolute", "unbounded", "unknown"]
    playable_low: int | None = Field(default=None, ge=0, le=127)
    playable_high: int | None = Field(default=None, ge=0, le=127)
    preferred_low: int | None = Field(default=None, ge=0, le=127)
    preferred_high: int | None = Field(default=None, ge=0, le=127)
    suggested_roles: list[str] = Field(default_factory=list, max_length=32)
    fingerprint: str = Field(..., min_length=16, max_length=128)


class ArrangementInstrumentCatalogResponse(BaseModel):
    """Versioned catalog plus complete canonical track-role vocabulary."""

    model_config = ConfigDict(extra="forbid")

    catalog_version: str = Field(default=ARRANGEMENT_CATALOG_VERSION, min_length=1, max_length=64)
    range_policy_version: str = Field(
        default=ARRANGEMENT_RANGE_POLICY_VERSION,
        min_length=1,
        max_length=64,
    )
    fingerprint: str = Field(..., min_length=16, max_length=128)
    source_path_category: Literal[
        "packaged",
        "override",
        "relative_rejected",
        "missing",
        "unreadable",
        "invalid",
    ]
    instruments: list[ArrangementInstrumentProfileResponse] = Field(default_factory=list)
    # Full SUPPORTED_TRACK_ROLES vocabulary — independent of suggested_roles.
    track_roles: list[str] = Field(default_factory=list, min_length=1)


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


def _map_arrangement_error(exc: CompositionArrangementError) -> HTTPException:
    detail: dict[str, Any] = {"code": exc.code, "message": exc.message}
    safe = _safe_details(exc.details)
    if safe:
        detail["details"] = safe
    return HTTPException(status_code=exc.http_status, detail=detail)


def _map_catalog_error(exc: InstrumentCatalogError) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "code": "arrangement_catalog_unavailable",
            "message": "Arrangement instrument catalog is unavailable or invalid.",
            "details": {
                "catalog_code": exc.code,
                "path_category": exc.path_category,
            },
        },
    )


@router.get("/instruments", response_model=ArrangementInstrumentCatalogResponse)
async def arrangement_instruments_route() -> ArrangementInstrumentCatalogResponse:
    """Return the curated instrument catalog and canonical track-role vocabulary."""
    started = time.perf_counter()
    logger.info(
        "Composition arrangement instruments request started",
        extra={"endpoint": "GET /composition/arrangement/instruments"},
    )
    try:
        catalog = get_catalog()
        roles = canonical_track_roles()
        response = ArrangementInstrumentCatalogResponse(
            catalog_version=catalog.catalog_version,
            range_policy_version=catalog.range_policy_version,
            fingerprint=catalog.fingerprint,
            source_path_category=catalog.source_path_category,
            instruments=[
                ArrangementInstrumentProfileResponse(
                    instrument_id=profile.instrument_id,
                    display_name=profile.display_name,
                    aliases=list(profile.aliases),
                    midi_program=profile.midi_program,
                    gm_family=profile.gm_family,
                    compatibility_identity=profile.compatibility_identity,
                    compatibility_family=profile.compatibility_family,
                    is_drum=profile.is_drum,
                    range_policy=profile.range_policy,
                    playable_low=profile.playable_low,
                    playable_high=profile.playable_high,
                    preferred_low=profile.preferred_low,
                    preferred_high=profile.preferred_high,
                    suggested_roles=list(profile.suggested_roles),
                    fingerprint=profile.fingerprint,
                )
                for profile in catalog.profiles
            ],
            track_roles=roles,
        )
    except InstrumentCatalogError as exc:
        logger.warning(
            "Composition arrangement instruments catalog failure",
            extra={
                "code": "arrangement_catalog_unavailable",
                "catalog_code": exc.code,
                "path_category": exc.path_category,
                "error_type": type(exc).__name__,
            },
        )
        raise _map_catalog_error(exc) from exc
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Composition arrangement instruments unexpected failure",
            extra={"error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=500,
            detail={
                "code": "arrangement_internal_error",
                "message": "Unexpected composition arrangement failure.",
            },
        ) from exc

    logger.info(
        "Composition arrangement instruments request completed",
        extra={
            "endpoint": "GET /composition/arrangement/instruments",
            "catalog_version": response.catalog_version,
            "range_policy_version": response.range_policy_version,
            "fingerprint_prefix": edit_fingerprint_log_prefix(response.fingerprint),
            "instrument_count": len(response.instruments),
            "track_role_count": len(response.track_roles),
            "source_path_category": response.source_path_category,
            "status": 200,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
        },
    )
    return response


@router.post("/preview", response_model=CompositionArrangementPreviewResponse)
async def composition_arrangement_preview_route(
    request: CompositionArrangementPreviewRequest,
) -> CompositionArrangementPreviewResponse:
    """Stateless multi-candidate arrangement preview — never writes a project."""
    started = time.perf_counter()
    logger.info(
        "Composition arrangement preview request started",
        extra={
            "endpoint": "POST /composition/arrangement/preview",
            "operation": request.operation,
            "provider": request.selection.provider,
            "model": request.selection.model,
            "source_track_count": len(request.source_track_ids),
            "protected_track_count": len(request.protected_track_ids),
            "before_part_count": request.instrumentation.before_part_count,
            "after_part_count": request.instrumentation.after_part_count,
            "requested_candidate_count": request.candidate_count,
            "instruction_len": len(request.instruction or ""),
        },
    )
    try:
        settings = load_llm_settings()
        response = await run_composition_arrangement_preview(request, settings=settings)
    except CompositionArrangementError as exc:
        logger.warning(
            "Composition arrangement preview domain failure",
            extra={
                "code": exc.code,
                "http_status": exc.http_status,
                "error_type": type(exc).__name__,
            },
        )
        raise _map_arrangement_error(exc) from exc
    except InstrumentCatalogError as exc:
        logger.warning(
            "Composition arrangement preview catalog failure",
            extra={
                "code": "arrangement_catalog_unavailable",
                "catalog_code": exc.code,
                "error_type": type(exc).__name__,
            },
        )
        raise _map_catalog_error(exc) from exc
    except NoLLMProviderConfiguredError as exc:
        logger.warning(
            "Composition arrangement preview missing provider",
            extra={"error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=503,
            detail={
                "code": "arrangement_provider_unavailable",
                "message": str(exc),
            },
        ) from exc
    except UnsupportedLLMProviderError as exc:
        logger.warning(
            "Composition arrangement preview unsupported provider",
            extra={"error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=503,
            detail={
                "code": "arrangement_provider_unavailable",
                "message": str(exc),
            },
        ) from exc
    except InvalidLLMOutputError as exc:
        logger.warning(
            "Composition arrangement preview invalid provider output",
            extra={"error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=502,
            detail={
                "code": "arrangement_candidate_exhausted",
                "message": "Provider returned invalid arrangement output.",
            },
        ) from exc
    except LLMGenerationError as exc:
        logger.error(
            "Composition arrangement preview provider failure",
            extra={"error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=502,
            detail={
                "code": "arrangement_provider_error",
                "message": "Composition arrangement provider failed.",
            },
        ) from exc
    except ValidationError as exc:
        logger.warning(
            "Composition arrangement preview validation failure",
            extra={
                "error_type": type(exc).__name__,
                "error_count": exc.error_count() if hasattr(exc, "error_count") else None,
            },
        )
        raise HTTPException(
            status_code=422,
            detail={
                "code": "arrangement_invalid_operation",
                "message": "Invalid composition arrangement request.",
            },
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Composition arrangement preview unexpected failure",
            extra={"error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=500,
            detail={
                "code": "arrangement_internal_error",
                "message": "Unexpected composition arrangement failure.",
            },
        ) from exc

    warning_count = len(response.warning_codes) + sum(
        len(candidate.warning_codes) for candidate in response.candidates
    )
    logger.info(
        "Composition arrangement preview request completed",
        extra={
            "endpoint": "POST /composition/arrangement/preview",
            "operation": response.operation,
            "provider": response.provider,
            "model": response.model,
            "requested_candidate_count": response.requested_candidate_count,
            "returned_candidate_count": len(response.candidates),
            "rejected_attempt_count": len(response.rejected_attempts),
            "catalog_version": response.catalog_version,
            "fingerprint_prefix": edit_fingerprint_log_prefix(response.catalog_fingerprint),
            "warning_count": warning_count,
            "status": 200,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
        },
    )
    return response
