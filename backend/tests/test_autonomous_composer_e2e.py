"""Mocked end-to-end autonomous run with the fake symbolic engine."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ai_agents import registry as agent_registry
from app.ai_runtime import registry as model_registry
from app.db import reset_database_initialization_cache
from app.main import app


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PROJECT_DB_PATH", str(tmp_path / "projects.db"))
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    monkeypatch.delenv("FAKE_CRITIC_REVISE_PASSES", raising=False)
    monkeypatch.delenv("MUSIC_TRANSFORMER_CHECKPOINT", raising=False)
    reset_database_initialization_cache()
    agent_registry.clear_registry_for_tests()
    model_registry.clear_registry_for_tests()
    with TestClient(app) as test_client:
        yield test_client
    agent_registry.clear_registry_for_tests()
    model_registry.clear_registry_for_tests()


def _brief() -> dict:
    return {
        "brief": {
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
        },
        "include_rendering": False,
        "seed": 0,
    }


def test_example_brief_completes_without_rendering(client: TestClient) -> None:
    response = client.post("/ai/agents/autonomous/runs", json=_brief())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["schema_version"] == "autonomous.run.v1"
    assert body["status"] == "completed"
    assert body["head_revision_id"]
    assert body["project_id"]
    assert "composition" not in body
    assert "brief" not in body
    statuses = {stage["stage_id"]: stage["status"] for stage in body["stages"]}
    assert statuses["symbolic"] == "completed"
    assert statuses["revision"] == "skipped"
    assert statuses["render"] == "skipped"
    assert statuses["expression"] == "completed"
