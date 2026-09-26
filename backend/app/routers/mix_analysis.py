"""HTTP routes for ``mix.analysis.v1`` (session or optional durable reports)."""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.db.connection import get_connection, get_project_db_path
from app.mix_analysis_schemas import (
    MixAnalysisAnalyzeRequest,
    MixAnalysisAnalyzeResponse,
    MixAnalysisError,
    MixAnalysisReportGetResponse,
    MixAnalysisReportListResponse,
    map_mix_analysis_error_to_http,
)
from app.services.mix_analysis.pipeline import analyze_mix
from app.services.mix_analysis_store import (
    delete_report,
    get_report_row,
    list_project_reports,
    load_report_body,
    load_settings_and_root,
    row_to_meta,
)
from app.routers.collaboration_guard import enforce_current

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mix-analysis", tags=["mix-analysis"])


@router.post("/analyze", response_model=MixAnalysisAnalyzeResponse)
async def analyze_mix_route(request: MixAnalysisAnalyzeRequest) -> MixAnalysisAnalyzeResponse:
    started = time.perf_counter()
    logger.info(
        "POST /mix-analysis/analyze",
        extra={
            "stem_set_id": request.stem_set_id,
            "project_id": request.project_id,
            "persist": request.persist,
            "include_ai": request.include_ai_interpretation,
        },
    )
    enforce_current(request.project_id, "write_audio" if request.persist else "read")
    try:
        report, persisted = analyze_mix(request)
    except MixAnalysisError as exc:
        status, detail = map_mix_analysis_error_to_http(exc)
        logger.warning(
            "Mix analysis failed",
            extra={"code": exc.code, "http_status": status, "stem_set_id": request.stem_set_id},
        )
        raise HTTPException(status_code=status, detail=detail) from exc
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Mix analysis internal error",
            extra={"error_type": type(exc).__name__, "stem_set_id": request.stem_set_id},
        )
        raise HTTPException(
            status_code=500,
            detail={
                "code": "mix_analysis_internal_error",
                "message": "Unexpected mix analysis failure",
            },
        ) from exc

    duration_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "POST /mix-analysis/analyze ok",
        extra={
            "stem_set_id": request.stem_set_id,
            "report_id": report.report_id,
            "dsp_backend": report.dsp_backend,
            "measurement_count": len(report.measurements),
            "observation_count": len(report.observations),
            "http_status": 200,
            "duration_ms": duration_ms,
        },
    )
    return MixAnalysisAnalyzeResponse(report=report, persisted=persisted)


@router.get("/reports/{report_id}", response_model=MixAnalysisReportGetResponse)
async def get_mix_analysis_report(report_id: str) -> MixAnalysisReportGetResponse:
    logger.info("GET /mix-analysis/reports/{id}", extra={"report_id": report_id})
    settings = load_settings_and_root()
    try:
        with get_connection(get_project_db_path()) as conn:
            row = get_report_row(conn, report_id)
            if row is None:
                raise MixAnalysisError(
                    "Mix analysis report not found",
                    code="mix_analysis_not_found",
                    http_status=404,
                )
            meta = row_to_meta(row)
            report = load_report_body(settings, row)
    except MixAnalysisError as exc:
        status, detail = map_mix_analysis_error_to_http(exc)
        raise HTTPException(status_code=status, detail=detail) from exc
    enforce_current(meta.project_id, "read")
    return MixAnalysisReportGetResponse(meta=meta, report=report)


@router.get("/reports", response_model=MixAnalysisReportListResponse)
async def list_mix_analysis_reports(
    project_id: str = Query(..., min_length=1),
    limit: int = Query(50, ge=1, le=200),
) -> MixAnalysisReportListResponse:
    logger.info(
        "GET /mix-analysis/reports",
        extra={"project_id": project_id, "limit": limit},
    )
    enforce_current(project_id, "read")
    with get_connection(get_project_db_path()) as conn:
        items = list_project_reports(conn, project_id, limit=limit)
    return MixAnalysisReportListResponse(items=items)


@router.delete("/reports/{report_id}")
async def delete_mix_analysis_report(report_id: str) -> dict[str, Any]:
    logger.info("DELETE /mix-analysis/reports/{id}", extra={"report_id": report_id})
    settings = load_settings_and_root()
    try:
        with get_connection(get_project_db_path()) as conn:
            row = get_report_row(conn, report_id)
            if row is None:
                raise MixAnalysisError(
                    "Mix analysis report not found",
                    code="mix_analysis_not_found",
                    http_status=404,
                )
            enforce_current(row_to_meta(row).project_id, "write_audio")
            delete_report(conn, settings, report_id)
    except MixAnalysisError as exc:
        status, detail = map_mix_analysis_error_to_http(exc)
        raise HTTPException(status_code=status, detail=detail) from exc
    return {"ok": True, "report_id": report_id, "deleted": True}
