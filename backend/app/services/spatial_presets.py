"""Closed spatial preset catalog and clone helpers.

Catalog reads never write SQLite. Clone returns an in-memory scene body.
"""

from __future__ import annotations

import copy
import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.spatial_constants import CATALOG_PRESET_IDS, ENGINE_VERSION, SCENE_SCHEMA_VERSION
from app.spatial_schemas import (
    SpatialPresetCatalogItemV1,
    SpatialSceneError,
    SpatialSceneV1,
    parse_spatial_scene,
)

logger = logging.getLogger(__name__)

_FIXTURE_PATH = (
    Path(__file__).resolve().parents[1] / "fixtures" / "spatial_presets.v1.json"
)


@lru_cache(maxsize=1)
def _load_preset_table() -> dict[str, Any]:
    data = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    presets = data.get("presets")
    if not isinstance(presets, dict):
        raise RuntimeError("spatial_presets.v1.json missing presets map")
    return presets


def list_preset_catalog() -> list[SpatialPresetCatalogItemV1]:
    """Static three seeds — side-effect free."""
    table = _load_preset_table()
    items: list[SpatialPresetCatalogItemV1] = []
    for preset_id in CATALOG_PRESET_IDS:
        row = table[preset_id]
        items.append(
            SpatialPresetCatalogItemV1(
                preset_id=preset_id,  # type: ignore[arg-type]
                name=str(row["name"]),
                description=str(row["description"]),
                source_count=len(row.get("sources") or []),
            )
        )
    logger.debug(
        "Served spatial preset catalog",
        extra={"preset_count": len(items)},
    )
    return items


def clone_preset(
    preset_id: str,
    *,
    source_composition_fingerprint: str,
    name: str | None = None,
    project_id: str | None = None,
    source_stem_set_id: str | None = None,
    source_stem_set_fingerprint: str | None = None,
) -> SpatialSceneV1:
    """Build a scene body from a catalog seed (no SQLite write)."""
    table = _load_preset_table()
    if preset_id not in table:
        raise SpatialSceneError(
            "preset_unknown",
            f"Unknown preset_id: {preset_id}",
            http_status=422,
            details={"preset_id": preset_id},
        )
    row = table[preset_id]
    body: dict[str, Any] = {
        "schema_version": SCENE_SCHEMA_VERSION,
        "name": name or str(row["name"]),
        "source_composition_fingerprint": source_composition_fingerprint,
        "engine_version": ENGINE_VERSION,
        "sources": copy.deepcopy(row.get("sources") or []),
        "listener": {"position": [0.0, 0.0, 0.0], "yaw_deg": 0.0},
    }
    if project_id is not None:
        body["project_id"] = project_id
    if source_stem_set_id is not None:
        body["source_stem_set_id"] = source_stem_set_id
    if source_stem_set_fingerprint is not None:
        body["source_stem_set_fingerprint"] = source_stem_set_fingerprint
    scene = parse_spatial_scene(body)
    logger.debug(
        "Cloned spatial preset",
        extra={"preset_id": preset_id, "source_count": len(scene.sources)},
    )
    return scene
