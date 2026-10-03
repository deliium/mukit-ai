"""HTTP CRUD + realize/compare for project performance plans."""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException

from app.performance_schemas import (
    PerformanceCompareRequest,
    PerformanceCompareResponse,
    PerformancePlanCreateRequest,
    PerformancePlanError,
    PerformancePlanGetResponse,
    PerformancePlanListResponse,
    PerformancePlanUpdateRequest,
    PerformancePresetCatalogResponse,
    PerformanceRealizeRequest,
    PerformanceRealizeResponse,
    map_performance_error_to_http,
)
from app.routers.collaboration_guard import enforce_current
from app.services.performance_plan_service import (
    compare_performance_plan,
    create_performance_plan,
    delete_performance_plan,
    get_performance_plan,
    list_performance_plans,
    realize_performance_plan,
    update_performance_plan,
)
from app.services.performance_presets import list_preset_catalog

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/projects/{project_id}/performance-plans",
    tags=["performance-plans"],
)


def _raise(exc: PerformancePlanError) -> None:
    status, detail = map_performance_error_to_http(exc)
    raise HTTPException(status_code=status, detail=detail) from exc


@router.get("/presets", response_model=PerformancePresetCatalogResponse)
async def get_performance_presets(project_id: str) -> PerformancePresetCatalogResponse:
    started = time.perf_counter()
    enforce_current(project_id, "read")
    presets = list_preset_catalog()
    logger.debug(
        "GET performance preset catalog",
        extra={
            "project_id": project_id,
            "preset_count": len(presets),
            "duration_ms": int((time.perf_counter() - started) * 1000),
        },
    )
    return PerformancePresetCatalogResponse(presets=presets)


@router.get("", response_model=PerformancePlanListResponse)
async def list_project_performance_plans(project_id: str) -> PerformancePlanListResponse:
    started = time.perf_counter()
    enforce_current(project_id, "read")
    try:
        body = list_performance_plans(project_id)
    except PerformancePlanError as exc:
        _raise(exc)
    logger.info(
        "GET performance plans",
        extra={
            "project_id": project_id,
            "plan_count": len(body.plans),
            "http_status": 200,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        },
    )
    return body


@router.post("", response_model=PerformancePlanGetResponse, status_code=201)
async def create_project_performance_plan(
    project_id: str,
    request: PerformancePlanCreateRequest,
) -> PerformancePlanGetResponse:
    started = time.perf_counter()
    enforce_current(project_id, "write_score")
    try:
        body = create_performance_plan(project_id, request)
    except PerformancePlanError as exc:
        _raise(exc)
    logger.info(
        "POST performance plan",
        extra={
            "project_id": project_id,
            "plan_id": body.plan.id,
            "preset_id": body.plan.preset_id,
            "http_status": 201,
            "document_revision": body.document_revision,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        },
    )
    return body


@router.get("/{plan_id}", response_model=PerformancePlanGetResponse)
async def get_project_performance_plan(
    project_id: str,
    plan_id: str,
) -> PerformancePlanGetResponse:
    started = time.perf_counter()
    enforce_current(project_id, "read")
    try:
        body = get_performance_plan(project_id, plan_id)
    except PerformancePlanError as exc:
        _raise(exc)
    logger.info(
        "GET performance plan",
        extra={
            "project_id": project_id,
            "plan_id": plan_id,
            "http_status": 200,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        },
    )
    return body


@router.put("/{plan_id}", response_model=PerformancePlanGetResponse)
async def put_project_performance_plan(
    project_id: str,
    plan_id: str,
    request: PerformancePlanUpdateRequest,
) -> PerformancePlanGetResponse:
    started = time.perf_counter()
    enforce_current(project_id, "write_score")
    try:
        body = update_performance_plan(project_id, plan_id, request)
    except PerformancePlanError as exc:
        _raise(exc)
    logger.info(
        "PUT performance plan",
        extra={
            "project_id": project_id,
            "plan_id": plan_id,
            "document_revision": body.document_revision,
            "http_status": 200,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        },
    )
    return body


@router.delete("/{plan_id}", status_code=204)
async def delete_project_performance_plan(project_id: str, plan_id: str) -> None:
    started = time.perf_counter()
    enforce_current(project_id, "write_score")
    try:
        delete_performance_plan(project_id, plan_id)
    except PerformancePlanError as exc:
        _raise(exc)
    logger.info(
        "DELETE performance plan",
        extra={
            "project_id": project_id,
            "plan_id": plan_id,
            "http_status": 204,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        },
    )


@router.post("/{plan_id}/realize", response_model=PerformanceRealizeResponse)
async def realize_project_performance_plan(
    project_id: str,
    plan_id: str,
    request: PerformanceRealizeRequest,
) -> PerformanceRealizeResponse:
    started = time.perf_counter()
    enforce_current(project_id, "read")
    try:
        body = realize_performance_plan(project_id, plan_id, request.composition)
    except PerformancePlanError as exc:
        _raise(exc)
    logger.info(
        "POST performance realize",
        extra={
            "project_id": project_id,
            "plan_id": plan_id,
            "stale": body.stale,
            "note_count": body.realization.metrics.note_count,
            "mean_abs_tick_delta": round(body.realization.metrics.mean_abs_tick_delta, 4),
            "http_status": 200,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        },
    )
    return body


@router.post("/{plan_id}/compare", response_model=PerformanceCompareResponse)
async def compare_project_performance_plan(
    project_id: str,
    plan_id: str,
    request: PerformanceCompareRequest,
) -> PerformanceCompareResponse:
    started = time.perf_counter()
    enforce_current(project_id, "read")
    try:
        body = compare_performance_plan(project_id, plan_id, request.composition)
    except PerformancePlanError as exc:
        _raise(exc)
    logger.info(
        "POST performance compare",
        extra={
            "project_id": project_id,
            "plan_id": plan_id,
            "stale": body.stale,
            "mean_abs_tick_delta": round(body.performed_metrics.mean_abs_tick_delta, 4),
            "http_status": 200,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        },
    )
    return body
