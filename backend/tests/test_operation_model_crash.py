"""SystemExit from an agent run stays inside the API process."""

from __future__ import annotations

import asyncio
import logging

import pytest
from fastapi.testclient import TestClient

from app.ai_agents import registry as agent_registry
from app.ai_agents import revision_loop
from app.ai_agents.fake_agents import FakeCriticAgent
from app.ai_agents.revision_loop import _run_agent_node
from app.ai_agents.schemas import AgentWorkflowContext
from app.ai_runtime import registry as model_registry
from app.composition_schemas import CompositionV2
from app.db import reset_database_initialization_cache
from app.main import app
from app.services.composition_snapshot_encoding import composition_snapshot_fingerprint
from tests.test_ai_agents_routes import _composition_payload


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    monkeypatch.setenv("PROJECT_DB_PATH", str(tmp_path / "operation-crash.db"))
    reset_database_initialization_cache()
    agent_registry.clear_registry_for_tests()
    model_registry.clear_registry_for_tests()
    with TestClient(app) as test_client:
        yield test_client
    agent_registry.clear_registry_for_tests()
    model_registry.clear_registry_for_tests()


def test_agent_system_exit_returns_failed_summary_and_ready(client, monkeypatch, caplog):
    async def _boom(self, request):  # noqa: ANN001
        raise SystemExit(7)

    monkeypatch.setattr(FakeCriticAgent, "run", _boom)
    caplog.set_level(logging.ERROR)
    response = client.post(
        "/ai/agents/critic/run",
        json={
            "operation": "critique",
            "composition": _composition_payload(),
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    summary = body["operation_summary"]
    assert summary["status"] == "failed"
    assert "operation_model_crashed" in body["warning_codes"]
    assert any(
        getattr(record, "failure_code", None) == "operation_model_crashed"
        and getattr(record, "error_type", None) == "SystemExit"
        for record in caplog.records
    )
    ready = client.get("/ready")
    assert ready.status_code == 200
    models = client.get("/ai/models")
    assert models.status_code == 200
    assert models.json()["models"]


def test_keyboard_interrupt_propagates_from_agent_node():
    class _Boom:
        async def run(self, request):  # noqa: ANN001
            raise KeyboardInterrupt

    composition = CompositionV2.model_validate(_composition_payload())
    fingerprint = composition_snapshot_fingerprint(composition)
    context = AgentWorkflowContext(
        source_composition=composition,
        source_fingerprint=fingerprint,
        working_draft_composition=composition,
    )

    def _agent(_agent_id: str) -> _Boom:
        return _Boom()

    original = revision_loop.get_agent
    revision_loop.get_agent = _agent  # type: ignore[assignment]
    try:
        with pytest.raises(KeyboardInterrupt):
            asyncio.run(
                _run_agent_node(
                    "critic",
                    context,
                    agent_model_overrides=None,
                    selection=None,
                )
            )
    finally:
        revision_loop.get_agent = original
