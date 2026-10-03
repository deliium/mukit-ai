"""Unit tests for ensemble arbitration schemas and settings."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.ensemble_arbitration_schemas import (
    FAKE_ENSEMBLE_MODEL_IDS,
    EnsembleArbitrationRequestV1,
    EnsembleArbitrationV1,
    EnsembleHardConstraintsV1,
    EnsemblePolicyV1,
    parse_ensemble_arbitration_request,
    reject_ensemble_payload,
    EnsembleArbitrationError,
)
from app.ensemble_arbitration_settings import (
    hard_max_models,
    load_ensemble_arbitration_settings,
    min_models,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "composition_plan"


def _minimal_plan() -> dict:
    return json.loads((FIXTURES / "valid_minimal.json").read_text(encoding="utf-8"))


def _minimal_constraints(**overrides) -> dict:
    payload = {
        "key": "C major",
        "key_user_specified": True,
        "time_signature": "4/4",
        "duration_bars": 8,
        "tempo_min": 100,
        "tempo_max": 140,
        "sections": [
            {"type": "intro", "start_bar": 1, "bar_count": 2},
            {"type": "verse", "start_bar": 3, "bar_count": 4},
            {"type": "outro", "start_bar": 7, "bar_count": 2},
        ],
        "sections_user_specified": True,
        "required_instrument_families": ["piano", "bass"],
        "requested_instruments": ["piano", "bass"],
        "allow_extra_instrument_families": False,
        "mood": "warm",
        "genre": "ballad",
        "complexity": "simple",
    }
    payload.update(overrides)
    return payload


def _policy(**overrides) -> dict:
    payload = {
        "schema_version": "ensemble.policy.v1",
        "model_ids": list(FAKE_ENSEMBLE_MODEL_IDS),
        "strategy": "parallel_once",
        "selection_mode": "human",
        "top_n": 2,
        "base_seed": 7,
        "execution": "sequential",
    }
    payload.update(overrides)
    return payload


def _request(**overrides) -> dict:
    payload = {
        "schema_version": "ensemble.arbitration.request.v1",
        "policy": _policy(),
        "plan": _minimal_plan(),
        "constraints": _minimal_constraints(),
    }
    payload.update(overrides)
    return payload


def test_policy_accepts_three_fake_models_human():
    policy = EnsemblePolicyV1.model_validate(_policy())
    assert policy.model_ids == list(FAKE_ENSEMBLE_MODEL_IDS)
    assert policy.selection_mode == "human"
    assert policy.strategy == "parallel_once"


def test_request_accepts_plan_constraints_and_policy():
    body = EnsembleArbitrationRequestV1.model_validate(_request())
    assert body.plan.form.bar_count == 8
    constraints = body.constraints.to_generation_constraints()
    assert constraints.duration_bars == 8
    assert constraints.key == "C major"
    assert constraints.key_user_specified is True


def test_policy_rejects_one_model():
    with pytest.raises(ValidationError) as exc:
        EnsemblePolicyV1.model_validate(_policy(model_ids=["fake:symbolic-tiny"]))
    assert "at least" in str(exc.value).lower() or "ensemble_model_limit" in str(
        exc.value
    )


def test_policy_rejects_five_models_when_hard_max_is_four():
    ids = [f"fake:symbolic-{i}" for i in range(5)]
    assert hard_max_models() == 4
    with pytest.raises(ValidationError):
        EnsemblePolicyV1.model_validate(_policy(model_ids=ids))


def test_policy_rejects_unknown_strategy():
    with pytest.raises(ValidationError):
        EnsemblePolicyV1.model_validate(_policy(strategy="seed_sweep"))


def test_policy_rejects_top_n_zero():
    with pytest.raises(ValidationError):
        EnsemblePolicyV1.model_validate(
            _policy(selection_mode="top_n", top_n=0)
        )


def test_request_rejects_missing_plan():
    payload = _request()
    del payload["plan"]
    with pytest.raises(ValidationError):
        EnsembleArbitrationRequestV1.model_validate(payload)


def test_request_rejects_embedded_command_key():
    payload = _request()
    payload["command"] = "rm -rf /"
    with pytest.raises((ValidationError, EnsembleArbitrationError)):
        parse_ensemble_arbitration_request(payload)


def test_reject_payload_command_shaped_body():
    with pytest.raises(EnsembleArbitrationError) as exc:
        reject_ensemble_payload({"command": "rm -rf /"})
    assert exc.value.code == "ensemble_payload_refused"


def test_policy_rejects_duplicate_model_ids():
    with pytest.raises(ValidationError) as exc:
        EnsemblePolicyV1.model_validate(
            _policy(
                model_ids=[
                    "fake:symbolic-tiny",
                    "fake:symbolic-tiny",
                    "fake:symbolic-dense",
                ]
            )
        )
    assert "duplicate" in str(exc.value).lower()


def test_arbitration_rejects_musical_quality_claim_true():
    # Construct via model_validate so Literal[False] is enforced.
    with pytest.raises(ValidationError):
        EnsembleArbitrationV1.model_validate(
            {
                "schema_version": "ensemble.arbitration.v1",
                "policy": _policy(),
                "candidates": [],
                "rejected_attempts": [],
                "musical_quality_claim": True,
                "critic_is_subjective_layer": True,
                "ranking_is_preference_not_quality": True,
                "wall_ms": 0,
            }
        )


def test_hard_constraints_to_generation_constraints():
    dto = EnsembleHardConstraintsV1.model_validate(_minimal_constraints())
    gc = dto.to_generation_constraints()
    assert gc.duration_bars == 8
    assert gc.sections is not None
    assert len(gc.sections) == 3
    assert gc.required_instrument_families == ("piano", "bass")


def test_settings_load_defaults_disabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("ENSEMBLE_ARBITRATION_ENABLED", raising=False)
    monkeypatch.delenv("ENSEMBLE_MAX_MODELS", raising=False)
    monkeypatch.delenv("ENSEMBLE_ALLOW_PARALLEL", raising=False)
    settings = load_ensemble_arbitration_settings({})
    assert settings.enabled is False
    assert settings.max_models == 3
    assert settings.allow_parallel is False
    assert settings.max_wall_ms == 120_000
    assert min_models() == 2


def test_settings_enabled_truthy(monkeypatch: pytest.MonkeyPatch):
    settings = load_ensemble_arbitration_settings(
        {
            "ENSEMBLE_ARBITRATION_ENABLED": "1",
            "ENSEMBLE_MAX_MODELS": "4",
            "ENSEMBLE_ALLOW_PARALLEL": "yes",
            "ENSEMBLE_MAX_WALL_MS": "30000",
        }
    )
    assert settings.enabled is True
    assert settings.max_models == 4
    assert settings.allow_parallel is True
    assert settings.max_wall_ms == 30_000
