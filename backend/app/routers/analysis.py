"""HTTP routes for deterministic composition.analysis.v1."""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.analysis_schemas import (
    ANALYSIS_ALGORITHM_VERSION,
    CompositionAnalysisError,
    CompositionAnalysisReport,
    CompositionAnalysisScope,
    CompositionAnalysisScopeComposition,
    fingerprint_log_prefix,
)
from app.services.composition_analysis import analyze_composition


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analysis", tags=["analysis"])


class CompositionAnalysisHttpRequest(BaseModel):
    """HTTP body: composition is validated by the analysis service for stable error codes.

    OpenAPI still documents the sidecar report response; composition must be full V2 JSON.
    Client-provided cached analysis is never accepted as authority.
    """

    model_config = ConfigDict(extra="forbid")

    composition: dict[str, Any]
    scope: CompositionAnalysisScope = Field(default_factory=CompositionAnalysisScopeComposition)


def _map_analysis_error(exc: CompositionAnalysisError) -> HTTPException:
    detail: dict[str, Any] = {"code": exc.code, "message": exc.message}
    if exc.details:
        safe = {
            key: value
            for key, value in exc.details.items()
            if isinstance(value, (str, int, float, bool)) or value is None
        }
        if safe:
            detail["details"] = safe
    status = exc.http_status
    if exc.code == "analysis_internal_error" and status == 422:
        status = 500
    return HTTPException(status_code=status, detail=detail)


@router.post("/composition", response_model=CompositionAnalysisReport)
async def analyze_composition_route(
    request: CompositionAnalysisHttpRequest,
) -> CompositionAnalysisReport:
    """Analyze the complete current V2 document for one scope.

    Musical warnings and insufficient evidence return HTTP 200 with report status.
    Invalid V2 / scope / limits map to structured 422 (or 500 for internal failures).
    """
    started = time.perf_counter()
    scope_kind = getattr(request.scope, "kind", "composition")
    composition_payload = request.composition
    track_count = (
        len(composition_payload.get("tracks") or [])
        if isinstance(composition_payload, dict)
        else 0
    )
    note_count = 0
    if isinstance(composition_payload, dict):
        for track in composition_payload.get("tracks") or []:
            if isinstance(track, dict):
                note_count += len(track.get("events") or [])
    bar_count = composition_payload.get("bar_count") if isinstance(composition_payload, dict) else None
    logger.info(
        "Composition analysis request started",
        extra={
            "algorithm_version": ANALYSIS_ALGORITHM_VERSION,
            "scope_kind": scope_kind,
            "track_count": track_count,
            "note_count": note_count,
            "bar_count": bar_count,
        },
    )
    try:
        report = analyze_composition(request.composition, request.scope)
        elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
        warning_codes = [item.code for item in report.warnings]
        logger.info(
            "Composition analysis request completed",
            extra={
                "algorithm_version": report.algorithm_version,
                "scope_kind": report.resolved_scope.kind,
                "status": report.status,
                "warning_count": len(report.warnings),
                "warning_codes": warning_codes[:32],
                "fingerprint_prefix": fingerprint_log_prefix(report.source_fingerprint),
                "elapsed_ms": elapsed_ms,
                "track_count": track_count,
                "note_count": note_count,
            },
        )
        return report
    except CompositionAnalysisError as exc:
        logger.warning(
            "Composition analysis request rejected",
            extra={
                "error_code": exc.code,
                "http_status": exc.http_status,
                "scope_kind": scope_kind,
                "detail_keys": sorted(exc.details.keys()),
            },
        )
        logger.error(
            "Composition analysis domain mapping",
            extra={"error_code": exc.code, "error_type": type(exc).__name__},
        )
        raise _map_analysis_error(exc) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Composition analysis unexpected failure",
            extra={"error_type": type(exc).__name__, "scope_kind": scope_kind},
        )
        raise HTTPException(
            status_code=500,
            detail={
                "code": "analysis_internal_error",
                "message": "Unexpected analysis failure",
            },
        ) from exc
