"""Closed preset catalog and clone helpers.

Catalog reads never write SQLite. Clone returns an in-memory plan body.
"""

from __future__ import annotations

import copy
import logging
from typing import Any

from app.performance_conductor_constants import (
    CATALOG_PRESET_IDS,
    PLAN_SCHEMA_VERSION,
    PRESET_DEFAULT_SEEDS,
    PRESET_DIMENSIONS,
)
from app.performance_schemas import (
    PerformanceDimensions,
    PerformancePlanError,
    PerformancePlanV1,
    PerformancePresetCatalogItemV1,
    parse_performance_plan,
)

logger = logging.getLogger(__name__)

_PRESET_DESCRIPTIONS: dict[str, str] = {
    "mechanical": "Near-zero rubato/humanize; literal pedaling; flat accents.",
    "restrained": "Light rubato, modest dynamics, dry-leaning pedal.",
    "intimate": "Gentle rubato, soft dynamics ceiling, legato bias, warm pedal.",
    "dramatic": "Deeper rubato, high contrast dynamics, strong accents, wider humanize.",
}

_PRESET_NAMES: dict[str, str] = {
    "mechanical": "Mechanical",
    "restrained": "Restrained",
    "intimate": "Intimate",
    "dramatic": "Dramatic",
}


def list_preset_catalog() -> list[PerformancePresetCatalogItemV1]:
    """Static four seeds — side-effect free."""
    items: list[PerformancePresetCatalogItemV1] = []
    for preset_id in CATALOG_PRESET_IDS:
        dims = PerformanceDimensions.model_validate(
            copy.deepcopy(PRESET_DIMENSIONS[preset_id])
        )
        items.append(
            PerformancePresetCatalogItemV1(
                preset_id=preset_id,  # type: ignore[arg-type]
                name=_PRESET_NAMES[preset_id],
                description=_PRESET_DESCRIPTIONS[preset_id],
                dimensions=dims,
                seed=PRESET_DEFAULT_SEEDS[preset_id],
            )
        )
    logger.debug(
        "Served performance preset catalog",
        extra={"preset_count": len(items)},
    )
    return items


def clone_preset(
    preset_id: str,
    *,
    source_composition_fingerprint: str,
    name: str | None = None,
    project_id: str | None = None,
    seed: int | None = None,
) -> PerformancePlanV1:
    """Build a plan body from a catalog seed (no SQLite write)."""
    if preset_id not in PRESET_DIMENSIONS:
        raise PerformancePlanError(
            "preset_unknown",
            f"Unknown preset_id: {preset_id}",
            http_status=422,
            details={"preset_id": preset_id},
        )
    body: dict[str, Any] = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "name": name or _PRESET_NAMES[preset_id],
        "preset_id": preset_id,
        "engine": "deterministic",
        "source_composition_fingerprint": source_composition_fingerprint,
        "dimensions": copy.deepcopy(PRESET_DIMENSIONS[preset_id]),
        "seed": PRESET_DEFAULT_SEEDS[preset_id] if seed is None else int(seed),
    }
    if project_id is not None:
        body["project_id"] = project_id
    plan = parse_performance_plan(body)
    logger.debug(
        "Cloned performance preset",
        extra={"preset_id": preset_id, "seed": plan.seed},
    )
    return plan
