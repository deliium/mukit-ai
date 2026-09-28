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


def _bars(start: int = 1, end: int = 4) -> dict:
    return {"kind": "bar_range", "start_bar": start, "end_bar": end}


def _minimal_score(**overrides) -> AdaptiveScoreV1:
    payload = {
        "schema_version": "adaptive.score.v1",
        "name": "Cue",
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
                "material": _bars(),
                "transition_ids": [],
            }
        ],
    }
    payload.update(overrides)
    return parse_adaptive_score(payload)


def _finding_by(score: AdaptiveScoreV1, code: str):
    return [item for item in validate_adaptive_score_graph(score) if item.code == code]


def test_min_time_above_max_duration_is_impossible() -> None:
    score = _minimal_score(
        states=[
            {
                "id": "state-combat",
                "name": "Combat",
                "intensity": 0.4,
                "max_duration_bars": 2,
                "material": _bars(),
                "transition_ids": ["to-exit"],
            },
            {
                "id": "state-victory",
                "name": "Victory",
                "intensity": 0.2,
                "material": _bars(),
                "transition_ids": [],
            },
        ],
        initial_state_id="state-combat",
        default_state_id="state-combat",
        transitions=[
            {
                "id": "to-exit",
                "from_state_id": "state-combat",
                "to_state_id": "state-victory",
                "quantization": "bar",
                "conditions": [{"kind": "min_time_in_state_bars", "value": 5}],
            }
        ],
    )
    hits = _finding_by(score, "impossible_transition")
    assert len(hits) == 1
    assert hits[0].target_id == "to-exit"
    assert "state-combat" in hits[0].message
    assert "5" in hits[0].message
    assert "2" in hits[0].message
    assert len(hits[0].message) <= 200


def test_intensity_conditions_beyond_state_and_variants() -> None:
    score = _minimal_score(
        states=[
            {
                "id": "state-combat",
                "name": "Combat",
                "intensity": 0.4,
                "material": _bars(),
                "transition_ids": ["too-high", "too-low"],
            },
            {
                "id": "state-victory",
                "name": "Victory",
                "intensity": 0.2,
                "material": _bars(),
                "transition_ids": [],
            },
        ],
        initial_state_id="state-combat",
        default_state_id="state-combat",
        variants=[
            {
                "id": "variant-high",
                "state_id": "state-combat",
                "name": "High",
                "material": _bars(),
                "intensity_min": 0.5,
                "intensity_max": 0.7,
            }
        ],
        transitions=[
            {
                "id": "too-high",
                "from_state_id": "state-combat",
                "to_state_id": "state-victory",
                "quantization": "bar",
                "conditions": [{"kind": "intensity_at_least", "value": 0.9}],
            },
            {
                "id": "too-low",
                "from_state_id": "state-combat",
                "to_state_id": "state-victory",
                "quantization": "bar",
                "conditions": [{"kind": "intensity_at_most", "value": 0.1}],
            },
        ],
    )
    hits = {item.target_id: item for item in _finding_by(score, "impossible_transition")}
    assert "0.9" in hits["too-high"].message
    assert "0.1" in hits["too-low"].message
    covered = _minimal_score(
        states=[
            {
                "id": "state-combat",
                "name": "Combat",
                "intensity": 0.4,
                "material": _bars(),
                "transition_ids": ["covered"],
            },
            {
                "id": "state-victory",
                "name": "Victory",
                "intensity": 0.2,
                "material": _bars(),
                "transition_ids": [],
            },
        ],
        initial_state_id="state-combat",
        default_state_id="state-combat",
        variants=[
            {
                "id": "variant-high",
                "state_id": "state-combat",
                "name": "High",
                "material": _bars(),
                "intensity_min": 0.5,
                "intensity_max": 0.95,
            }
        ],
        transitions=[
            {
                "id": "covered",
                "from_state_id": "state-combat",
                "to_state_id": "state-victory",
                "quantization": "bar",
                "conditions": [{"kind": "intensity_at_least", "value": 0.9}],
            }
        ],
    )
    assert _finding_by(covered, "impossible_transition") == []


