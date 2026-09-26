"""Caps and conflicts around autonomous run start."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ai_agents import registry as agent_registry
from app.ai_runtime import registry as model_registry
from app.db import reset_database_initialization_cache
from app.main import app
from app.services import project_store


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PROJECT_DB_PATH", str(tmp_path / "projects.db"))
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    monkeypatch.setenv("AUTONOMOUS_MAX_RUNS_PER_PROJECT", "1")
    monkeypatch.delenv("FAKE_CRITIC_REVISE_PASSES", raising=False)
    reset_database_initialization_cache()
    agent_registry.clear_registry_for_tests()
    model_registry.clear_registry_for_tests()
    with TestClient(app) as test_client:
        yield test_client
    agent_registry.clear_registry_for_tests()
    model_registry.clear_registry_for_tests()


def _brief(project_id: str | None = None, fingerprint: str | None = None) -> dict:
    body = {
        "brief": {
            "schema_version": "creative.brief.v1",
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
        },
        "include_rendering": False,
    }
    if project_id:
        body["project_id"] = project_id
    if fingerprint:
        body["expected_source_fingerprint"] = fingerprint
    return body


def test_run_cap_returns_409(client: TestClient) -> None:
    first = client.post("/ai/agents/autonomous/runs", json=_brief())
    assert first.status_code == 200, first.text
    project_id = first.json()["project_id"]
    second = client.post("/ai/agents/autonomous/runs", json=_brief(project_id))
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "autonomous_run_limit"


def test_fingerprint_mismatch_returns_409(client: TestClient, tmp_path: Path) -> None:
    db = tmp_path / "projects.db"
    created = project_store.create_project("Existing", db_path=db)
    response = client.post(
        "/ai/agents/autonomous/runs",
        json=_brief(created.id, fingerprint="0" * 64),
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "project_revision_conflict"
