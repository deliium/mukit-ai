"""Deterministic musical-context streams. No database and no playback clock."""

from __future__ import annotations

import ast
import logging
from pathlib import Path

import pytest

from app.adaptive_musical_context_schemas import parse_adaptive_context_external, parse_adaptive_context_mapping
from app.services.adaptive_musical_context import (
    begin_musical_context,
    musical_context_snapshot,
    step_musical_context,
)

_CONTEXT = "actx_0123abcd"


def _mapping(body: dict):
    return parse_adaptive_context_mapping(body)


def _sample(values: dict):
    return parse_adaptive_context_external(
        {"schema_version": "adaptive.context.external.v1", "values": values}
    )


def _locked() -> dict:
    return {
        "schema_version": "adaptive.context.mapping.v1",
        "baseline_state_id": "state-exploration",
        "bindings": [
            {
                "id": "bind-danger",
                "kind": "numeric",
                "external_key": "danger",
                "slot": "danger",
                "transform": "identity",
                "smooth_alpha": 1,
            }
        ],
        "state_rules": [
            {
                "id": "rule-combat",
                "kind": "numeric_band",
                "slot": "danger",
                "polarity": "high",
                "enter": 0.65,
                "exit": 0.35,
                "min_dwell_samples": 3,
                "target_state_id": "state-combat",
                "priority": 10,
            }
        ],
        "intensity": {"slot": "danger", "emit_epsilon": 0.02},
    }


def _run(mapping_body: dict, samples: list[dict], *, context_id: str = _CONTEXT):
    mapping = _mapping(mapping_body)
    clock = begin_musical_context(mapping, context_id=context_id, document_revision=4)
    rows = []
    for values in samples:
        clock = step_musical_context(mapping, clock, _sample(values))
        rows.append(musical_context_snapshot(clock))
    return rows


def _decision(snapshot) -> dict:
    return {
        "sample_index": snapshot.sample_index,
        "danger": snapshot.danger,
        "health": snapshot.health,
        "state": snapshot.state,
        "musical_state_id": snapshot.musical_state_id,
        "dwell_rule_id": snapshot.dwell_rule_id,
        "dwell_count": snapshot.dwell_count,
        "narrative_tags": snapshot.narrative_tags,
        "characters": [(row.id, row.present) for row in snapshot.characters],
        "ops": [item.op for item in snapshot.emitted],
        "state_targets": [
            item.to_state_id for item in snapshot.emitted if item.op == "request_state"
        ],
        "intensities": [item.intensity for item in snapshot.emitted if item.op == "set_intensity"],
        "flags": [item.flags for item in snapshot.emitted if item.op == "set_flags"],
        "warning_codes": [item.code for item in snapshot.warnings],
        "telemetry": snapshot.telemetry.model_dump(),
    }


def test_stream_a_does_not_flap() -> None:
    samples = [{"danger": 0.49 if index % 2 == 0 else 0.51} for index in range(20)]
    rows = _run(_locked(), samples)
    assert len(rows) == 20
    for row in rows:
        decision = _decision(row)
        assert decision["musical_state_id"] == "state-exploration"
        assert decision["telemetry"]["state_change_count"] == 0
        assert "request_state" not in decision["ops"]
        assert decision["dwell_count"] == 0


def test_stream_b_enters_combat_on_the_third_high_sample() -> None:
    rows = _run(_locked(), [{"danger": value} for value in (0.2, 0.7, 0.7, 0.7)])
    assert rows[0].musical_state_id == "state-exploration"
    assert rows[0].danger == 0.2
    assert _decision(rows[0])["intensities"] == [0.2]
    assert rows[0].dwell_count == 0
    assert rows[1].musical_state_id == "state-exploration"
    assert rows[1].dwell_count == 1
    assert rows[1].dwell_rule_id == "rule-combat"
    assert rows[2].dwell_count == 2
    assert rows[2].musical_state_id == "state-exploration"
    last = _decision(rows[3])
    assert last["musical_state_id"] == "state-combat"
    assert last["dwell_count"] == 3
    assert last["dwell_rule_id"] == "rule-combat"
    assert last["telemetry"]["state_change_count"] == 1
    assert last["state_targets"] == ["state-combat"]
    assert last["intensities"] == [0.7]


