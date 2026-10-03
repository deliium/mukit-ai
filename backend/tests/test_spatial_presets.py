"""Preset catalog and distinguishability via compile_spatial_preview."""

from __future__ import annotations

from app.services.spatial_compiler import compile_spatial_preview, preview_metric_digest
from app.services.spatial_presets import clone_preset, list_preset_catalog
from app.spatial_constants import CATALOG_PRESET_IDS
from app.spatial_schemas import SpatialSceneError
import pytest


def test_catalog_lists_three_seeds_side_effect_free() -> None:
    a = list_preset_catalog()
    b = list_preset_catalog()
    assert [p.preset_id for p in a] == list(CATALOG_PRESET_IDS)
    assert [p.preset_id for p in b] == list(CATALOG_PRESET_IDS)
    assert all(p.source_count >= 2 for p in a)


def test_clone_unknown_preset_refused() -> None:
    with pytest.raises(SpatialSceneError) as captured:
        clone_preset("not_a_preset", source_composition_fingerprint="a" * 32)
    assert captured.value.code == "preset_unknown"


def test_presets_produce_distinguishable_metric_digests() -> None:
    digests: dict[str, str] = {}
    for preset_id in CATALOG_PRESET_IDS:
        scene = clone_preset(
            preset_id,
            source_composition_fingerprint="f" * 32,
        ).model_copy(update={"id": f"sscene_{preset_id[:8].ljust(8, '0')}abcdef01"})
        preview = compile_spatial_preview(scene, scene_revision=1)
        digests[preset_id] = preview_metric_digest(preview)
    values = list(digests.values())
    assert len(set(values)) == len(values), digests