def test_next_exit_without_an_end_is_impossible() -> None:
    score = _minimal_score(
        states=[
            {
                "id": "state-hit",
                "name": "Hit",
                "intensity": 0.1,
                "material": {"kind": "asset", "asset_kind": "neural_stem", "asset_id": "stem-1"},
                "exit": {"kind": "material_end"},
                "transition_ids": ["to-next"],
            },
            {
                "id": "state-rest",
                "name": "Rest",
                "intensity": 0.1,
                "material": _bars(),
                "transition_ids": [],
            },
        ],
        initial_state_id="state-hit",
        default_state_id="state-hit",
        transitions=[
            {
                "id": "to-next",
                "from_state_id": "state-hit",
                "to_state_id": "state-rest",
                "quantization": "next_exit",
                "conditions": [],
            }
        ],
    )
    hits = _finding_by(score, "impossible_transition")
    assert hits
    assert "state-hit" in hits[0].message
    assert "next_exit" in hits[0].message
    section_score = _minimal_score(
        states=[
            {
                "id": "state-hit",
                "name": "Hit",
                "intensity": 0.1,
                "material": {"kind": "section", "section_id": "section-1"},
                "exit": {"kind": "material_end"},
                "transition_ids": ["to-next"],
            },
            {
                "id": "state-rest",
                "name": "Rest",
                "intensity": 0.1,
                "material": {"kind": "section", "section_id": "section-1"},
                "transition_ids": [],
            },
        ],
        initial_state_id="state-hit",
        default_state_id="state-hit",
        transitions=[
            {
                "id": "to-next",
                "from_state_id": "state-hit",
                "to_state_id": "state-rest",
                "quantization": "next_exit",
            }
        ],
    )
    assert _finding_by(section_score, "impossible_transition") == []


def test_cycle_of_manual_edges_is_not_impossible_or_deadlock() -> None:
    score = _chain(loop_back=True)
    codes = {item.code for item in validate_adaptive_score_graph(score)}
    assert "impossible_transition" not in codes
    assert "transition_deadlock" not in codes


def test_terminal_state_is_not_a_missing_fallback() -> None:
    score = _chain(loop_back=False)
    findings = validate_adaptive_score_graph(score)
    assert all(item.code != "missing_fallback_state" for item in findings)
    assert all(item.code != "transition_deadlock" for item in findings)
    assert any(item.code == "state_unreachable" for item in findings) is False


def test_policy_default_state_without_a_state_is_an_error() -> None:
    score = _minimal_score(
        default_state_id=None,
        fallback={
            "on_missing_material": "default_state",
            "on_invalid_transition": "stay",
            "on_unresolved_condition": "stay",
        },
    )
    findings = validate_adaptive_score_graph(score)
    errors = [item for item in findings if item.code == "missing_fallback_state"]
    assert errors
    assert "null" in errors[0].message
    assert all(item.code != "default_state_missing" for item in findings)


def test_transition_fallback_without_default_suppresses_warning() -> None:
    score = _minimal_score(
        default_state_id=None,
        states=[
            {
                "id": "state-exploration",
                "name": "Exploration",
                "intensity": 0.2,
                "material": _bars(),
                "transition_ids": ["to-combat"],
            },
            {
                "id": "state-combat",
                "name": "Combat",
                "intensity": 0.4,
                "material": _bars(),
                "transition_ids": [],
            },
        ],
        transitions=[
            {
                "id": "to-combat",
                "from_state_id": "state-exploration",
                "to_state_id": "state-combat",
                "quantization": "bar",
                "fallback_behavior": "default_state",
            }
        ],
    )
    findings = validate_adaptive_score_graph(score)
    errors = [item for item in findings if item.code == "missing_fallback_state"]
    assert any(item.target_id == "to-combat" for item in errors)
    assert "to-combat" in errors[0].message or any("to-combat" in item.message for item in errors)
    assert all(item.code != "default_state_missing" for item in findings)