def test_stream_c_releases_only_after_three_low_samples() -> None:
    rows = _run(
        _locked(),
        [{"danger": value} for value in (0.7, 0.7, 0.7, 0.4, 0.3, 0.3, 0.3)],
    )
    assert rows[3].musical_state_id == "state-combat"
    assert rows[3].danger == 0.4
    assert rows[4].musical_state_id == "state-combat"
    assert rows[5].musical_state_id == "state-combat"
    assert rows[6].musical_state_id == "state-exploration"
    assert rows[6].dwell_count == 3
    assert rows[6].dwell_rule_id == "rule-combat"


def test_stream_d_repeats_from_a_fresh_session() -> None:
    samples = [{"danger": 0.49 if index % 2 == 0 else 0.51} for index in range(20)]
    first = _run(_locked(), samples, context_id="actx_aaaa1111")
    second = _run(_locked(), samples, context_id="actx_bbbb2222")
    assert first[-1].context_id == "actx_aaaa1111"
    assert {row.context_id for row in first} == {"actx_aaaa1111"}
    for left, right in zip(first, second, strict=True):
        left_body = left.model_dump()
        right_body = right.model_dump()
        assert left_body.pop("context_id") != right_body.pop("context_id")
        assert left_body == right_body


def test_stream_e_category_dwells_for_two_samples() -> None:
    mapping = {
        "schema_version": "adaptive.context.mapping.v1",
        "baseline_state_id": "state-exploration",
        "bindings": [
            {
                "id": "bind-mode",
                "kind": "categorical",
                "external_key": "mode",
                "slot": "state",
                "allowed": ["explore", "combat"],
            }
        ],
        "state_rules": [
            {
                "id": "rule-mode",
                "kind": "category_equals",
                "slot": "state",
                "equals": "combat",
                "min_dwell_samples": 2,
                "target_state_id": "state-combat",
                "priority": 5,
            }
        ],
    }
    rows = _run(mapping, [{"mode": "combat"}, {"mode": "combat"}])
    assert rows[0].musical_state_id == "state-exploration"
    assert rows[0].dwell_count == 1
    assert rows[0].state == "combat"
    assert rows[1].musical_state_id == "state-combat"


def test_stream_f_rejects_a_bad_tag_and_holds_the_list() -> None:
    mapping = {
        "schema_version": "adaptive.context.mapping.v1",
        "baseline_state_id": "state-exploration",
        "bindings": [{"id": "bind-tags", "kind": "tags", "external_key": "tags"}],
        "state_rules": [
            {
                "id": "rule-boss",
                "kind": "tag_present",
                "tag": "boss",
                "min_dwell_samples": 1,
                "target_state_id": "state-combat",
                "priority": 5,
            }
        ],
    }
    rows = _run(mapping, [{"tags": ["boss"]}, {"tags": ["explore", "Bad"]}])
    assert rows[0].musical_state_id == "state-combat"
    assert rows[0].narrative_tags == ["boss"]
    assert rows[1].narrative_tags == ["boss"]
    assert rows[1].warnings[0].code == "context_value_rejected"
    assert rows[1].musical_state_id == "state-combat"


def test_stream_g_and_h_character_and_flags() -> None:
    mapping = {
        "schema_version": "adaptive.context.mapping.v1",
        "baseline_state_id": "state-exploration",
        "bindings": [
            {
                "id": "bind-aria",
                "kind": "boolean",
                "external_key": "aria_present",
                "slot": "character",
                "character_id": "aria",
            }
        ],
        "state_rules": [
            {
                "id": "rule-aria",
                "kind": "character_present",
                "character_id": "aria",
                "min_dwell_samples": 1,
                "target_state_id": "state-combat",
                "priority": 5,
            }
        ],
        "flag_rules": [
            {"id": "flag-aria", "source": "character", "source_key": "aria", "flag": "aria_here"}
        ],
    }
    rows = _run(mapping, [{"aria_present": True}, {"aria_present": False}])
    assert rows[0].musical_state_id == "state-combat"
    assert _decision(rows[0])["flags"] == [{"aria_here": True}]
    assert rows[1].musical_state_id == "state-exploration"
    assert _decision(rows[1])["flags"] == [{}]


