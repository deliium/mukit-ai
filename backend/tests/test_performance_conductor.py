"""Deterministic conductor unit tests: identity, ties, seed stability."""

from __future__ import annotations

import json
from pathlib import Path

from app.composition_schemas import CompositionV2
from app.performance_conductor_constants import ENGINE_VERSION, PRESET_DIMENSIONS
from app.performance_schemas import PerformancePlanV1, parse_performance_plan
from app.services.performance_conductor import realize_performance
from app.services.performance_identity import identity_digest
from app.services.performance_presets import clone_preset

_V2 = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "fixtures"
    / "composition_v2_expressive.json"
)


def _composition() -> CompositionV2:
    return CompositionV2.model_validate(json.loads(_V2.read_text(encoding="utf-8")))


def _plan(preset_id: str = "dramatic") -> PerformancePlanV1:
    return clone_preset(
        preset_id,
        source_composition_fingerprint="c" * 32,
        name=f"{preset_id}-plan",
    ).model_copy(update={"id": "pplan_0123456789abcdef"})


def test_realize_preserves_identity_and_engine_version() -> None:
    composition = _composition()
    before = identity_digest(composition)
    realization = realize_performance(composition, _plan("intimate"), plan_revision=1)
    assert identity_digest(composition) == before
    assert realization.engine_version == ENGINE_VERSION
    assert realization.metrics.capture_performance_ignored is True
    assert all("pitch" not in note.model_dump() for note in realization.notes)


def test_realize_is_seed_stable() -> None:
    composition = _composition()
    plan = _plan("dramatic")
    a = realize_performance(composition, plan, plan_revision=1)
    b = realize_performance(composition, plan, plan_revision=1)
    assert [n.model_dump() for n in a.notes] == [n.model_dump() for n in b.notes]


def test_tie_chain_members_share_tick_delta() -> None:
    composition = _composition()
    realization = realize_performance(composition, _plan("dramatic"), plan_revision=1)
    by_id = {n.event_id: n for n in realization.notes}
    assert "m3" in by_id and "m4" in by_id
    assert by_id["m3"].tick_delta == by_id["m4"].tick_delta


def test_mechanical_near_zero_deltas() -> None:
    composition = _composition()
    realization = realize_performance(composition, _plan("mechanical"), plan_revision=1)
    assert realization.metrics.mean_abs_tick_delta == 0.0
    assert realization.metrics.mean_abs_velocity_delta == 0.0


def test_duration_floor_holds() -> None:
    composition = _composition()
    plan = parse_performance_plan(
        {
            "schema_version": "performance.plan.v1",
            "name": "floor",
            "preset_id": "custom",
            "engine": "deterministic",
            "source_composition_fingerprint": "d" * 32,
            "dimensions": {
                **PRESET_DIMENSIONS["dramatic"],
                "articulation": {"legato_bias": -1.0, "staccato_bias": 1.0},
            },
            "seed": 1,
            "id": "pplan_0123456789abcdef",
        }
    )
    realization = realize_performance(composition, plan, plan_revision=1)
    events = {
        (track.id, event.id): event
        for track in composition.tracks
        for event in track.events
    }
    for note in realization.notes:
        event = events[(note.track_id, note.event_id)]
        assert event.duration_ticks + note.duration_delta >= 1
