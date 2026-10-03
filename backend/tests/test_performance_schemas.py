"""Accept/reject fixtures for performance.plan.v1 and realization contracts."""

from __future__ import annotations

import pytest

import json
from pathlib import Path

from app.composition_schemas import CompositionV2
from app.performance_conductor_constants import (
    ENGINE_VERSION,
    PLAN_SCHEMA_VERSION,
    PRESET_DIMENSIONS,
    REALIZATION_SCHEMA_VERSION,
)
from app.performance_schemas import (
    PerformancePlanError,
    PerformanceRealizationV1,
    parse_performance_plan,
    parse_performance_realization,
    reject_plan_embedded_material,
)
from app.services.performance_identity import identity_digest

_V2_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "fixtures"
    / "composition_v2_expressive.json"
)


def _minimal_plan(**overrides):
    body = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "name": "Intimate take",
        "preset_id": "intimate",
        "engine": "deterministic",
        "source_composition_fingerprint": "a" * 32,
        "dimensions": PRESET_DIMENSIONS["intimate"],
        "seed": 42,
    }
    body.update(overrides)
    return body


def test_parse_plan_accepts_preset_dimensions() -> None:
    plan = parse_performance_plan(_minimal_plan())
    assert plan.preset_id == "intimate"
    assert plan.dimensions.tempo_rubato.depth == pytest.approx(0.35)


def test_reject_plan_embeds_events() -> None:
    with pytest.raises(PerformancePlanError) as captured:
        reject_plan_embedded_material({"events": [{"id": "n1"}]})
    assert captured.value.code == "plan_embeds_events"


def test_reject_plan_embeds_harmony_nested() -> None:
    body = _minimal_plan()
    body["meta"] = {"harmony": [{"bar": 1, "chord": "C"}]}
    with pytest.raises(PerformancePlanError) as captured:
        parse_performance_plan(body)
    assert captured.value.code == "plan_embeds_harmony"


def test_reject_plan_embeds_pitch() -> None:
    with pytest.raises(PerformancePlanError) as captured:
        reject_plan_embedded_material({"pitch": "C4"})
    assert captured.value.code == "plan_embeds_pitch"


def test_realization_refuses_pitch_fields() -> None:
    with pytest.raises(PerformancePlanError) as captured:
        parse_performance_realization(
            {
                "schema_version": REALIZATION_SCHEMA_VERSION,
                "plan_id": "pplan_0123456789abcdef",
                "plan_revision": 1,
                "engine_version": ENGINE_VERSION,
                "source_composition_fingerprint": "b" * 32,
                "notes": [
                    {
                        "event_id": "m1",
                        "track_id": "melody-1",
                        "tick_delta": 0,
                        "duration_delta": 0,
                        "velocity": 90,
                        "pitch": "C4",
                    }
                ],
            }
        )
    assert captured.value.code == "realization_invalid"


def test_realization_accepts_without_pitch() -> None:
    realization = PerformanceRealizationV1.model_validate(
        {
            "schema_version": REALIZATION_SCHEMA_VERSION,
            "plan_id": "pplan_0123456789abcdef",
            "plan_revision": 1,
            "engine_version": ENGINE_VERSION,
            "source_composition_fingerprint": "b" * 32,
            "notes": [
                {
                    "event_id": "m1",
                    "track_id": "melody-1",
                    "tick_delta": 4,
                    "duration_delta": -2,
                    "velocity": 88,
                }
            ],
        }
    )
    assert realization.notes[0].velocity == 88
    assert not hasattr(realization.notes[0], "pitch") or "pitch" not in realization.notes[0].model_dump()


def test_identity_digest_stable_and_ignores_unrelated() -> None:
    composition = CompositionV2.model_validate(
        json.loads(_V2_FIXTURE.read_text(encoding="utf-8"))
    )
    first = identity_digest(composition)
    second = identity_digest(composition)
    assert first == second
    assert len(first) == 64