def test_stream_i_low_polarity_health() -> None:
    mapping = {
        "schema_version": "adaptive.context.mapping.v1",
        "baseline_state_id": "state-exploration",
        "bindings": [
            {
                "id": "bind-hp",
                "kind": "numeric",
                "external_key": "hp",
                "slot": "health",
                "transform": "identity",
                "smooth_alpha": 1,
            }
        ],
        "state_rules": [
            {
                "id": "rule-hurt",
                "kind": "numeric_band",
                "slot": "health",
                "polarity": "low",
                "enter": 0.25,
                "exit": 0.40,
                "min_dwell_samples": 2,
                "target_state_id": "state-combat",
                "priority": 5,
            }
        ],
    }
    rows = _run(
        mapping,
        [{"hp": value} for value in (0.50, 0.20, 0.20, 0.30, 0.50, 0.50)],
    )
    assert rows[0].musical_state_id == "state-exploration"
    assert rows[0].dwell_count == 0
    assert rows[1].musical_state_id == "state-exploration"
    assert rows[1].dwell_count == 1
    assert rows[2].musical_state_id == "state-combat"
    assert rows[3].musical_state_id == "state-combat"
    assert rows[3].dwell_count == 0
    assert rows[4].musical_state_id == "state-combat"
    assert rows[5].musical_state_id == "state-exploration"


def test_stream_j_ema_clamps_the_seed_and_rejects_strings() -> None:
    mapping = {
        "schema_version": "adaptive.context.mapping.v1",
        "baseline_state_id": "state-exploration",
        "bindings": [
            {
                "id": "bind-danger",
                "kind": "numeric",
                "external_key": "danger",
                "slot": "danger",
                "transform": "identity",
                "smooth_alpha": 0.5,
            }
        ],
        "state_rules": [],
    }
    rows = _run(mapping, [{"danger": 0}, {"danger": 1}])
    assert rows[0].danger == 0
    assert rows[1].danger == 0.5
    seeded = _run(mapping, [{"danger": 1.4}, {"danger": 0}])
    assert seeded[0].danger == 1.0
    assert seeded[1].danger == 0.5
    held = _run(mapping, [{"danger": 0}, {"danger": "0.7"}])
    assert held[1].danger == 0
    assert held[1].warnings[0].code == "context_value_rejected"


def test_unbound_state_and_ignored_keys_do_not_select_music(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    rows = _run(
        _locked(),
        [{"state": "state-combat"}, {"danger": 0.7, "quest_step": 4}],
    )
    assert rows[0].musical_state_id == "state-exploration"
    assert rows[0].state is None
    assert rows[1].musical_state_id == "state-exploration"
    assert rows[1].dwell_count == 1
    assert rows[1].danger == 0.7
    blob = caplog.text
    assert "quest_step" not in blob
    assert "boss" not in blob
    assert any(getattr(record, "danger", None) == 0.7 for record in caplog.records)
    same = _run(_locked(), [{"danger": 0.2}])
    again = _run(_locked(), [{"danger": 0.2}])
    assert _decision(same[0]) == _decision(again[0])


def test_module_does_not_import_playback_sqlite_fastapi_or_jam() -> None:
    path = Path(__file__).resolve().parents[1] / "app" / "services" / "adaptive_musical_context.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    joined = " ".join(modules)
    assert "adaptive_playback" not in joined
    assert "sqlite" not in joined
    assert "fastapi" not in joined
    assert "live" not in joined
    assert "ai_runtime" not in joined
    assert "fake_llm" not in joined
