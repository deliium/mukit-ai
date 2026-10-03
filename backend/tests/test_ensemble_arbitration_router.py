"""HTTP surface for ensemble arbitration (no Collab C, not on /ready)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ai_runtime.registry import reload_registry
from app.db import initialize_database
from app.db.connection import reset_database_initialization_cache
from app.ensemble_arbitration_schemas import FAKE_ENSEMBLE_MODEL_IDS
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


def _preview_body(**overrides) -> dict:
    payload = {
        "schema_version": "ensemble.arbitration.request.v1",
        "policy": {
            "schema_version": "ensemble.policy.v1",
            "model_ids": list(FAKE_ENSEMBLE_MODEL_IDS),
            "strategy": "parallel_once",
            "selection_mode": "human",
            "top_n": 2,
            "base_seed": 1,
            "execution": "sequential",
        },
        "plan": _plan(),
        "constraints": _constraints(),
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def ensemble_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.setenv("ENSEMBLE_ARBITRATION_ENABLED", "1")
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    monkeypatch.setenv("COLLABORATION_ENABLED", "0")
    monkeypatch.setenv("PREFERENCE_LEARNING_ENABLED", "0")
    reset_database_initialization_cache()
    initialize_database()
    reload_registry()
    with TestClient(app) as http:
        yield http


def test_status_when_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.setenv("ENSEMBLE_ARBITRATION_ENABLED", "0")
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    reset_database_initialization_cache()
    initialize_database()
    with TestClient(app) as http:
        status = http.get("/ensemble/arbitration/status")
        assert status.status_code == 200
        body = status.json()
        assert body["enabled"] is False
        assert body["musical_quality_claim"] is False
        strategies = http.get("/ensemble/arbitration/strategies")
        assert strategies.status_code == 200
        assert "parallel_once" in strategies.json()["strategies"]
        refused = http.post("/ensemble/arbitration/preview", json=_preview_body())
        assert refused.status_code == 503
        assert refused.json()["detail"]["code"] == "ensemble_arbitration_disabled"


def test_preview_happy_path(ensemble_client: TestClient):
    http = ensemble_client
    response = http.post("/ensemble/arbitration/preview", json=_preview_body())
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "ensemble.arbitration.v1"
    assert body["musical_quality_claim"] is False
    assert body["critic_is_subjective_layer"] is True
    assert body["ranking_is_preference_not_quality"] is True
    assert len(body["candidates"]) >= 2


def test_payload_refuse(ensemble_client: TestClient):
    http = ensemble_client
    refused = http.post(
        "/ensemble/arbitration/preview",
        json={**_preview_body(), "command": "rm -rf /"},
    )
    assert refused.status_code == 422
    assert refused.json()["detail"]["code"] == "ensemble_payload_refused"


def test_model_limit_mapping(ensemble_client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ENSEMBLE_MAX_MODELS", "3")
    http = ensemble_client
    body = _preview_body()
    body["policy"]["model_ids"] = [
        "fake:symbolic-tiny",
        "fake:symbolic-sparse",
        "fake:symbolic-dense",
        "fake:symbolic-extra",
    ]
    refused = http.post("/ensemble/arbitration/preview", json=body)
    assert refused.status_code == 422
    assert refused.json()["detail"]["code"] == "ensemble_model_limit"


def test_preview_does_not_require_collab(ensemble_client: TestClient, monkeypatch):
    monkeypatch.setenv("COLLABORATION_ENABLED", "1")
    http = ensemble_client
    # No X-Mukit-Actor / authorize_studio_lab — preview must still succeed.
    response = http.post("/ensemble/arbitration/preview", json=_preview_body())
    assert response.status_code == 200


def test_ready_has_no_ensemble_key(ensemble_client: TestClient):
    http = ensemble_client
    ready = http.get("/ready")
    assert ready.status_code == 200
    payload = ready.json()
    blob = json.dumps(payload)
    assert "ensemble" not in blob.lower() or "ensemble_arbitration" not in blob
