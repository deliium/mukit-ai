"""Run and agent spans on workflow preview and single-agent routes."""

from __future__ import annotations

import logging
import uuid

import pytest
from fastapi.testclient import TestClient

from app.ai_agents import registry as agent_registry
from app.ai_agents import revision_loop
from app.ai_agents.registry import get_agent
from app.ai_runtime import registry as model_registry
from app.db import reset_database_initialization_cache
from app.main import app
from tests.test_ai_agents_routes import _composition_payload


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    monkeypatch.setenv("PROJECT_DB_PATH", str(tmp_path / "operation-runs.db"))
    reset_database_initialization_cache()
    agent_registry.clear_registry_for_tests()
    model_registry.clear_registry_for_tests()
    with TestClient(app) as test_client:
        yield test_client
    agent_registry.clear_registry_for_tests()
    model_registry.clear_registry_for_tests()


def test_preview_mints_operation_summary(client):
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
    summary = response.json()["operation_summary"]
    assert summary["schema_version"] == "operation.summary.v1"
    uuid.UUID(summary["run_id"])
    assert summary["status"] in {"ok", "failed", "cancelled", "budget_exceeded"}
    assert "composition" not in summary
    assert "prompt" not in summary


def test_preview_adopts_client_run_id(client, caplog):
    run_id = str(uuid.uuid4())
    caplog.set_level(logging.INFO)
    response = client.post(
        "/ai/agents/workflows/preview",
        json={
            "composition": _composition_payload(),
            "revision_mode": "off",
            "operation_run_id": run_id,
        },
    )
    assert response.status_code == 200, response.text
    summary = response.json()["operation_summary"]
    assert summary["run_id"] == run_id
    span_runs = {
        getattr(record, "run_id", None)
        for record in caplog.records
        if getattr(record, "operation_trace", False)
    }
    assert run_id in span_runs
    assert all(
        not hasattr(record, "composition") or getattr(record, "composition", None) is None
        for record in caplog.records
        if getattr(record, "operation_trace", False)
    )


def test_preview_rejects_invalid_run_id(client):
    response = client.post(
        "/ai/agents/workflows/preview",
        json={
            "composition": _composition_payload(),
            "operation_run_id": "not-a-uuid",
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "operation_run_id_invalid"


def test_single_agent_run_includes_summary(client):
    run_id = str(uuid.uuid4())
    response = client.post(
        "/ai/agents/critic/run",
        json={
            "operation": "critique",
            "composition": _composition_payload(),
            "operation_run_id": run_id,
        },
    )
    assert response.status_code == 200, response.text
    summary = response.json()["operation_summary"]
    assert summary["run_id"] == run_id
    assert summary["schema_version"] == "operation.summary.v1"


def test_scripted_agent_failure_is_on_summary_and_span(client, monkeypatch, caplog):
    real_get = get_agent

    def _wrapped(agent_id: str):
        agent = real_get(agent_id)
        if agent_id == "harmony":
            async def _boom(_request):
                raise RuntimeError("scripted")

            agent.run = _boom  # type: ignore[method-assign]
        return agent

    monkeypatch.setattr(revision_loop, "get_agent", _wrapped)
    caplog.set_level(logging.INFO)
    response = client.post(
        "/ai/agents/workflows/preview",
        json={
            "composition": _composition_payload(),
            "revision_mode": "off",
        },
    )
    assert response.status_code == 200, response.text
    summary = response.json()["operation_summary"]
    assert summary["failure_count"] >= 1
    assert summary["status"] == "failed"
    matched = [
        record
        for record in caplog.records
        if getattr(record, "operation_trace", False)
        and getattr(record, "run_id", None) == summary["run_id"]
        and getattr(record, "kind", None) == "agent"
        and getattr(record, "status", None) == "failed"
    ]
    assert matched
    assert all(not hasattr(record, "api_key") for record in matched)


def test_operation_revision_ceiling_binds_thorough(client, monkeypatch):
    monkeypatch.setenv("OPERATION_MAX_REVISIONS", "1")
    response = client.post(
        "/ai/agents/workflows/preview",
        json={
            "composition": _composition_payload(),
            "revision_mode": "thorough",
            "critic_parameters": {
                "fake_revise_passes": 3,
                "fake_revise_hard_finding": True,
            },
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["max_passes"] == 1
    summary = payload["operation_summary"]
    assert summary["revision_count"] <= 1
    if payload["stop_reason"] == "max_passes_reached":
        raise AssertionError("env ceiling must bind ahead of the mode cap")
    if payload["stop_reason"] == "resource_budget_exhausted":
        assert summary["budget_code"] == "operation_revision_budget"
