"""HTTP coverage for /ai/agents discovery, run, and workflow preview."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.ai_agents import registry as agent_registry
from app.ai_runtime import registry as model_registry
from app.main import app
from app.db import reset_database_initialization_cache
from tests.test_composition_v2_schema import minimal_v2


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    monkeypatch.setenv("PROJECT_DB_PATH", str(tmp_path / "ai-agents-routes.db"))
    reset_database_initialization_cache()
    agent_registry.clear_registry_for_tests()
    model_registry.clear_registry_for_tests()
    with TestClient(app) as test_client:
        yield test_client
    agent_registry.clear_registry_for_tests()
    model_registry.clear_registry_for_tests()


def _composition_payload():
    return minimal_v2(
        tracks=[
            {
                "id": "piano-1",
                "name": "Piano",
                "instrument": "piano",
                "role": "melody",
                "midi_program": 0,
                "channel": 1,
                "events": [
                    {
                        "pitch": "C4",
                        "start_tick": 0,
                        "duration_ticks": 480,
                        "velocity": 80,
                    }
                ],
            }
        ]
    )


def test_list_agents_nine_ready(client):
    response = client.get("/ai/agents")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["agents"]) == 9
    assert all(a["mutates_composition"] is False for a in payload["agents"])
    assert "agent_spine_v1" in payload["workflow_ids"]


def test_get_agent_detail(client):
    response = client.get("/ai/agents/harmony")
    assert response.status_code == 200
    assert response.json()["id"] == "harmony"
    assert response.json()["capability"] == "harmony"


def test_get_agent_not_found(client):
    response = client.get("/ai/agents/nope")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "agent_not_found"


def test_run_agent_fake_mode(client):
    response = client.post(
        "/ai/agents/critic/run",
        json={"operation": "critique", "composition": _composition_payload()},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["mutates_composition"] is False
    assert payload["artifacts"]
    assert payload["provenance_stage"]["agent_id"] == "critic"


def test_run_harmony_agent(client):
    response = client.post(
        "/ai/agents/harmony/run",
        json={"operation": "propose", "composition": _composition_payload()},
    )
    assert response.status_code == 200, response.text
    assert response.json()["agent_id"] == "harmony"


def test_workflow_preview_stateless(client):
    before = client.get("/projects").json()["projects"]
    response = client.post(
        "/ai/agents/workflows/preview",
        json={
            "composition": _composition_payload(),
            "workflow_id": "agent_spine_v1",
            "max_revisions": 0,
            "revision_mode": "off",
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["mutates_composition"] is False
    assert payload["operation_type"] == "multi-agent-apply"
    assert payload["candidate"]["schema_version"] == "composition.v2"
    assert payload["candidate_fingerprint"]
    assert payload["revision_mode"] == "off"
    assert payload["max_passes"] == 0
    assert payload["stop_reason"] in {
        "critic_approve",
        "hard_requirements_satisfied",
        "revise_exhausted",
        "max_passes_reached",
    }
    assert isinstance(payload["revision_history"], list)
    assert len(payload["revision_history"]) >= 1
    assert payload["revision_history"][0]["pass_index"] == 0
    assert "candidate_fingerprint" in payload["revision_history"][0]
    assert "composition" not in payload["revision_history"][0]
    assert isinstance(payload["pass_candidates"], list)
    assert len(payload["pass_candidates"]) >= 1
    assert payload["pass_candidates"][0]["pass_index"] == 0
    assert payload["pass_candidates"][0]["composition"]["schema_version"] == "composition.v2"
    assert payload["pass_candidates"][0]["candidate_fingerprint"]
    assert payload["agent_sequence"] == [
        "creative_director",
        "harmony",
        "melody_motif",
        "arrangement",
        "critic",
    ]
    assert all(s.get("agent_id") for s in payload["stages"])
    after = client.get("/projects").json()["projects"]
    assert after == before


def test_workflow_preview_revision_mode_fast_history(client):
    response = client.post(
        "/ai/agents/workflows/preview",
        json={
            "composition": _composition_payload(),
            "workflow_id": "agent_spine_v1",
            "revision_mode": "fast",
            "critic_parameters": {
                "fake_revise_passes": 1,
                "fake_revise_hard_finding": True,
            },
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["revision_mode"] == "fast"
    assert payload["max_passes"] == 1
    assert payload["stop_reason"]
    assert len(payload["revision_history"]) >= 1
    assert len(payload["pass_candidates"]) == len(payload["revision_history"])
    for rec, cand in zip(payload["revision_history"], payload["pass_candidates"], strict=True):
        assert rec["pass_index"] == cand["pass_index"]
        assert rec["candidate_fingerprint"] == cand["candidate_fingerprint"]
        assert cand["composition"]["schema_version"] == "composition.v2"
        assert "composition" not in rec
    assert payload["mutates_composition"] is False
