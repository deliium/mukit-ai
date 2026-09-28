"""Graph and binding tests. Composition bytes stay unchanged."""

from __future__ import annotations

import copy
import logging

import pytest

from app.adaptive_score_schemas import AdaptiveScoreV1, parse_adaptive_score
from app.composition_schemas import CompositionV2
from app.services.adaptive_score_validation import (
    bind_material_refs,
    binding_status_for,
    validate_adaptive_score_graph,
)
from tests.test_adaptive_score_schema import adventure_score


def _score(**overrides) -> AdaptiveScoreV1:
    payload = adventure_score()
    payload.update(overrides)
    return parse_adaptive_score(payload)


def _composition(*, section_ids: list[str | None] | None = None, bar_count: int = 8) -> CompositionV2:
    ids = section_ids if section_ids is not None else ["section-explore"]
    sections = []
    for index, section_id in enumerate(ids):
        section = {
            "type": "intro",
            "start_bar": 1 if index == 0 else 1,
            "bar_count": bar_count,
            "start_tick": 0,
            "duration_ticks": bar_count * 1920,
        }
        if section_id is not None:
            section["id"] = section_id
        sections.append(section)
    if len(ids) > 1:
        sections = [
            {
                "id": ids[0],
                "type": "intro",
                "start_bar": 1,
                "bar_count": bar_count,
                "start_tick": 0,
                "duration_ticks": bar_count * 1920,
            }
        ]
        if ids[0] is None:
            sections[0].pop("id")
    return CompositionV2.model_validate(
        {
            "schema_version": "composition.v2",
            "tempo": 100,
            "key": "C major",
            "time_signature": "4/4",
            "ticks_per_quarter": 480,
            "duration_ticks": bar_count * 1920,
            "bar_count": bar_count,
            "sections": sections,
            "tracks": [
                {
                    "id": "melody-1",
                    "name": "Melody",
                    "instrument": "piano",
                    "role": "melody",
                    "midi_program": 0,
                    "channel": 1,
                    "events": [
                        {
                            "type": "note",
                            "pitch": "C4",
                            "start_tick": 0,
                            "duration_ticks": 480,
                            "velocity": 80,
                        }
                    ],
                }
            ],
        }
    )


def test_duration_quantization_eligibility_and_duplicate() -> None:
    payload = adventure_score()
    payload["states"][0]["min_duration_bars"] = 8
    payload["states"][0]["max_duration_bars"] = 2
    payload["transitions"][0]["quantization"] = "custom"
    payload["transitions"][0].pop("custom_grid_bars", None)
    payload["states"][0]["transition_ids"] = []
    payload["layers"][0]["id"] = "state-combat"
    score = parse_adaptive_score(payload)
    codes = {item.code for item in validate_adaptive_score_graph(score)}
    assert "duration_bounds" in codes
    assert "quantization_grid" in codes
    assert "transition_eligibility_mismatch" in codes
    assert "duplicate_id" in codes


def test_unreachable_is_warning_until_strict() -> None:
    payload = adventure_score()
    payload["states"].append(
        {
            "id": "state-secret",
            "name": "Secret",
            "intensity": 0.1,
            "material": {"kind": "section", "section_id": "section-explore"},
            "transition_ids": [],
        }
    )
    payload["initial_state_id"] = "state-exploration"
    score = parse_adaptive_score(payload)
    warnings = validate_adaptive_score_graph(score)
    unreachable = [item for item in warnings if item.code == "state_unreachable"]
    assert unreachable
    assert all(item.severity == "warning" for item in unreachable)
    strict = validate_adaptive_score_graph(score, strict=True)
    promoted = [item for item in strict if item.code == "state_unreachable"]
    assert promoted
    assert all(item.severity == "error" for item in promoted)


def test_bar_range_outside_and_composition_unchanged() -> None:
    payload = adventure_score()
    payload["states"] = [payload["states"][0]]
    payload["states"][0]["transition_ids"] = []
    payload["states"][0]["material"] = {"kind": "bar_range", "start_bar": 1, "end_bar": 12}
    payload["variants"] = []
    payload["transitions"] = []
    payload["layers"] = []
    payload["stingers"] = []
    score = parse_adaptive_score(payload)
    composition = _composition(bar_count=4)
    before = copy.deepcopy(composition.model_dump(mode="json"))
    findings = bind_material_refs(score, composition)
    after = composition.model_dump(mode="json")
    assert before == after
    assert any(item.code == "material_range_outside" for item in findings)


def test_null_section_id_is_not_a_target() -> None:
    payload = adventure_score()
    payload["states"] = [payload["states"][0]]
    payload["states"][0]["transition_ids"] = []
    payload["states"][0]["material"] = {"kind": "section", "section_id": "section-explore"}
    payload["variants"] = []
    payload["transitions"] = []
    payload["layers"] = []
    payload["stingers"] = []
    score = parse_adaptive_score(payload)
    composition = _composition(section_ids=[None], bar_count=4)
    findings = bind_material_refs(score, composition)
    assert any(item.code == "material_target_missing" for item in findings)


def test_mixed_revision_targets() -> None:
    payload = adventure_score()
    payload["states"][0]["material"]["revision_id"] = "rev-a"
    payload["states"][1]["material"]["revision_id"] = "rev-b"
    score = parse_adaptive_score(payload)
    codes = {item.code for item in validate_adaptive_score_graph(score)}
    assert "mixed_revision_targets" in codes


def test_composition_unavailable_without_target() -> None:
    score = _score()
    findings = bind_material_refs(score, None)
    assert findings[0].code == "composition_unavailable"
    assert findings[0].severity == "error"


def test_asset_only_is_unchecked() -> None:
    payload = {
        "schema_version": "adaptive.score.v1",
        "name": "Stinger bed",
        "stingers": [
            {
                "id": "stinger-hit",
                "name": "Hit",
                "material": {
                    "kind": "asset",
                    "asset_kind": "neural_stem",
                    "asset_id": "stem-1",
                },
                "interrupt_policy": "overlay",
                "quantization": "immediate",
                "retrigger": "always",
            }
        ],
    }
    score = parse_adaptive_score(payload)
    findings = bind_material_refs(score, None)
    assert findings == []
    assert binding_status_for(score, None) == "unchecked"


def test_binding_logs_omit_note_pitch(caplog: pytest.LogCaptureFixture) -> None:
    payload = adventure_score()
    payload["states"] = [payload["states"][0]]
    payload["states"][0]["transition_ids"] = []
    payload["variants"] = []
    payload["transitions"] = []
    payload["layers"] = []
    payload["stingers"] = []
    score = parse_adaptive_score(payload)
    composition = _composition(section_ids=["section-explore"], bar_count=8)
    caplog.clear()
    caplog.set_level(logging.INFO)
    bind_material_refs(score, composition)
    blob = " ".join(
        record.getMessage()
        for record in caplog.records
        if record.name.startswith("app.services.adaptive_score")
    )
    assert "C4" not in blob
    assert "error_count" in blob or any(
        getattr(record, "error_count", None) is not None
        for record in caplog.records
        if record.name.startswith("app.services.adaptive_score")
    )
