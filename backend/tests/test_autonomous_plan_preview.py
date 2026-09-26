"""Compiler preview returns project.plan.v1 and does not create a project."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import reset_database_initialization_cache
from app.db.connection import get_connection
from app.main import app


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    reset_database_initialization_cache()
    with TestClient(app) as test_client:
        yield test_client, db_path


def _brief() -> dict:
    return {
        "schema_version": "creative.brief.v1",
        "title": "Cinematic",
        "duration_seconds": 150,
        "narrative": [
            {"intent": "sparse_opening", "text": "cold sparse opening"},
            {"intent": "establish_theme", "text": "introduce Theme A"},
            {"intent": "build", "text": "increase tension"},
            {"intent": "climax", "text": "strong climax"},
            {"intent": "resolve", "text": "quiet transformed ending"},
        ],
        "instrumentation": ["piano", "cello", "strings"],
        "forbidden_instrument_families": ["drums"],
        "opening_key": "F# minor",
        "final_section_key": "F# major",
        "motif_label": "Theme A",
    }


def test_preview_returns_forty_five_bars_without_a_project(client) -> None:
    test_client, db_path = client
    response = test_client.post("/ai/agents/autonomous/plans", json=_brief())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["schema_version"] == "project.plan.v1"
    assert body["constraints"]["duration_bars"] == 45
    assert "tracks" not in body
    with get_connection(db_path) as conn:
        projects = conn.execute("SELECT COUNT(*) AS n FROM projects").fetchone()
        runs = conn.execute("SELECT COUNT(*) AS n FROM autonomous_runs").fetchone()
    assert int(projects["n"]) == 0
    assert int(runs["n"]) == 0


def test_unknown_instrument_is_rejected(client) -> None:
    test_client, _db_path = client
    body = _brief()
    body["instrumentation"] = ["not-a-real-instrument"]
    response = test_client.post("/ai/agents/autonomous/plans", json=body)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "autonomous_instrument_unknown"


def test_run_view_projects_plan_without_composition(client) -> None:
    test_client, _db_path = client
    response = test_client.post(
        "/ai/agents/autonomous/runs",
        json={"brief": _brief(), "include_rendering": False, "autonomy_mode": "autonomous"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["autonomy_mode"] == "autonomous"
    assert body["checkpoint_id"] is None
    assert body["plan"]["constraints"]["duration_bars"] == 45
    assert body["plan"]["constraints"]["motif_label"] == "Theme A"
    assert "tracks" not in body["plan"]
    assert "events" not in body
    assert body["stages"][0]["completion_code"]
