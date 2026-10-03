"""Fan-out orchestration service for ensemble arbitration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ai_runtime.registry import reload_registry
from app.ensemble_arbitration_schemas import (
    FAKE_ENSEMBLE_MODEL_IDS,
    EnsembleArbitrationError,
)
from app.ensemble_arbitration_settings import load_ensemble_arbitration_settings
from app.services.ensemble_arbitration_service import run_ensemble_arbitration_preview

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "composition_plan"
planner_calls: list[object] = []


def _plan() -> dict:
    return json.loads((FIXTURES / "valid_minimal.json").read_text(encoding="utf-8"))


def _constraints() -> dict:
    return {
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
        "allow_extra_instrument_families": True,
        "complexity": "simple",
    }


def _request(**overrides) -> dict:
    payload = {
        "schema_version": "ensemble.arbitration.request.v1",
        "policy": {
            "schema_version": "ensemble.policy.v1",
            "model_ids": list(FAKE_ENSEMBLE_MODEL_IDS),
            "strategy": "parallel_once",
            "selection_mode": "human",
            "top_n": 2,
            "base_seed": 5,
            "execution": "sequential",
        },
        "plan": _plan(),
        "constraints": _constraints(),
    }
    payload.update(overrides)
    return payload


@pytest.fixture(autouse=True)
def _fake_registry(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    monkeypatch.setenv("ENSEMBLE_ARBITRATION_ENABLED", "1")
    reload_registry()
    yield


def test_three_fake_preview_completes_without_planner():
    settings = load_ensemble_arbitration_settings(
        {"ENSEMBLE_ARBITRATION_ENABLED": "1", "ENSEMBLE_MAX_MODELS": "3"}
    )
    report = run_ensemble_arbitration_preview(
        _request(),
        settings=settings,
        planner_hook=None,
    )
    assert report.musical_quality_claim is False
    assert len(report.candidates) >= 2
    echoed = {c.provenance.model_id for c in report.candidates}
    assert echoed.issubset(set(FAKE_ENSEMBLE_MODEL_IDS))
    for candidate in report.candidates:
        assert candidate.provenance.model_id in FAKE_ENSEMBLE_MODEL_IDS


def test_planner_hook_refused():
    settings = load_ensemble_arbitration_settings({"ENSEMBLE_ARBITRATION_ENABLED": "1"})

    def _planner():
        planner_calls.append(1)

    with pytest.raises(EnsembleArbitrationError) as exc:
        run_ensemble_arbitration_preview(
            _request(),
            settings=settings,
            planner_hook=_planner,
        )
    assert exc.value.code == "ensemble_payload_refused"
    assert planner_calls == []


def test_unready_model_rejected_others_survive():
    settings = load_ensemble_arbitration_settings({"ENSEMBLE_ARBITRATION_ENABLED": "1"})
    body = _request()
    body["policy"]["model_ids"] = [
        "fake:symbolic-tiny",
        "fake:symbolic-dense",
        "missing:not-a-model",
    ]
    report = run_ensemble_arbitration_preview(body, settings=settings)
    rejected = [r for r in report.rejected_attempts if r.model_id == "missing:not-a-model"]
    assert rejected
    assert rejected[0].stage == "generate"
    assert rejected[0].code == "ensemble_model_unready"
    assert len(report.candidates) >= 1


def test_model_limit_raises():
    settings = load_ensemble_arbitration_settings(
        {"ENSEMBLE_ARBITRATION_ENABLED": "1", "ENSEMBLE_MAX_MODELS": "3"}
    )
    body = _request()
    # Schema hard-max is 4; service settings max is 3 — use 4 distinct valid-shaped ids.
    body["policy"]["model_ids"] = [
        "fake:symbolic-tiny",
        "fake:symbolic-sparse",
        "fake:symbolic-dense",
        "fake:symbolic-extra",
    ]
    with pytest.raises(EnsembleArbitrationError) as exc:
        run_ensemble_arbitration_preview(body, settings=settings)
    assert exc.value.code == "ensemble_model_limit"


def test_disabled_raises():
    settings = load_ensemble_arbitration_settings({"ENSEMBLE_ARBITRATION_ENABLED": "0"})
    with pytest.raises(EnsembleArbitrationError) as exc:
        run_ensemble_arbitration_preview(_request(), settings=settings)
    assert exc.value.code == "ensemble_arbitration_disabled"
    assert exc.value.http_status == 503
