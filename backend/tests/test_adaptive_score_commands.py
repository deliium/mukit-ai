"""Pure adaptive-score command tests. No database."""

from __future__ import annotations

import logging
import re

import pytest

from app.adaptive_score_schemas import (
    AdaptiveScoreError,
    parse_adaptive_score,
    parse_adaptive_score_command,
)
from app.services.adaptive_score_commands import apply_adaptive_score_command


def _empty():
    return parse_adaptive_score(
        {"schema_version": "adaptive.score.v1", "name": "Exploration cue"}
    )


def _apply(score, op: str, payload: dict, revision: int = 1):
    command = parse_adaptive_score_command(
        {"expected_document_revision": revision, "op": op, "payload": payload}
    )
    return apply_adaptive_score_command(score, command)


def test_each_operation_updates_only_the_named_field() -> None:
    score = _apply(_empty(), "create_state", {"name": "Exploration", "id": "state-exploration"})
    assert score.initial_state_id == "state-exploration"
    assert score.default_state_id == "state-exploration"
    assert score.states[0].material.kind == "bar_range"
    assert score.states[0].material.start_bar == 1
    assert score.states[0].intensity == 0

    with_material = _apply(
        score,
        "assign_material",
        {
            "state_id": "state-exploration",
            "material": {"kind": "section", "section_id": "section-1"},
        },
    )
    assert with_material.states[0].material.section_id == "section-1"

    combat = _apply(with_material, "create_state", {"name": "Combat", "id": "state-combat"})
    assert combat.initial_state_id == "state-exploration"
    linked = _apply(
        combat,
        "create_transition",
        {
            "id": "to-combat",
            "from_state_id": "state-exploration",
            "to_state_id": "state-combat",
            "quantization": "bar",
            "conditions": [{"kind": "manual"}],
        },
    )
    assert linked.states[0].transition_ids == ["to-combat"]
    edited = _apply(
        linked,
        "edit_transition",
        {"transition_id": "to-combat", "quantization": "beat", "priority": 3},
    )
    assert edited.transitions[0].quantization == "beat"
    assert edited.transitions[0].priority == 3
    assert edited.transitions[0].id == "to-combat"
    looped = _apply(
        edited,
        "assign_loop",
        {"state_id": "state-combat", "enabled": True, "start_bar": 1, "end_bar": 2},
    )
    assert looped.states[1].loop.enabled is True
    intense = _apply(looped, "assign_intensity", {"state_id": "state-combat", "intensity": 0.4})
    assert intense.states[1].intensity == 0.4
    bounded = _apply(
        intense,
        "assign_boundary",
        {"state_id": "state-combat", "which": "entry", "boundary": {"kind": "bar", "bar": 1}},
    )
    assert bounded.states[1].entry.kind == "bar"
    assert bounded.states[1].entry.bar == 1


def test_unknown_state_and_duplicate_id_do_not_mutate() -> None:
    score = _apply(_empty(), "create_state", {"name": "Exploration", "id": "state-exploration"})
    with pytest.raises(AdaptiveScoreError) as missing:
        _apply(score, "assign_intensity", {"state_id": "state-missing", "intensity": 0.2})
    assert missing.value.code == "dangling_state_ref"
    assert missing.value.details["target_id"] == "state-missing"
    with pytest.raises(AdaptiveScoreError) as duplicate:
        _apply(score, "create_state", {"name": "Again", "id": "state-exploration"})
    assert duplicate.value.code == "duplicate_id"
    with pytest.raises(AdaptiveScoreError) as missing_edge:
        _apply(
            score,
            "edit_transition",
            {"transition_id": "tran-missing", "priority": 1},
        )
    assert missing_edge.value.code == "adaptive_score_invalid"
    assert score.states[0].name == "Exploration"


