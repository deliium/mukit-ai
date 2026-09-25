"""Env parsing for autonomous-run ceilings."""

from __future__ import annotations

import logging

from app.operation_budget_settings import load_operation_budget_settings


def test_defaults_when_env_empty() -> None:
    settings = load_operation_budget_settings({})
    assert settings.max_model_calls == 32
    assert settings.max_runtime_ms == 180_000
    assert settings.max_prompt_tokens is None
    assert settings.remote_cost_micros is None
    assert settings.max_revisions == 8
    assert settings.neural_audio_max_attempts == 2


def test_revision_ceiling_above_eight_is_stored_as_eight() -> None:
    settings = load_operation_budget_settings({"OPERATION_MAX_REVISIONS": "12"})
    assert settings.max_revisions == 8


def test_zero_model_calls_disables_the_ceiling() -> None:
    settings = load_operation_budget_settings({"OPERATION_MAX_MODEL_CALLS": "0"})
    assert settings.max_model_calls == 0


def test_invalid_values_fall_back_and_warn(caplog) -> None:
    caplog.set_level(logging.WARNING)
    settings = load_operation_budget_settings(
        {
            "OPERATION_MAX_MODEL_CALLS": "nope",
            "OPERATION_MAX_RUNTIME_MS": "-5",
            "OPERATION_MAX_PROMPT_TOKENS": "0",
            "NEURAL_AUDIO_MAX_ATTEMPTS": "9",
        }
    )
    assert settings.max_model_calls == 32
    assert settings.max_runtime_ms == 180_000
    assert settings.max_prompt_tokens is None
    assert settings.neural_audio_max_attempts == 2
    warned = {getattr(record, "env_key", None) for record in caplog.records}
    assert "OPERATION_MAX_MODEL_CALLS" in warned
    assert "OPERATION_MAX_RUNTIME_MS" in warned
    assert "NEURAL_AUDIO_MAX_ATTEMPTS" in warned
    for record in caplog.records:
        if record.levelno >= logging.WARNING:
            assert "nope" not in record.getMessage()


def test_optional_caps_parse() -> None:
    settings = load_operation_budget_settings(
        {
            "OPERATION_MAX_PROMPT_TOKENS": "1000",
            "OPERATION_REMOTE_COST_MICROS": "25",
            "NEURAL_AUDIO_MAX_ATTEMPTS": "5",
            "OPERATION_MAX_REVISIONS": "3",
        }
    )
    assert settings.max_prompt_tokens == 1000
    assert settings.remote_cost_micros == 25
    assert settings.neural_audio_max_attempts == 5
    assert settings.max_revisions == 3
