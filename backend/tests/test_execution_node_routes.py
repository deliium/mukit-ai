"""Controller register / heartbeat / list routes."""

from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db import initialize_database, reset_database_initialization_cache
from app.routers.execution_nodes import router as controller_router
from app.services import execution_node_runtime as live
from app.services import execution_node_tasks as tasks

_TOKEN = "route-token-fixture"
_NODE_ID = "node_0123456789abcdef"


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(path))
    monkeypatch.setenv("AI_EXECUTION_NODES_ENABLED", "1")
    monkeypatch.setenv("AI_EXECUTION_NODE_TOKEN", _TOKEN)
    monkeypatch.setenv("AI_EXECUTION_NODE_ROLE", "both")
    reset_database_initialization_cache()
    initialize_database()
    live.clear_live_state()
    tasks.clear_tasks()
    app = FastAPI()
    app.include_router(controller_router)
    return TestClient(app)


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {_TOKEN}"}


def _registration(**overrides) -> dict:
    body = {
        "schema_version": "execution.node.registration.v1",
        "node_id": _NODE_ID,
        "display_name": "Spare",
        "address": "http://192.168.1.40:8000",
        "capabilities": ["language_planner"],
        "installed_models": [
            {
                "id": "fake:language",
                "display_name": "Fake",
                "primary_capability": "language_planner",
                "runtime": "fake",
                "status": "ready",
            }
        ],
        "resources": {"active_tasks": 0, "max_concurrency": 1},
    }
    body.update(overrides)
    return body


def test_register_heartbeat_list(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    created = client.post("/ai/execution-nodes/register", json=_registration(), headers=_auth())
    assert created.status_code == 201
    body = created.json()
    assert body["node_id"] == _NODE_ID
    assert body["availability"] == "unavailable"
    revision = body["document_revision"]
    hb = client.post(
        f"/ai/execution-nodes/{_NODE_ID}/heartbeat",
        json={
            "schema_version": "execution.node.heartbeat.v1",
            "node_id": _NODE_ID,
            "health": {"status": "ready"},
            "resources": {"active_tasks": 0, "max_concurrency": 1},
            "availability": "available",
            "document_revision": revision,
        },
        headers=_auth(),
    )
    assert hb.status_code == 200
    assert hb.json()["availability"] == "available"
    listed = client.get("/ai/execution-nodes", headers=_auth())
    assert listed.status_code == 200
    assert listed.json()[0]["availability"] == "available"
    assert _TOKEN not in caplog.text


def test_disabled_and_unauthorized(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("AI_EXECUTION_NODES_ENABLED", "0")
    missing = client.post("/ai/execution-nodes/register", json=_registration(), headers=_auth())
    assert missing.status_code == 404
    monkeypatch.setenv("AI_EXECUTION_NODES_ENABLED", "1")
    unauth = client.post("/ai/execution-nodes/register", json=_registration())
    assert unauth.status_code == 401


def test_address_rejected(client: TestClient) -> None:
    response = client.post(
        "/ai/execution-nodes/register",
        json=_registration(address="http://169.254.169.254"),
        headers=_auth(),
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "execution_node_address_rejected"