def test_delete_removes_transitions_and_variants() -> None:
    score = _apply(_empty(), "create_state", {"name": "Exploration", "id": "state-exploration"})
    score = _apply(score, "create_state", {"name": "Combat", "id": "state-combat"})
    score = _apply(
        score,
        "create_transition",
        {
            "id": "to-combat",
            "from_state_id": "state-exploration",
            "to_state_id": "state-combat",
            "quantization": "bar",
        },
    )
    raw = score.model_dump(mode="json")
    raw["variants"] = [
        {
            "id": "variant-combat",
            "state_id": "state-combat",
            "name": "High",
            "material": {"kind": "bar_range", "start_bar": 1, "end_bar": 1},
            "intensity_min": 0.2,
            "intensity_max": 0.8,
        }
    ]
    raw["layers"] = [
        {
            "id": "layer-bed",
            "name": "Bed",
            "state_id": "state-combat",
            "material": {"kind": "bar_range", "start_bar": 1, "end_bar": 1},
            "intensity_min": 0,
            "intensity_max": 1,
            "mix_hint": "bed",
            "default_active": False,
        }
    ]
    score = parse_adaptive_score(raw)
    deleted = _apply(score, "delete_state", {"state_id": "state-combat"})
    assert [state.id for state in deleted.states] == ["state-exploration"]
    assert deleted.transitions == []
    assert deleted.states[0].transition_ids == []
    assert deleted.variants == []
    assert deleted.layers[0].state_id is None
    assert deleted.initial_state_id == "state-exploration"


def test_first_state_becomes_initial_and_default() -> None:
    updated = _apply(_empty(), "create_state", {"name": "Exploration"})
    assert updated.states[0].id.startswith("state_")
    assert updated.initial_state_id == updated.states[0].id
    assert updated.default_state_id == updated.states[0].id


def test_duplicate_does_not_copy_transition_ids() -> None:
    score = _apply(_empty(), "create_state", {"name": "Exploration", "id": "state-exploration"})
    score = _apply(score, "create_state", {"name": "Combat", "id": "state-combat"})
    score = _apply(
        score,
        "create_transition",
        {
            "id": "to-combat",
            "from_state_id": "state-exploration",
            "to_state_id": "state-combat",
            "quantization": "bar",
        },
    )
    copied = _apply(score, "duplicate_state", {"state_id": "state-exploration"})
    assert copied.states[-1].transition_ids == []
    assert copied.states[-1].name == "Exploration copy"
    assert copied.initial_state_id == "state-exploration"
    assert copied.states[-1].id != "state-exploration"


def test_edit_transition_rejects_missing_endpoint() -> None:
    score = _apply(_empty(), "create_state", {"name": "Exploration", "id": "state-exploration"})
    score = _apply(score, "create_state", {"name": "Combat", "id": "state-combat"})
    score = _apply(
        score,
        "create_transition",
        {
            "id": "to-combat",
            "from_state_id": "state-exploration",
            "to_state_id": "state-combat",
            "quantization": "bar",
        },
    )
    with pytest.raises(AdaptiveScoreError) as captured:
        _apply(score, "edit_transition", {"transition_id": "to-combat", "to_state_id": "state-missing"})
    assert captured.value.code == "dangling_state_ref"


def test_forbidden_note_key_rejected() -> None:
    with pytest.raises(AdaptiveScoreError) as captured:
        parse_adaptive_score_command(
            {
                "expected_document_revision": 1,
                "op": "assign_material",
                "payload": {
                    "state_id": "state-exploration",
                    "material": {"kind": "bar_range", "start_bar": 1, "end_bar": 1, "pitch": 60},
                },
            }
        )
    assert captured.value.code == "embedded_note_material"


def test_unknown_op_is_invalid() -> None:
    with pytest.raises(AdaptiveScoreError) as captured:
        parse_adaptive_score_command(
            {"expected_document_revision": 1, "op": "play_state", "payload": {}}
        )
    assert captured.value.code == "adaptive_score_invalid"


def test_create_cue_and_edit_keeps_realization() -> None:
    score = _apply(_empty(), "create_state", {"name": "Exploration", "id": "state-exploration"})
    score = _apply(score, "create_state", {"name": "Combat", "id": "state-combat"})
    created = _apply(
        score,
        "create_transition",
        {
            "id": "to-combat",
            "from_state_id": "state-exploration",
            "to_state_id": "state-combat",
            "quantization": "cue",
            "cue_label": "Hit",
            "conditions": [{"kind": "manual"}],
        },
    )
    assert created.transitions[0].quantization == "cue"
    assert created.transitions[0].cue_label == "Hit"
    realized = _apply(
        created,
        "edit_transition",
        {
            "transition_id": "to-combat",
            "realization": {"kind": "crossfade", "crossfade_ms": 250},
        },
    )
    edited = _apply(
        realized,
        "edit_transition",
        {"transition_id": "to-combat", "priority": 4},
    )
    assert edited.transitions[0].priority == 4
    assert edited.transitions[0].realization.kind == "crossfade"
    assert edited.transitions[0].realization.crossfade_ms == 250
    assert edited.transitions[0].cue_label == "Hit"


