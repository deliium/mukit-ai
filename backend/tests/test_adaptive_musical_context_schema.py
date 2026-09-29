"""Schema tests for adaptive musical context documents."""

from __future__ import annotations

import logging

import pytest

from app.adaptive_musical_context_schemas import (
    AdaptiveScoreError,
    CONTEXT_SCHEMA_MESSAGES,
    parse_adaptive_context_external,
    parse_adaptive_context_mapping,
    parse_adaptive_musical_context,
)
from app.adaptive_musical_context_settings import load_adaptive_musical_context_settings
import app.adaptive_musical_context_settings as settings_module


def _combat_band(*, enter: float, exit: float) -> dict:
    return {
        "schema_version": "adaptive.context.mapping.v1",
        "baseline_state_id": "state-exploration",
        "bindings": [
            {
                "id": "bind_danger",
                "kind": "numeric",
                "external_key": "danger",
                "slot": "danger",
                "transform": "identity",
                "smooth_alpha": 1,
            }
        ],
        "state_rules": [
            {
                "id": "rule_combat",
                "kind": "numeric_band",
                "slot": "danger",
                "polarity": "high",
                "enter": enter,
                "exit": exit,
                "min_dwell_samples": 3,
                "target_state_id": "state-combat",
                "priority": 10,
            }
        ],
    }


def _snapshot(**overrides: object) -> dict:
    body = {
        "schema_version": "adaptive.musical_context.v1",
        "context_id": "actx_0123abcd",
        "sample_index": 0,
        "state": None,
        "intensity": None,
        "tension": None,
        "location": None,
        "health": None,
        "danger": None,
        "narrative_tags": [],
        "characters": [],
        "custom_numeric": {},
        "custom_boolean": {},
        "custom_categorical": {},
        "musical_state_id": "state-exploration",
        "emitted": [],
        "dwell_rule_id": None,
        "dwell_count": 0,
        "warnings": [],
        "telemetry": {
            "sample_count": 0,
            "state_change_count": 0,
            "intensity_emit_count": 0,
            "rejected_sample_count": 0,
        },
        "document_revision": 1,
    }
    body.update(overrides)
    return body


def test_unsupported_schema_versions_name_the_context_documents() -> None:
    with pytest.raises(AdaptiveScoreError) as external:
        parse_adaptive_context_external({"schema_version": "adaptive.score.v1", "values": {}})
    with pytest.raises(AdaptiveScoreError) as mapping:
        parse_adaptive_context_mapping({"schema_version": "game.v1", "baseline_state_id": "state-exploration"})
    with pytest.raises(AdaptiveScoreError) as snapshot:
        parse_adaptive_musical_context({"schema_version": "adaptive.score.v2"})
    messages = (
        external.value.message,
        mapping.value.message,
        snapshot.value.message,
    )
    assert messages == (
        CONTEXT_SCHEMA_MESSAGES["adaptive.context.external.v1"],
        CONTEXT_SCHEMA_MESSAGES["adaptive.context.mapping.v1"],
        CONTEXT_SCHEMA_MESSAGES["adaptive.musical_context.v1"],
    )
    for message in messages:
        assert "adaptive.score.v1" not in message


def test_extra_fields_boolean_numbers_and_nested_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    with pytest.raises(AdaptiveScoreError) as extra:
        parse_adaptive_context_external(
            {
                "schema_version": "adaptive.context.external.v1",
                "values": {},
                "scene": {"danger": 0.49},
            }
        )
    assert extra.value.code == "adaptive_score_invalid"
    with pytest.raises(AdaptiveScoreError) as boolean:
        parse_adaptive_context_mapping(
            {
                **_combat_band(enter=0.65, exit=0.35),
                "bindings": [
                    {
                        "id": "bind_danger",
                        "kind": "numeric",
                        "external_key": "danger",
                        "slot": "danger",
                        "smooth_alpha": True,
                    }
                ],
            }
        )
    assert boolean.value.code == "adaptive_score_invalid"
    with pytest.raises(AdaptiveScoreError) as nested:
        parse_adaptive_context_external(
            {
                "schema_version": "adaptive.context.external.v1",
                "values": {"danger": {"raw": 0.49}},
            }
        )
    assert nested.value.code == "adaptive_score_invalid"
    assert nested.value.details["field"] == "values"
    blob = " ".join(
        f"{record.getMessage()} {getattr(record, 'code', '')} {getattr(record, 'field', '')}"
        for record in caplog.records
        if record.levelno <= logging.DEBUG
    )
    assert "0.49" not in blob
    assert "{" not in blob


def test_note_like_keys_are_rejected_before_a_session_exists() -> None:
    with pytest.raises(AdaptiveScoreError) as captured:
        parse_adaptive_context_external(
            {
                "schema_version": "adaptive.context.external.v1",
                "values": {"events": [60]},
            }
        )
    assert captured.value.code == "embedded_note_material"
    assert captured.value.details["field"] == "values"


def test_narrow_band_is_hysteresis_gap_and_locked_band_is_legal() -> None:
    with pytest.raises(AdaptiveScoreError) as captured:
        parse_adaptive_context_mapping(_combat_band(enter=0.5, exit=0.5))
    assert captured.value.code == "hysteresis_gap"
    mapping = parse_adaptive_context_mapping(_combat_band(enter=0.65, exit=0.35))
    assert mapping.state_rules[0].target_state_id == "state-combat"
    assert mapping.baseline_state_id == "state-exploration"


def test_duplicate_danger_slot_is_invalid_and_returns_nothing() -> None:
    body = _combat_band(enter=0.65, exit=0.35)
    body["bindings"] = [
        {
            "id": "bind_danger",
            "kind": "numeric",
            "external_key": "danger",
            "slot": "danger",
        },
        {
            "id": "bind_danger_again",
            "kind": "numeric",
            "external_key": "threat",
            "slot": "danger",
        },
    ]
    with pytest.raises(AdaptiveScoreError) as captured:
        parse_adaptive_context_mapping(body)
    assert captured.value.code == "adaptive_score_invalid"
    assert captured.value.details["field"] == "bindings"


def test_tag_and_character_caps() -> None:
    tags = [f"tag_{index}" for index in range(33)]
    with pytest.raises(AdaptiveScoreError) as tags_captured:
        parse_adaptive_musical_context(_snapshot(narrative_tags=sorted(tags)))
    assert tags_captured.value.code == "adaptive_score_too_large"
    characters = [{"id": f"hero_{index}", "present": True} for index in range(33)]
    with pytest.raises(AdaptiveScoreError) as characters_captured:
        parse_adaptive_musical_context(_snapshot(characters=characters))
    assert characters_captured.value.code == "adaptive_score_too_large"


def test_settings_keep_the_default_when_env_is_not_an_integer(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    settings = load_adaptive_musical_context_settings({"ADAPTIVE_CONTEXT_MAX_BINDINGS": "nope"})
    assert settings.max_bindings == 32
    warning = next(record for record in caplog.records if record.levelno == logging.WARNING)
    assert getattr(warning, "setting", None) == "ADAPTIVE_CONTEXT_MAX_BINDINGS"
    assert getattr(warning, "invalid", None) is True
    assert "danger" not in warning.getMessage()
    settings_module._LOGGED_CAPS = False
    caplog.set_level(logging.DEBUG)
    loaded = load_adaptive_musical_context_settings()
    assert loaded.default_dwell == 3
    assert any(getattr(record, "max_bindings", None) == loaded.max_bindings for record in caplog.records)
