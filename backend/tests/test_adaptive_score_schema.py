"""Schema tests for adaptive.score.v1."""

from __future__ import annotations

import logging

import pytest

from app.adaptive_score_schemas import (
    AdaptiveScoreError,
    AdaptiveScoreUpdateRequest,
    AdaptiveScoreV1,
    parse_adaptive_score,
)
from app.services.adaptive_score_validation import reject_embedded_note_material


def _section(section_id: str) -> dict:
    return {"kind": "section", "section_id": section_id}


def adventure_score() -> dict:
    """Exploration, suspense, combat, and victory, with a return cycle."""
    return {
        "schema_version": "adaptive.score.v1",
        "name": "Main cue",
        "initial_state_id": "state-exploration",
        "default_state_id": "state-exploration",
        "fallback": {
            "on_missing_material": "hold",
            "on_invalid_transition": "stay",
            "on_unresolved_condition": "stay",
        },
        "states": [
            {
                "id": "state-exploration",
                "name": "Exploration",
                "intensity": 0.2,
                "material": _section("section-explore"),
                "transition_ids": ["to-suspense"],
            },
            {
                "id": "state-suspense",
                "name": "Suspense",
                "intensity": 0.45,
                "material": _section("section-suspense"),
                "transition_ids": ["to-combat"],
            },
            {
                "id": "state-combat",
                "name": "Combat",
                "intensity": 0.8,
                "material": _section("section-combat"),
                "loop": {"enabled": True, "start_bar": 1, "end_bar": 4},
                "transition_ids": ["to-victory"],
            },
            {
                "id": "state-victory",
                "name": "Victory",
                "intensity": 0.6,
                "material": _section("section-victory"),
                "transition_ids": ["to-exploration"],
            },
        ],
        "variants": [
            {
                "id": "variant-combat-high",
                "state_id": "state-combat",
                "name": "Combat high",
                "material": _section("section-combat"),
                "intensity_min": 0.7,
                "intensity_max": 1.0,
            }
        ],
        "transitions": [
            {
                "id": "to-suspense",
                "from_state_id": "state-exploration",
                "to_state_id": "state-suspense",
                "quantization": "bar",
                "priority": 1,
                "conditions": [{"kind": "flag_equals", "flag": "tension_high", "value": True}],
            },
            {
                "id": "to-combat",
                "from_state_id": "state-suspense",
                "to_state_id": "state-combat",
                "quantization": "beat",
                "conditions": [{"kind": "intensity_at_least", "value": 0.6}],
            },
            {
                "id": "to-victory",
                "from_state_id": "state-combat",
                "to_state_id": "state-victory",
                "quantization": "bar",
                "conditions": [{"kind": "manual"}],
            },
            {
                "id": "to-exploration",
                "from_state_id": "state-victory",
                "to_state_id": "state-exploration",
                "quantization": "next_exit",
                "fallback_behavior": "default_state",
                "conditions": [{"kind": "min_time_in_state_bars", "value": 2}],
            },
        ],
        "layers": [
            {
                "id": "layer-combat-low",
                "name": "Combat bed pulse",
                "state_id": "state-combat",
                "material": _section("section-combat-low"),
                "intensity_min": 0.0,
                "intensity_max": 0.49,
                "mix_hint": "bed",
                "default_active": True,
            },
            {
                "id": "layer-combat-high",
                "name": "Combat percussion",
                "state_id": "state-combat",
                "material": _section("section-combat-high"),
                "intensity_min": 0.5,
                "intensity_max": 1.0,
                "mix_hint": "foreground",
                "default_active": False,
            },
        ],
        "stingers": [
            {
                "id": "stinger-victory",
                "name": "Victory sting",
                "material": _section("section-victory"),
                "associated_state_id": "state-victory",
                "interrupt_policy": "overlay",
                "quantization": "bar",
                "retrigger": "once",
            }
        ],
    }


def test_adventure_score_round_trip() -> None:
    score = parse_adaptive_score(adventure_score())
    again = AdaptiveScoreV1.model_validate(score.model_dump(mode="json"))
    names = [state.name for state in again.states]
    assert names == ["Exploration", "Suspense", "Combat", "Victory"]
    combat_layers = [layer for layer in again.layers if layer.state_id == "state-combat"]
    assert len(combat_layers) == 2
    assert combat_layers[0].intensity_max < combat_layers[1].intensity_min
    assert again.stingers[0].name == "Victory sting"
    back = next(item for item in again.transitions if item.id == "to-exploration")
    assert back.to_state_id == "state-exploration"
    assert back.fallback_behavior == "default_state"
    assert "fallback_state_id" not in back.model_dump()


@pytest.mark.parametrize(
    "payload",
    [
        {"events": [{"pitch": "C4"}]},
        {"states": [{"pitch": 60}]},
        {"layers": [{"material": {"notes": [1, 2, 3]}}]},
        {"nested": {"composition": {"schema_version": "composition.v2"}}},
        {"score": {"composition_json": "{}"}},
    ],
)
def test_reject_embedded_note_keys(payload: dict, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    with pytest.raises(AdaptiveScoreError) as captured:
        reject_embedded_note_material(payload)
    assert captured.value.code == "embedded_note_material"
    blob = " ".join(record.getMessage() for record in caplog.records)
    assert "C4" not in blob


def test_schema_version_must_be_adaptive_score_v1() -> None:
    payload = adventure_score()
    payload["schema_version"] = "composition.v5"
    with pytest.raises(AdaptiveScoreError) as captured:
        parse_adaptive_score(payload)
    assert captured.value.code == "unsupported_schema_version"


def test_extra_note_key_rejected_by_model() -> None:
    payload = adventure_score()
    payload["events"] = [{"pitch": "C4"}]
    with pytest.raises(AdaptiveScoreError) as captured:
        parse_adaptive_score(payload)
    assert captured.value.code == "embedded_note_material"


def test_update_request_keeps_revision_beside_body() -> None:
    score = adventure_score()
    request = AdaptiveScoreUpdateRequest(expected_document_revision=2, score=score)
    assert request.expected_document_revision == 2
    assert "expected_document_revision" not in request.score


def test_schema_failure_log_omits_rejected_body(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    payload = adventure_score()
    payload["schema_version"] = "composition.v5"
    with pytest.raises(AdaptiveScoreError):
        parse_adaptive_score(payload)
    assert "C4" not in " ".join(record.getMessage() for record in caplog.records)
    assert any(
        getattr(record, "code", None) == "unsupported_schema_version" for record in caplog.records
    )
