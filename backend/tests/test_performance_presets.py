"""Preset catalog clone + distinguishability via realize_performance."""

from __future__ import annotations

import json
from pathlib import Path

from app.composition_schemas import CompositionV2
from app.performance_conductor_constants import CATALOG_PRESET_IDS
from app.services.performance_conductor import realize_performance
from app.services.performance_identity import metrics_digest
from app.services.performance_presets import clone_preset, list_preset_catalog

_V2 = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "fixtures"
    / "composition_v2_expressive.json"
)


def test_catalog_lists_four_seeds() -> None:
    catalog = list_preset_catalog()
    assert [item.preset_id for item in catalog] == list(CATALOG_PRESET_IDS)


def test_clone_preset_sets_dimensions() -> None:
    plan = clone_preset("intimate", source_composition_fingerprint="e" * 32)
    assert plan.preset_id == "intimate"
    assert plan.dimensions.tempo_rubato.depth > 0


def test_presets_distinguishable_via_realize() -> None:
    composition = CompositionV2.model_validate(json.loads(_V2.read_text(encoding="utf-8")))
    digests: dict[str, str] = {}
    for preset_id in CATALOG_PRESET_IDS:
        plan = clone_preset(
            preset_id,
            source_composition_fingerprint="f" * 32,
        ).model_copy(update={"id": f"pplan_{preset_id[:8].ljust(16, '0')}"})
        realization = realize_performance(composition, plan, plan_revision=1)
        digests[preset_id] = metrics_digest(realization.metrics)
    assert digests["mechanical"] != digests["dramatic"]
    assert digests["intimate"] != digests["restrained"]
    assert len(set(digests.values())) == 4
