"""Pause, reject, open, and branch keep the same run and earlier stages."""

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


def _approve(client: TestClient, run_id: str, checkpoint_id: str) -> dict:
    response = client.post(
        f"/ai/agents/autonomous/runs/{run_id}/checkpoints/{checkpoint_id}/approve"
    )
    assert response.status_code == 200, response.text
    return response.json()


def _walk_to_arrangement(client: TestClient) -> dict:
    import uuid

    started = client.post(
        "/ai/agents/autonomous/runs",
        json=_brief(autonomy_mode="guided", operation_run_id=str(uuid.uuid4())),
    )
    assert started.status_code == 200, started.text
    body = started.json()
    for checkpoint_id in ("form", "harmony", "motif", "critique"):
        assert body["checkpoint_id"] == checkpoint_id
        body = _approve(client, body["run_id"], checkpoint_id)
    assert body["checkpoint_id"] == "arrangement"
    return body


def test_unknown_pause_id_is_404(client: TestClient) -> None:
    response = client.post(
        "/ai/agents/autonomous/runs/pause",
        json={"operation_run_id": "missing-operation"},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "autonomous_run_not_found"


def test_pause_during_plan_stops_before_harmony(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import uuid

    from app.ai_agents.fake_agents import FakeCreativeDirectorAgent
    from app.services.autonomous_composer_store import get_run_by_operation, set_pause_requested

    operation_run_id = str(uuid.uuid4())
    original = FakeCreativeDirectorAgent._run_impl

    async def wrapped(self, request):
        result = await original(self, request)
        run = get_run_by_operation(operation_run_id)
        assert run is not None
        set_pause_requested(run.id, True)
        return result

    monkeypatch.setattr(FakeCreativeDirectorAgent, "_run_impl", wrapped)
    response = client.post(
        "/ai/agents/autonomous/runs",
        json=_brief(autonomy_mode="autonomous", operation_run_id=operation_run_id),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "paused"
    statuses = {stage["stage_id"]: stage["status"] for stage in body["stages"]}
    assert statuses["plan"] == "completed"
    assert statuses["harmony_plan"] == "pending"
    assert "failed" not in statuses.values()
    assert body["pause_requested"] is False


def test_pause_while_waiting_does_not_clear_the_checkpoint(client: TestClient) -> None:
    import uuid

    operation_run_id = str(uuid.uuid4())
    started = client.post(
        "/ai/agents/autonomous/runs",
        json=_brief(autonomy_mode="guided", operation_run_id=operation_run_id),
    )
    assert started.status_code == 200, started.text
    body = started.json()
    paused = client.post(
        "/ai/agents/autonomous/runs/pause",
        json={"operation_run_id": operation_run_id},
    )
    assert paused.status_code == 200, paused.text
    payload = paused.json()
    assert payload["status"] == "awaiting_approval"
    assert payload["checkpoint_id"] == body["checkpoint_id"] == "form"
    resume = client.post(f"/ai/agents/autonomous/runs/{body['run_id']}/resume")
    assert resume.status_code == 409


def test_reject_resume_keeps_earlier_revisions(client: TestClient) -> None:
    waiting = _walk_to_arrangement(client)
    before = {stage["stage_id"]: stage for stage in waiting["stages"]}
    rejected = client.post(
        f"/ai/agents/autonomous/runs/{waiting['run_id']}/checkpoints/arrangement/reject"
    )
    assert rejected.status_code == 200, rejected.text
    body = rejected.json()
    assert body["status"] == "paused"
    assert body["checkpoint_id"] is None
    assert body["run_id"] == waiting["run_id"]
    assert body["project_id"] == waiting["project_id"]
    arrangement = next(stage for stage in body["stages"] if stage["stage_id"] == "arrangement")
    assert arrangement["status"] == "failed"
    assert arrangement["failure_code"] == "autonomous_stage_rejected"
    assert arrangement["rejected_revision_id"] == before["arrangement"]["revision_id"]
    assert arrangement["revision_id"] is None
    assert body["head_revision_id"] != waiting["head_revision_id"]
    expression = next(stage for stage in body["stages"] if stage["stage_id"] == "expression")
    assert expression["status"] == "pending"
    for stage_id in ("plan", "harmony_plan", "motif_plan", "symbolic", "critique"):
        current = next(stage for stage in body["stages"] if stage["stage_id"] == stage_id)
        assert current["status"] == "completed"
        assert current["revision_id"] == before[stage_id]["revision_id"]

    resumed = client.post(f"/ai/agents/autonomous/runs/{body['run_id']}/resume")
    assert resumed.status_code == 200, resumed.text
    again = resumed.json()
    assert again["checkpoint_id"] == "arrangement"
    for stage_id in ("plan", "harmony_plan", "motif_plan", "symbolic", "critique"):
        current = next(stage for stage in again["stages"] if stage["stage_id"] == stage_id)
        assert current["status"] == "completed"
        assert current["revision_id"] == before[stage_id]["revision_id"]


def test_open_and_branch_use_history(client: TestClient) -> None:
    waiting = _walk_to_arrangement(client)
    missing = client.post(
        f"/ai/agents/autonomous/runs/{waiting['run_id']}/stages/plan/open"
    )
    assert missing.status_code == 409
    assert missing.json()["detail"]["code"] == "autonomous_revision_not_head"
    head_open = client.post(
        f"/ai/agents/autonomous/runs/{waiting['run_id']}/stages/arrangement/open"
    )
    assert head_open.status_code == 200, head_open.text
    assert head_open.json()["head_revision_id"] != waiting["head_revision_id"]
    branched = client.post(
        f"/ai/agents/autonomous/runs/{waiting['run_id']}/stages/symbolic/branch",
        json={"name": "From theme"},
    )
    assert branched.status_code == 200, branched.text
    assert branched.json()["name"] == "From theme"
    assert branched.json()["branch_id"]
    from app.services.autonomous_composer_store import get_run

    stored = get_run(waiting["run_id"])
    assert stored.branch_id
    assert stored.branch_id != branched.json()["branch_id"]
    current = client.get(f"/ai/agents/autonomous/runs/{waiting['run_id']}")
    assert current.json()["checkpoint_id"] == "arrangement"
