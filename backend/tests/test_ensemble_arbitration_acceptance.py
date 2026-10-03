"""Acceptance vector for controlled multi-model ensemble arbitration."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.ai_runtime.capabilities import ModelCapability
from app.ai_runtime.registry import list_models, reload_registry
from app.db import initialize_database
from app.db.connection import reset_database_initialization_cache
from app.ensemble_arbitration_schemas import FAKE_ENSEMBLE_MODEL_IDS
from app.ensemble_arbitration_settings import load_ensemble_arbitration_settings
from app.main import app
from app.preference_schemas import PREFERENCE_FEATURE_DIMS, PreferenceRankerV1
from app.services.ensemble_arbitration_pipeline import EnsembleGenerateAttempt
from app.services.ensemble_arbitration_service import run_ensemble_arbitration_preview
from app.services.fake_symbolic_composer import generate_fake_symbolic_composition
from app.services.preference_ranker import utc_now_iso
from app.composition_plan_schemas import parse_composition_plan

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "composition_plan"


def _plan_dict() -> dict:
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


def _preview_body(**overrides) -> dict:
    payload = {
        "schema_version": "ensemble.arbitration.request.v1",
        "policy": {
            "schema_version": "ensemble.policy.v1",
            "model_ids": list(FAKE_ENSEMBLE_MODEL_IDS),
            "strategy": "parallel_once",
            "selection_mode": "auto_suggest",
            "top_n": 2,
            "base_seed": 0,
            "execution": "sequential",
        },
        "plan": _plan_dict(),
        "constraints": _constraints(),
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def acceptance_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.setenv("ENSEMBLE_ARBITRATION_ENABLED", "1")
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    monkeypatch.setenv("COLLABORATION_ENABLED", "0")
    monkeypatch.setenv("PREFERENCE_LEARNING_ENABLED", "1")
    reset_database_initialization_cache()
    initialize_database()
    reload_registry()
    return db_path


def test_three_fake_models_listed_on_ai_models(acceptance_env):
    models = list_models(capability=ModelCapability.SYMBOLIC_COMPOSER, status="ready")
    ids = {item.id for item in models}
    for model_id in FAKE_ENSEMBLE_MODEL_IDS:
        assert model_id in ids


def test_acceptance_happy_path_with_injected_invalid_and_rank(acceptance_env, monkeypatch, caplog):
    plan = parse_composition_plan(_plan_dict())
    real_run = __import__(
        "app.services.ensemble_arbitration_service", fromlist=["_run_one_attempt"]
    )._run_one_attempt

    def _run_with_invalid(**kwargs):
        attempt = real_run(**kwargs)
        if kwargs["model_id"] == "fake:symbolic-sparse":
            return EnsembleGenerateAttempt(
                model_id=kwargs["model_id"],
                seed=kwargs["seed"],
                attempt_ordinal=kwargs["attempt_ordinal"],
                strategy=kwargs["strategy"],
                composition={"schema_version": "composition.v2", "tracks": "broken"},
            )
        return attempt

    monkeypatch.setattr(
        "app.services.ensemble_arbitration_service._run_one_attempt",
        _run_with_invalid,
    )
    weights = [0.0] * PREFERENCE_FEATURE_DIMS
    weights[3] = 4.0
    monkeypatch.setattr(
        "app.services.ensemble_arbitration_service.effective_ranking",
        lambda **_k: True,
    )
    monkeypatch.setattr(
        "app.services.ensemble_arbitration_service.get_ranker",
        lambda **_k: PreferenceRankerV1(
            weights=weights, pair_count=3, updated_at=utc_now_iso()
        ),
    )
    score_pending = MagicMock()
    monkeypatch.setattr(
        "app.services.preference_store.score_pending",
        score_pending,
        raising=False,
    )

    settings = load_ensemble_arbitration_settings({"ENSEMBLE_ARBITRATION_ENABLED": "1"})
    with caplog.at_level(logging.INFO):
        report = run_ensemble_arbitration_preview(
            _preview_body(), settings=settings, db_path=acceptance_env
        )

    assert report.musical_quality_claim is False
    assert report.critic_is_subjective_layer is True
    assert report.ranking_is_preference_not_quality is True
    assert report.suggested_candidate_id is not None
    assert all(
        item.stage == "validator"
        for item in report.rejected_attempts
        if item.model_id == "fake:symbolic-sparse"
    )
    survivor_models = [c.provenance.model_id for c in report.candidates]
    assert "fake:symbolic-sparse" not in survivor_models
    assert report.candidates[0].provenance.model_id == "fake:symbolic-dense"
    assert report.ranking_applied is True
    score_pending.assert_not_called()

    # Logs must not dump event arrays in extras.
    for record in caplog.records:
        extras = getattr(record, "__dict__", {})
        assert "events" not in extras or not isinstance(extras.get("events"), list)

    # Human override: non-suggested survivor still available for Apply staging.
    non_suggested = next(
        c
        for c in report.candidates
        if c.candidate_id != report.suggested_candidate_id
    )
    assert non_suggested.composition.schema_version == "composition.v2"
    assert non_suggested.provenance.model_id in FAKE_ENSEMBLE_MODEL_IDS


def test_flag_off_refuses_and_no_collab_required(acceptance_env, monkeypatch):
    monkeypatch.setenv("ENSEMBLE_ARBITRATION_ENABLED", "0")
    with TestClient(app) as http:
        status = http.get("/ensemble/arbitration/status")
        assert status.status_code == 200
        assert status.json()["enabled"] is False
        refused = http.post("/ensemble/arbitration/preview", json=_preview_body())
        assert refused.status_code == 503
        assert refused.json()["detail"]["code"] == "ensemble_arbitration_disabled"

    monkeypatch.setenv("ENSEMBLE_ARBITRATION_ENABLED", "1")
    monkeypatch.setenv("COLLABORATION_ENABLED", "1")
    reload_registry()
    with TestClient(app) as http:
        # Preview works without studio-lab authorize / actor header.
        ok = http.post("/ensemble/arbitration/preview", json=_preview_body())
        assert ok.status_code == 200
        body = ok.json()
        assert body["musical_quality_claim"] is False


def test_top_n_limits_returned_candidates(acceptance_env):
    settings = load_ensemble_arbitration_settings({"ENSEMBLE_ARBITRATION_ENABLED": "1"})
    body = _preview_body()
    body["policy"]["selection_mode"] = "top_n"
    body["policy"]["top_n"] = 2
    report = run_ensemble_arbitration_preview(
        body, settings=settings, db_path=acceptance_env
    )
    assert len(report.candidates) <= 2
    assert report.suggested_candidate_id == report.candidates[0].candidate_id


def test_generate_echo_and_distinct_material(acceptance_env):
    plan = parse_composition_plan(_plan_dict())
    counts = {}
    for model_id in FAKE_ENSEMBLE_MODEL_IDS:
        music, report = generate_fake_symbolic_composition(
            plan, seed=0, model_id=model_id
        )
        assert report["model_id"] == model_id
        counts[model_id] = sum(len(t.events) for t in music.tracks)
    assert len(set(counts.values())) == 3
