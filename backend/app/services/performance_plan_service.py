"""Orchestration for performance plans: CRUD, realize, compare, optional AI patch."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.composition_schemas import CompositionV2
from app.performance_schemas import (
    PERFORMANCE_ERROR_CODES,
    PerformanceCompareResponse,
    PerformancePlanCreateRequest,
    PerformancePlanError,
    PerformancePlanGetResponse,
    PerformancePlanListResponse,
    PerformancePlanUpdateRequest,
    PerformancePlanV1,
    PerformanceRealizeResponse,
    parse_performance_plan,
)
from app.performance_settings import load_performance_settings
from app.services.composition_snapshot_encoding import composition_snapshot_fingerprint
from app.services.performance_conductor import (
    mechanical_metrics,
    realize_performance,
    sample_deltas,
)
from app.services.performance_plan_store import (
    PerformancePlanRecord,
    create_plan,
    delete_plan,
    get_plan,
    is_stale_fingerprint,
    list_plans,
    replace_plan,
)
from app.services.performance_presets import clone_preset, list_preset_catalog

logger = logging.getLogger(__name__)


def _parse_composition(payload: dict[str, Any]) -> CompositionV2:
    try:
        return CompositionV2.model_validate(payload)
    except Exception as exc:
        raise PerformancePlanError(
            "composition_invalid",
            PERFORMANCE_ERROR_CODES["composition_invalid"],
            http_status=422,
        ) from exc


def _record_to_get(record: PerformancePlanRecord) -> PerformancePlanGetResponse:
    return PerformancePlanGetResponse(
        plan=record.plan,
        document_revision=record.document_revision,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def list_performance_plans(
    project_id: str,
    *,
    db_path: Path | None = None,
) -> PerformancePlanListResponse:
    return PerformancePlanListResponse(plans=list_plans(project_id, db_path=db_path))


def get_performance_plan(
    project_id: str,
    plan_id: str,
    *,
    db_path: Path | None = None,
) -> PerformancePlanGetResponse:
    return _record_to_get(get_plan(project_id, plan_id, db_path=db_path))


def create_performance_plan(
    project_id: str,
    request: PerformancePlanCreateRequest,
    *,
    db_path: Path | None = None,
) -> PerformancePlanGetResponse:
    fingerprint = request.source_composition_fingerprint
    if request.composition is not None:
        composition = _parse_composition(request.composition)
        fingerprint = composition_snapshot_fingerprint(composition)
    if request.plan is not None:
        plan = request.plan
        if fingerprint and not plan.source_composition_fingerprint:
            plan = plan.model_copy(update={"source_composition_fingerprint": fingerprint})
    elif request.preset_id and request.preset_id != "custom":
        if not fingerprint:
            raise PerformancePlanError(
                "performance_plan_invalid",
                "composition or source_composition_fingerprint is required when cloning a preset",
                http_status=422,
            )
        plan = clone_preset(
            request.preset_id,
            source_composition_fingerprint=fingerprint,
            name=request.name,
            project_id=project_id,
            seed=request.seed,
        )
        if request.engine:
            plan = plan.model_copy(update={"engine": request.engine})
    else:
        raise PerformancePlanError(
            "performance_plan_invalid",
            "Provide plan body or preset_id to clone",
            http_status=422,
        )
    # Strip client id for create.
    plan = plan.model_copy(update={"id": None, "project_id": project_id})
    if request.name:
        plan = plan.model_copy(update={"name": request.name.strip()})
    record = create_plan(project_id, plan, db_path=db_path)
    logger.info(
        "Service created performance plan",
        extra={
            "plan_id": record.id,
            "preset_id": record.preset_id,
            "project_id": project_id,
        },
    )
    return _record_to_get(record)


def update_performance_plan(
    project_id: str,
    plan_id: str,
    request: PerformancePlanUpdateRequest,
    *,
    db_path: Path | None = None,
) -> PerformancePlanGetResponse:
    record = replace_plan(
        project_id,
        plan_id,
        request.plan,
        expected_document_revision=request.expected_document_revision,
        db_path=db_path,
    )
    return _record_to_get(record)


def delete_performance_plan(
    project_id: str,
    plan_id: str,
    *,
    db_path: Path | None = None,
) -> None:
    delete_plan(project_id, plan_id, db_path=db_path)


def _maybe_ai_augment_plan(plan: PerformancePlanV1) -> PerformancePlanV1:
    """Optional AI path: dimension parameter patches only (fake / off by default)."""
    settings = load_performance_settings()
    if plan.engine != "ai_augmented":
        return plan
    if not settings.ai_enabled:
        logger.warning(
            "AI augment requested but disabled",
            extra={"code": "ai_augment_disabled", "plan_id": plan.id},
        )
        raise PerformancePlanError(
            "ai_augment_disabled",
            PERFORMANCE_ERROR_CODES["ai_augment_disabled"],
            http_status=422,
        )
    # Ship-1 fake path: deterministic soft nudge on dynamics contrast only.
    # Never invent pitch/harmony/events.
    dims = plan.dimensions.model_dump(mode="json")
    dims["dynamics"]["contrast"] = min(
        1.0, float(dims["dynamics"]["contrast"]) + 0.05
    )
    # Guard against accidental embeds if a future model path is wired.
    from app.performance_schemas import (
        PerformanceDimensions,
        reject_plan_embedded_material,
    )

    try:
        reject_plan_embedded_material({"dimensions": dims})
    except PerformancePlanError as exc:
        logger.warning(
            "AI augment refused forbidden material",
            extra={"code": "ai_augment_refused", "plan_id": plan.id},
        )
        raise PerformancePlanError(
            "ai_augment_refused",
            PERFORMANCE_ERROR_CODES["ai_augment_refused"],
            http_status=422,
            details=exc.details,
        ) from exc
    patched = plan.model_copy(
        update={"dimensions": PerformanceDimensions.model_validate(dims)}
    )
    logger.debug(
        "Applied fake AI dimension patch",
        extra={"plan_id": plan.id, "patch": "dynamics.contrast"},
    )
    return patched


def realize_performance_plan(
    project_id: str,
    plan_id: str,
    composition_payload: dict[str, Any],
    *,
    db_path: Path | None = None,
) -> PerformanceRealizeResponse:
    if not isinstance(composition_payload, dict):
        raise PerformancePlanError(
            "composition_required",
            PERFORMANCE_ERROR_CODES["composition_required"],
            http_status=422,
        )
    record = get_plan(project_id, plan_id, db_path=db_path)
    composition = _parse_composition(composition_payload)
    request_fp = composition_snapshot_fingerprint(composition)
    stale = is_stale_fingerprint(record.source_composition_fingerprint, request_fp)
    plan = _maybe_ai_augment_plan(record.plan)
    realization = realize_performance(
        composition, plan, plan_revision=record.document_revision
    )
    logger.info(
        "Realized performance plan",
        extra={
            "plan_id": plan_id,
            "preset_id": record.preset_id,
            "note_count": realization.metrics.note_count,
            "mean_abs_tick_delta": round(realization.metrics.mean_abs_tick_delta, 4),
            "mean_abs_velocity_delta": round(
                realization.metrics.mean_abs_velocity_delta, 4
            ),
            "stale": stale,
        },
    )
    if stale:
        logger.warning(
            "Performance plan soft-stale on realize",
            extra={"code": "performance_plan_stale", "plan_id": plan_id},
        )
    return PerformanceRealizeResponse(
        realization=realization,
        stale=stale,
        plan_id=plan_id,
        document_revision=record.document_revision,
    )


def compare_performance_plan(
    project_id: str,
    plan_id: str,
    composition_payload: dict[str, Any],
    *,
    db_path: Path | None = None,
) -> PerformanceCompareResponse:
    if not isinstance(composition_payload, dict):
        raise PerformancePlanError(
            "composition_required",
            PERFORMANCE_ERROR_CODES["composition_required"],
            http_status=422,
        )
    record = get_plan(project_id, plan_id, db_path=db_path)
    composition = _parse_composition(composition_payload)
    request_fp = composition_snapshot_fingerprint(composition)
    stale = is_stale_fingerprint(record.source_composition_fingerprint, request_fp)
    plan = _maybe_ai_augment_plan(record.plan)
    realization = realize_performance(
        composition, plan, plan_revision=record.document_revision
    )
    mech = mechanical_metrics(composition)
    sample = sample_deltas(realization, max_sample=8)
    logger.info(
        "Compared performance plan",
        extra={
            "plan_id": plan_id,
            "preset_id": record.preset_id,
            "stale": stale,
            "mean_abs_tick_delta": round(realization.metrics.mean_abs_tick_delta, 4),
            "sample_count": len(sample),
        },
    )
    return PerformanceCompareResponse(
        stale=stale,
        plan_id=plan_id,
        mechanical_metrics=mech,
        performed_metrics=realization.metrics,
        sample_deltas=sample,
        max_sample=8,
    )


def preset_catalog_response() -> dict[str, Any]:
    from app.performance_schemas import PerformancePresetCatalogResponse

    return PerformancePresetCatalogResponse(presets=list_preset_catalog()).model_dump(
        mode="json"
    )


# Re-export parse helper for tests.
__all__ = [
    "list_performance_plans",
    "get_performance_plan",
    "create_performance_plan",
    "update_performance_plan",
    "delete_performance_plan",
    "realize_performance_plan",
    "compare_performance_plan",
    "preset_catalog_response",
    "parse_performance_plan",
]