def test_loop_outside_material_bars() -> None:
    score = _minimal_score(
        states=[
            {
                "id": "state-combat",
                "name": "Combat",
                "intensity": 0.4,
                "material": _bars(4, 8),
                "loop": {"enabled": True, "start_bar": 1, "end_bar": 2},
                "transition_ids": [],
            }
        ],
        initial_state_id="state-combat",
        default_state_id="state-combat",
    )
    hits = _finding_by(score, "loop_bounds")
    assert hits
    assert hits[0].target_id == "state-combat"
    assert "1" in hits[0].message and "2" in hits[0].message and "4" in hits[0].message


def test_loop_outside_section_span() -> None:
    payload = _minimal_score(
        states=[
            {
                "id": "state-combat",
                "name": "Combat",
                "intensity": 0.4,
                "material": {"kind": "section", "section_id": "section-2"},
                "loop": {"enabled": True, "start_bar": 1, "end_bar": 4},
                "transition_ids": [],
            }
        ],
        initial_state_id="state-combat",
        default_state_id="state-combat",
    )
    composition = CompositionV2.model_validate(
        {
            "schema_version": "composition.v2",
            "tempo": 100,
            "key": "C major",
            "time_signature": "4/4",
            "ticks_per_quarter": 480,
            "duration_ticks": 7680,
            "bar_count": 4,
            "sections": [
                {
                    "id": "section-1",
                    "type": "intro",
                    "start_bar": 1,
                    "bar_count": 2,
                    "start_tick": 0,
                    "duration_ticks": 3840,
                },
                {
                    "id": "section-2",
                    "type": "chorus",
                    "start_bar": 3,
                    "bar_count": 2,
                    "start_tick": 3840,
                    "duration_ticks": 3840,
                },
            ],
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
    hits = [item for item in bind_material_refs(payload, composition) if item.code == "loop_bounds"]
    assert hits
    assert "section-2" in hits[0].message
    assert "state-combat" in hits[0].message


def test_impossible_self_loop_with_stay_is_deadlock(caplog: pytest.LogCaptureFixture) -> None:
    score = _minimal_score(
        states=[
            {
                "id": "state-combat",
                "name": "Combat",
                "intensity": 0.4,
                "max_duration_bars": 2,
                "material": _bars(),
                "transition_ids": ["to-self"],
            }
        ],
        initial_state_id="state-combat",
        default_state_id="state-combat",
        transitions=[
            {
                "id": "to-self",
                "from_state_id": "state-combat",
                "to_state_id": "state-combat",
                "quantization": "bar",
                "conditions": [{"kind": "min_time_in_state_bars", "value": 8}],
            }
        ],
    )
    caplog.set_level(logging.DEBUG)
    findings = validate_adaptive_score_graph(score)
    deadlocks = [item for item in findings if item.code == "transition_deadlock"]
    assert len(deadlocks) == 1
    assert deadlocks[0].target_id == "state-combat"
    assert "state-combat" in deadlocks[0].message
    assert any(
        getattr(record, "component_size", None) == 1
        for record in caplog.records
        if record.name.startswith("app.services.adaptive_score")
    )


def test_timing_boundaries_must_agree() -> None:
    early_exit = _minimal_score(
        states=[
            {
                "id": "state-combat",
                "name": "Combat",
                "intensity": 0.4,
                "material": _bars(1, 8),
                "entry": {"kind": "bar", "bar": 4},
                "exit": {"kind": "bar", "bar": 2},
                "transition_ids": [],
            }
        ],
        initial_state_id="state-combat",
        default_state_id="state-combat",
    )
    order = _finding_by(early_exit, "timing_incompatible")
    assert any("exit bar 2" in item.message and "entry bar 4" in item.message for item in order)

    outside = _minimal_score(
        states=[
            {
                "id": "state-combat",
                "name": "Combat",
                "intensity": 0.4,
                "material": {"kind": "bar_range", "start_bar": 2, "end_bar": 4, "start_tick": 10, "end_tick": 40},
                "entry": {"kind": "tick", "tick": 0},
                "exit": {"kind": "tick", "tick": 20},
                "transition_ids": [],
            }
        ],
        initial_state_id="state-combat",
        default_state_id="state-combat",
    )
    ticks = _finding_by(outside, "timing_incompatible")
    assert any("tick 0" in item.message and "state-combat" in item.message for item in ticks)

    mixed = _minimal_score(
        states=[
            {
                "id": "state-combat",
                "name": "Combat",
                "intensity": 0.4,
                "material": {"kind": "bar_range", "start_bar": 1, "end_bar": 4, "start_tick": 0, "end_tick": 100},
                "entry": {"kind": "bar", "bar": 1},
                "exit": {"kind": "tick", "tick": 10},
                "transition_ids": [],
            }
        ],
        initial_state_id="state-combat",
        default_state_id="state-combat",
    )
    assert any("bar and tick" in item.message for item in _finding_by(mixed, "timing_incompatible"))

    tick_only = _minimal_score(
        states=[
            {
                "id": "state-combat",
                "name": "Combat",
                "intensity": 0.4,
                "material": {"kind": "track_range", "track_ids": ["melody-1"], "start_tick": 0, "end_tick": 80},
                "entry": {"kind": "bar", "bar": 1},
                "exit": {"kind": "material_end"},
                "transition_ids": [],
            }
        ],
        initial_state_id="state-combat",
        default_state_id="state-combat",
    )
    assert any("tick-only" in item.message for item in _finding_by(tick_only, "timing_incompatible"))

    loop_after = _minimal_score(
        states=[
            {
                "id": "state-combat",
                "name": "Combat",
                "intensity": 0.4,
                "material": _bars(1, 8),
                "entry": {"kind": "bar", "bar": 2},
                "exit": {"kind": "bar", "bar": 6},
                "loop": {"enabled": True, "start_bar": 5, "end_bar": 6},
                "transition_ids": [],
            }
        ],
        initial_state_id="state-combat",
        default_state_id="state-combat",
    )
    assert any("loop start bar 5" in item.message for item in _finding_by(loop_after, "timing_incompatible"))


def _chain(*, loop_back: bool) -> AdaptiveScoreV1:
    victory_ids = ["to-explore"] if loop_back else []
    transitions = [
        {
            "id": "to-combat",
            "from_state_id": "state-exploration",
            "to_state_id": "state-combat",
            "quantization": "bar",
            "conditions": [{"kind": "manual"}],
        },
        {
            "id": "to-victory",
            "from_state_id": "state-combat",
            "to_state_id": "state-victory",
            "quantization": "bar",
            "conditions": [],
        },
    ]
    if loop_back:
        transitions.append(
            {
                "id": "to-explore",
                "from_state_id": "state-victory",
                "to_state_id": "state-exploration",
                "quantization": "bar",
                "conditions": [{"kind": "manual"}],
            }
        )
    return _minimal_score(
        states=[
            {
                "id": "state-exploration",
                "name": "Exploration",
                "intensity": 0.2,
                "material": _bars(),
                "transition_ids": ["to-combat"],
            },
            {
                "id": "state-combat",
                "name": "Combat",
                "intensity": 0.5,
                "material": _bars(),
                "transition_ids": ["to-victory"],
            },
            {
                "id": "state-victory",
                "name": "Victory",
                "intensity": 0.3,
                "material": _bars(),
                "transition_ids": victory_ids,
            },
        ],
        transitions=transitions,
    )


def test_bar_cycle_still_saves_and_disabled_loop_end_is_impossible() -> None:
    score = parse_adaptive_score(adventure_score())
    assert "impossible_transition" not in {item.code for item in validate_adaptive_score_graph(score)}
    payload = adventure_score()
    payload["transitions"][0]["quantization"] = "loop_end"
    looped = parse_adaptive_score(payload)
    hits = [
        item
        for item in validate_adaptive_score_graph(looped)
        if item.code == "impossible_transition"
    ]
    assert hits
    assert "state-exploration" in hits[0].message
    assert "disabled" in hits[0].message


def test_phrase_unaligned_is_a_warning_until_strict() -> None:
    payload = adventure_score()
    payload["states"] = payload["states"][:2]
    payload["states"][0]["material"] = {"kind": "motif", "motif_id": "motif-explore"}
    payload["states"][0]["transition_ids"] = ["to-suspense"]
    payload["states"][1]["transition_ids"] = []
    payload["variants"] = []
    payload["layers"] = []
    payload["stingers"] = []
    payload["transitions"] = [
        {
            "id": "to-suspense",
            "from_state_id": "state-exploration",
            "to_state_id": "state-suspense",
            "quantization": "phrase",
            "conditions": [{"kind": "manual"}],
        }
    ]
    score = parse_adaptive_score(payload)
    composition = _composition(bar_count=4)
    warnings = bind_material_refs(score, composition)
    phrase = [item for item in warnings if item.code == "phrase_unaligned"]
    assert len(phrase) == 1
    assert phrase[0].severity == "warning"
    strict = bind_material_refs(score, composition, strict=True)
    promoted = [item for item in strict if item.code == "phrase_unaligned"]
    assert promoted[0].severity == "error"


def test_phrase_material_on_a_second_revision_is_mixed() -> None:
    payload = adventure_score()
    payload["states"][0]["material"]["revision_id"] = "rev-a"
    payload["transitions"][0]["realization"] = {
        "kind": "phrase",
        "phrase_material": {
            "kind": "revision_region",
            "revision_id": "rev-b",
            "start_bar": 1,
            "end_bar": 2,
        },
    }
    score = parse_adaptive_score(payload)
    codes = {item.code for item in validate_adaptive_score_graph(score)}
    assert "mixed_revision_targets" in codes


def _locked_intensity_layers() -> list[dict]:
    rows = [
        ("layer-pad", "ambient", 0, 1, None, 0),
        ("layer-piano", "harmony", 0, 1, None, 0),
        ("layer-bass", "bass", 0.5, 1, None, 0),
        ("layer-strings", "strings", 0.5, 1, None, 0),
        ("layer-perc", "percussion", 0.8, 1, None, 0),
        ("layer-brass-hint", "brass", 0.9, 1, "orchestration", 0),
        ("layer-orch", "brass", 1, 1, "orchestration", 1),
    ]
    layers = []
    for layer_id, role, low, high, group, priority in rows:
        layers.append(
            {
                "id": layer_id,
                "name": layer_id,
                "state_id": "state-exploration",
                "material": {"kind": "section", "section_id": "section-explore"},
                "intensity_min": low,
                "intensity_max": high,
                "mix_hint": "bed",
                "role": role,
                "exclusive_group": group,
                "priority": priority,
                "fade": (
                    {"in_policy": "bar", "out_policy": "cut"}
                    if layer_id == "layer-orch"
                    else {"in_policy": "cut", "out_policy": "cut"}
                ),
            }
        )
    return layers


def test_locked_layer_fixture_validates_and_reports_layer_count(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    payload = {
        "schema_version": "adaptive.score.v1",
        "name": "Intensity cue",
        "initial_state_id": "state-exploration",
        "default_state_id": "state-exploration",
        "states": [
            {
                "id": "state-exploration",
                "name": "Exploration",
                "intensity": 0,
                "material": {"kind": "section", "section_id": "section-explore"},
            }
        ],
        "layers": _locked_intensity_layers(),
    }
    score = parse_adaptive_score(payload)
    findings = validate_adaptive_score_graph(score)
    assert [item for item in findings if item.severity == "error"] == []
    assert len(score.layers) == 7
    assert any(getattr(record, "layer_count", None) == 7 for record in caplog.records)
    assert "layer-orch" not in " ".join(record.getMessage() for record in caplog.records)


def test_bypassed_exclusive_group_is_invalid() -> None:
    payload = {
        "schema_version": "adaptive.score.v1",
        "name": "Intensity cue",
        "states": [
            {
                "id": "state-exploration",
                "name": "Exploration",
                "intensity": 0,
                "material": {"kind": "section", "section_id": "section-explore"},
            }
        ],
        "layers": [_locked_intensity_layers()[0]],
    }
    score = parse_adaptive_score(payload)
    layer = score.layers[0].model_copy(deep=True)
    object.__setattr__(layer, "exclusive_group", "Orchestration")
    bypassed = score.model_copy(update={"layers": [layer]})
    codes = {
        item.code
        for item in validate_adaptive_score_graph(bypassed)
        if item.target_id == "layer-pad"
    }
    assert "adaptive_score_invalid" in codes

