"""Worker typed inference routes."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.execution_node_schemas import ExecutionNodeError, ExecutionTaskV1
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
    yield TestClient(app)
    dispatch.clear_worker_tasks()


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


def test_max_concurrency_busy_and_draining(monkeypatch) -> None:
    monkeypatch.setenv("AI_EXECUTION_NODES_ENABLED", "1")
    monkeypatch.setenv("AI_EXECUTION_NODE_TOKEN", _TOKEN)
    monkeypatch.setenv("AI_EXECUTION_NODE_ROLE", "both")
    monkeypatch.setenv("AI_EXECUTION_NODE_FAKE", "1")
    monkeypatch.setenv("AI_EXECUTION_NODE_MAX_CONCURRENCY", "1")
    monkeypatch.setenv("AI_EXECUTION_NODE_FAKE_SLOW", "1")
    dispatch.clear_worker_tasks()

    resources, availability = dispatch.worker_resource_snapshot()
    assert resources.max_concurrency == 1
    assert resources.active_tasks == 0
    assert availability == "available"

    task_a = ExecutionTaskV1(
        task_id="task_aaaaaaaaaaaaaaaa",
        operation="complete_text",
        model_id="fake:language",
        input_text="a",
        purpose="generate",
    )
    task_b = ExecutionTaskV1(
        task_id="task_bbbbbbbbbbbbbbbb",
        operation="complete_text",
        model_id="fake:language",
        input_text="b",
        purpose="generate",
    )

    async def _run() -> None:
        first = asyncio.create_task(dispatch.dispatch_complete_text(task_a))
        await asyncio.sleep(0.1)
        busy_resources, busy_availability = dispatch.worker_resource_snapshot()
        assert busy_resources.active_tasks == 1
        assert busy_availability == "busy"
        with pytest.raises(ExecutionNodeError) as exc_info:
            await dispatch.dispatch_complete_text(task_b)
        assert exc_info.value.code == "execution_node_busy"
        dispatch.set_worker_draining(True)
        _, draining_availability = dispatch.worker_resource_snapshot()
        assert draining_availability == "draining"
        cancelled = dispatch.cancel_worker_task(task_a.task_id)
        assert cancelled["status"] == "cancelled"
        result = await first
        assert result.status == "cancelled"
        assert result.failure_code == "operation_cancelled"

    asyncio.run(_run())
    dispatch.clear_worker_tasks()