_LAYER = {
    "name": "Pad bed",
    "material": {"kind": "section", "section_id": "section-pad"},
    "state_id": "state-exploration",
    "intensity_min": 0,
    "intensity_max": 1,
    "mix_hint": "bed",
    "role": "ambient",
}


def test_layer_create_edit_and_delete(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    score = _apply(_empty(), "create_state", {"name": "Exploration", "id": "state-exploration"})
    created = _apply(score, "create_layer", _LAYER)
    assert len(created.layers) == 1
    layer_id = created.layers[0].id
    assert re.fullmatch(r"layer_[0-9a-f]{8}", layer_id)
    assert created.layers[0].material.section_id == "section-pad"
    assert any(getattr(record, "layer_count", None) == 1 for record in caplog.records)
    assert "Pad bed" not in " ".join(record.getMessage() for record in caplog.records)

    edited = _apply(created, "edit_layer", {"layer_id": layer_id, "intensity_min": 0.2})
    assert edited.layers[0].intensity_min == 0.2
    assert edited.layers[0].material.section_id == "section-pad"

    grouped = _apply(
        edited, "edit_layer", {"layer_id": layer_id, "exclusive_group": "orchestration"}
    )
    assert grouped.layers[0].exclusive_group == "orchestration"
    cleared = _apply(grouped, "edit_layer", {"layer_id": layer_id, "exclusive_group": None})
    assert cleared.layers[0].exclusive_group is None

    faded = _apply(
        cleared,
        "edit_layer",
        {"layer_id": layer_id, "fade": {"in_policy": "linear", "fade_in_ms": 400}},
    )
    assert faded.layers[0].fade.in_policy == "linear"
    assert faded.layers[0].fade.fade_in_ms == 400
    kept = _apply(faded, "edit_layer", {"layer_id": layer_id, "priority": 2})
    assert kept.layers[0].priority == 2
    assert kept.layers[0].fade.in_policy == "linear"
    assert kept.layers[0].fade.fade_in_ms == 400

    with pytest.raises(AdaptiveScoreError) as invalid:
        _apply(kept, "edit_layer", {"layer_id": layer_id, "intensity_max": 0.1})
    assert invalid.value.code == "adaptive_score_invalid"
    assert kept.layers[0].intensity_max == 1
    assert kept.layers[0].fade.in_policy == "linear"

    removed = _apply(kept, "delete_layer", {"layer_id": layer_id})
    assert removed.layers == []
    assert removed.states[0].id == "state-exploration"


def test_layer_command_rejections_do_not_write() -> None:
    score = _apply(_empty(), "create_state", {"name": "Exploration", "id": "state-exploration"})
    with pytest.raises(AdaptiveScoreError) as dangling:
        _apply(score, "create_layer", {**_LAYER, "state_id": "state-missing"})
    assert dangling.value.code == "dangling_state_ref"
    assert score.layers == []

    created = _apply(score, "create_layer", _LAYER)
    layer_id = created.layers[0].id
    with pytest.raises(AdaptiveScoreError) as missing:
        _apply(created, "delete_layer", {"layer_id": "layer_missing"})
    assert missing.value.code == "adaptive_score_invalid"
    assert missing.value.details["target_id"] == "layer_missing"
    assert created.layers[0].id == layer_id

    with pytest.raises(AdaptiveScoreError) as embedded:
        parse_adaptive_score_command(
            {
                "expected_document_revision": 1,
                "op": "create_layer",
                "payload": {**_LAYER, "events": [{"pitch": 60}]},
            }
        )
    assert embedded.value.code == "embedded_note_material"
    assert created.layers[0].id == layer_id

