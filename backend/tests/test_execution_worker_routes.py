"""Worker typed inference routes."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.execution_worker import router as worker_router
from app.services import execution_worker_dispatch as dispatch

_TOKEN = "worker-token-fixture"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AI_EXECUTION_NODES_ENABLED", "1")
    monkeypatch.setenv("AI_EXECUTION_NODE_TOKEN", _TOKEN)
    monkeypatch.setenv("AI_EXECUTION_NODE_ROLE", "both")
    monkeypatch.setenv("AI_EXECUTION_NODE_FAKE", "1")
    monkeypatch.delenv("AI_EXECUTION_NODE_FAKE_SLOW", raising=False)
    dispatch.clear_worker_tasks()
    app = FastAPI()
    app.include_router(worker_router)
    return TestClient(app)


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {_TOKEN}"}


def test_health_models_complete(client: TestClient) -> None:
    health = client.get("/execution/v1/health", headers=_auth())
    assert health.status_code == 200
    assert health.json()["status"] == "ready"
    models = client.get("/execution/v1/models", headers=_auth())
    assert models.status_code == 200
    assert models.json()["schema_version"] == "execution.node.catalog.v1"
    assert models.json()["installed_models"]
    result = client.post(
        "/execution/v1/complete_text",
        json={
            "schema_version": "execution.task.v1",
            "task_id": "task_fedcba9876543210",
            "operation": "complete_text",
            "model_id": "fake:language",
            "input_text": "hello",
            "purpose": "generate",
        },
        headers=_auth(),
    )
    assert result.status_code == 200
    body = result.json()
    assert body["status"] == "completed"
    assert body["output_text"] == "fake-execution-node:generate"


def test_worker_role_gate(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("AI_EXECUTION_NODE_ROLE", "controller")
    response = client.get("/execution/v1/health", headers=_auth())
    assert response.status_code == 404
