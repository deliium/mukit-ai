"""Engine continuous maintain proxy (singular /session/ path)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import reset_database_initialization_cache
from app.main import app
from app.services.adaptive_engine_service import reset_default_engine_registry
from app.services.adaptive_musical_context_runtime import reset_default_context_registry
from app.services.adaptive_playback_runtime import reset_default_playback_registry
from app.services.adaptive_runtime_continuation_runtime import (
    reset_default_continuation_registry,
)
from app.services.adaptive_score_transition_pending import reset_default_registry
from tests.test_adaptive_engine_api import _AUTH, _TOKEN, _composition, _score


def _client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    continuous: bool = False,
    engine_continuous: bool = False,
) -> TestClient:
    monkeypatch.setenv("PROJECT_DB_PATH", str(tmp_path / "projects.db"))
    monkeypatch.setenv("ADAPTIVE_ENGINE_TOKEN", _TOKEN)
    monkeypatch.setenv("ADAPTIVE_ENGINE_TICKER", "manual")
    monkeypatch.delenv("COLLABORATION_ENABLED", raising=False)
    if continuous:
        monkeypatch.setenv("ADAPTIVE_CONTINUOUS_ENABLED", "1")
    else:
        monkeypatch.delenv("ADAPTIVE_CONTINUOUS_ENABLED", raising=False)
    if engine_continuous:
        monkeypatch.setenv("ADAPTIVE_ENGINE_CONTINUOUS_ENABLED", "1")
    else:
        monkeypatch.delenv("ADAPTIVE_ENGINE_CONTINUOUS_ENABLED", raising=False)
    reset_database_initialization_cache()
    reset_default_registry()
    reset_default_playback_registry()
    reset_default_context_registry()
    reset_default_engine_registry()
    reset_default_continuation_registry()
    return TestClient(app)


def _seed_session(client: TestClient) -> tuple[str, str, str, int]:
    created = client.post("/projects", json={"name": "Engine continuous", "composition": _composition()})
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    posted = client.post(
        f"/projects/{project_id}/adaptive-scores",
        json={"is_default": True, "score": _score()},
    )
    assert posted.status_code == 201, posted.text
    score_id = posted.json()["score"]["id"]
    revision = posted.json()["document_revision"]
    session = client.post(
        "/adaptive/session",
        headers=_AUTH,
        json={
            "project_id": project_id,
            "score_id": score_id,
            "expected_document_revision": revision,
        },
    )
    assert session.status_code == 201, session.text
    return project_id, score_id, session.json()["session_id"], revision


def test_engine_continuous_disabled_refuse(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(tmp_path, monkeypatch)
    _project_id, _score_id, session_id, _revision = _seed_session(client)
    response = client.post(
        f"/adaptive/session/{session_id}/continuous/maintain",
        headers=_AUTH,
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "adaptive_engine_continuous_disabled"
    # Path must be singular session.
    assert "/adaptive/sessions/" not in str(response.url)


def test_engine_continuous_enabled_fake_maintain(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    client = _client(tmp_path, monkeypatch, continuous=True, engine_continuous=True)
    project_id, score_id, session_id, _revision = _seed_session(client)
    got = client.get(f"/adaptive/session/{session_id}", headers=_AUTH)
    assert got.status_code == 200
    assert got.json()["continuous_enabled"] is True
    assert got.json()["engine_continuous_enabled"] is True
    maintain = client.post(
        f"/adaptive/session/{session_id}/continuous/maintain",
        headers=_AUTH,
    )
    assert maintain.status_code == 200, maintain.text
    body = maintain.json()
    assert body["schema_version"] == "adaptive.runtime.continuation.v1"
    assert body["continuous"] is True
    assert body["source"] in {"fallback", "none"}
    assert body["fallback_kind"]
    score = client.get(f"/projects/{project_id}/adaptive-scores/{score_id}")
    assert score.status_code == 200
    assert "composition.v5" not in score.text
    assert any(
        getattr(record, "code", None) == "engine_continuous_maintain"
        for record in caplog.records
    )
