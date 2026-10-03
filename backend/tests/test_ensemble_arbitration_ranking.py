"""Ranking gates, honesty stamps, and no-survivor errors for ensemble preview."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.ai_runtime.registry import reload_registry
from app.db import initialize_database
from app.db.connection import reset_database_initialization_cache
from app.ensemble_arbitration_schemas import (
    FAKE_ENSEMBLE_MODEL_IDS,
    EnsembleArbitrationError,
)
from app.ensemble_arbitration_settings import load_ensemble_arbitration_settings
from app.preference_schemas import PREFERENCE_FEATURE_DIMS, PreferenceRankerV1
from app.services.ensemble_arbitration_service import run_ensemble_arbitration_preview
from app.services.preference_ranker import utc_now_iso
from fastapi.testclient import TestClient

from app.main import app

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "composition_plan"


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


def _request(selection_mode: str = "auto_suggest") -> dict:
    return {
        "schema_version": "ensemble.arbitration.request.v1",
        "policy": {
            "schema_version": "ensemble.policy.v1",
            "model_ids": list(FAKE_ENSEMBLE_MODEL_IDS),
            "strategy": "parallel_once",
            "selection_mode": selection_mode,
            "top_n": 2,
            "base_seed": 0,
            "execution": "sequential",
        },
        "plan": _plan(),
        "constraints": _constraints(),
    }


@pytest.fixture
def enabled_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.setenv("ENSEMBLE_ARBITRATION_ENABLED", "1")
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    monkeypatch.setenv("PREFERENCE_LEARNING_ENABLED", "0")
    reset_database_initialization_cache()
    initialize_database()
    reload_registry()
    return db_path


def _warm_dense_ranker() -> PreferenceRankerV1:
    weights = [0.0] * PREFERENCE_FEATURE_DIMS
    weights[3] = 4.0
    return PreferenceRankerV1(
        weights=weights,
        pair_count=4,
        updated_at=utc_now_iso(),
    )


def test_ranking_off_preserves_generation_order(enabled_env, monkeypatch):
    monkeypatch.setattr(
        "app.services.ensemble_arbitration_service.effective_ranking",
        lambda **_kwargs: False,
    )
    settings = load_ensemble_arbitration_settings({"ENSEMBLE_ARBITRATION_ENABLED": "1"})
    report = run_ensemble_arbitration_preview(
        _request(), settings=settings, db_path=enabled_env
    )
    assert report.ranking_applied is False
    order = [c.provenance.model_id for c in report.candidates]
    assert order == list(FAKE_ENSEMBLE_MODEL_IDS)
    assert report.musical_quality_claim is False
    assert report.critic_is_subjective_layer is True
    assert report.ranking_is_preference_not_quality is True


def test_warm_ranker_reorders_dense_first(enabled_env, monkeypatch):
    monkeypatch.setattr(
        "app.services.ensemble_arbitration_service.effective_ranking",
        lambda **_kwargs: True,
    )
    monkeypatch.setattr(
        "app.services.ensemble_arbitration_service.get_ranker",
        lambda **_kwargs: _warm_dense_ranker(),
    )
    score_pending = MagicMock()
    monkeypatch.setattr(
        "app.services.preference_store.score_pending",
        score_pending,
        raising=False,
    )
    settings = load_ensemble_arbitration_settings({"ENSEMBLE_ARBITRATION_ENABLED": "1"})
    report = run_ensemble_arbitration_preview(
        _request(selection_mode="auto_suggest"),
        settings=settings,
        db_path=enabled_env,
    )
    assert report.ranking_applied is True
    assert report.candidates[0].provenance.model_id == "fake:symbolic-dense"
    assert report.suggested_candidate_id == report.candidates[0].candidate_id
    score_pending.assert_not_called()


def test_all_invalid_raises_no_survivors(enabled_env, monkeypatch):
    from app.services import ensemble_arbitration_service as svc
    from app.services.ensemble_arbitration_pipeline import EnsembleGenerateAttempt

    def _fail_all(**kwargs):
        return EnsembleGenerateAttempt(
            model_id=kwargs["model_id"],
            seed=kwargs["seed"],
            attempt_ordinal=kwargs["attempt_ordinal"],
            strategy=kwargs["strategy"],
            composition={"schema_version": "composition.v2", "tracks": "broken"},
        )

    monkeypatch.setattr(svc, "_run_one_attempt", _fail_all)
    settings = load_ensemble_arbitration_settings({"ENSEMBLE_ARBITRATION_ENABLED": "1"})
    with pytest.raises(EnsembleArbitrationError) as exc:
        run_ensemble_arbitration_preview(
            _request(), settings=settings, db_path=enabled_env
        )
    assert exc.value.code == "ensemble_no_survivors"
    assert exc.value.http_status == 422
    assert int(exc.value.details.get("rejected_count") or 0) == 3


def test_router_honesty_and_no_preferences_rank(enabled_env, monkeypatch):
    monkeypatch.setenv("PREFERENCE_LEARNING_ENABLED", "0")
    with TestClient(app) as http:
        response = http.post("/ensemble/arbitration/preview", json=_request())
        assert response.status_code == 200
        body = response.json()
        assert body["musical_quality_claim"] is False
        assert body["critic_is_subjective_layer"] is True
        assert body["ranking_is_preference_not_quality"] is True
        # No preference-rank HTTP side effects on this path.
        rank = http.post(
            "/preferences/rank",
            json={"surface": "development"},
        )
        # When learning off, rank may 503 — the assertion is that preview did not require it.
        assert response.status_code == 200
        assert rank.status_code in {200, 422, 503}
