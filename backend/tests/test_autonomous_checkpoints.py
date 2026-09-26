"""Guided checkpoints hold after a finished stage. Autonomous mode does not."""

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


def _brief(**extra) -> dict:
    body = {
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
    body.update(extra)
    return body


def _statuses(body: dict) -> dict[str, str]:
    return {stage["stage_id"]: stage["status"] for stage in body["stages"]}


def test_guided_holds_after_plan_then_harmony(client: TestClient) -> None:
    started = client.post("/ai/agents/autonomous/runs", json=_brief(autonomy_mode="guided"))
    assert started.status_code == 200, started.text
    body = started.json()
    assert body["status"] == "awaiting_approval"
    assert body["checkpoint_id"] == "form"
    statuses = _statuses(body)
    assert statuses["plan"] == "completed"
    assert statuses["harmony_plan"] == "pending"
    plan_decision = next(stage for stage in body["stages"] if stage["stage_id"] == "plan")
    assert plan_decision["decision"] is None

    mismatch = client.post(
        f"/ai/agents/autonomous/runs/{body['run_id']}/checkpoints/harmony/approve"
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["detail"]["code"] == "autonomous_run_not_resumable"

    approved = client.post(
        f"/ai/agents/autonomous/runs/{body['run_id']}/checkpoints/form/approve"
    )
    assert approved.status_code == 200, approved.text
    next_body = approved.json()
    assert next_body["run_id"] == body["run_id"]
    assert next_body["checkpoint_id"] == "harmony"
    assert next_body["status"] == "awaiting_approval"
    approved_plan = next(stage for stage in next_body["stages"] if stage["stage_id"] == "plan")
    assert approved_plan["decision"] == "approved"
    statuses = _statuses(next_body)
    assert statuses["harmony_plan"] == "completed"
    assert statuses["motif_plan"] == "pending"
    assert next_body["plan"]["goals"]
    assert "events" not in next_body["plan"]
