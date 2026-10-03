"""HTTP routes for AI scheduling policy and preview."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import initialize_database, reset_database_initialization_cache
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(path))
    monkeypatch.delenv("AI_SCHEDULING_ENABLED", raising=False)
    reset_database_initialization_cache()
    initialize_database()
    return TestClient(app)


def test_policy_404_when_disabled(client) -> None:
    response = client.get("/ai/scheduling/policy")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "scheduling_disabled"


def test_get_put_preview_when_enabled(client, monkeypatch) -> None:
    monkeypatch.setenv("AI_SCHEDULING_ENABLED", "1")
    monkeypatch.setenv("AI_SCHEDULING_DEFAULT_MODE", "prefer_local")

    got = client.get("/ai/scheduling/policy")
    assert got.status_code == 200
    body = got.json()
    assert body["mode"] == "prefer_local"
    assert body["document_revision"] == 1

    put = client.put(
        "/ai/scheduling/policy",
        json={
            "mode": "fastest_available",
            "allow_public_cloud": False,
            "max_attempts": 2,
            "document_revision": 2,
        },
    )
    assert put.status_code == 200
    assert put.json()["mode"] == "fastest_available"
    assert put.json()["document_revision"] == 2

    conflict = client.put(
        "/ai/scheduling/policy",
        json={
            "mode": "prefer_local",
            "allow_public_cloud": False,
            "max_attempts": 2,
            "document_revision": 2,
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "scheduling_conflict"

    preview = client.post(
        "/ai/scheduling/preview",
        json={
            "job": {
                "operation": "generate_planner",
                "required_capability": "language_planner",
                "privacy_class": "private",
            },
            "candidates": [
                {
                    "model_id": "local:a",
                    "runtime": "local_openai_compatible",
                    "primary_capability": "language_planner",
                    "supported_operations": ["generate_planner"],
                    "trust_boundary": "controller_local",
                    "device_class": "igpu",
                    "memory_available_mb": 4096,
                    "estimated_latency_ms": 40,
                    "availability": "available",
                    "status": "ready",
                    "locality": "local",
                },
                {
                    "model_id": "openai:c",
                    "runtime": "openai_compatible_chat",
                    "primary_capability": "language_planner",
                    "supported_operations": ["generate_planner"],
                    "trust_boundary": "public_cloud",
                    "estimated_latency_ms": 5,
                    "availability": "available",
                    "status": "ready",
                    "locality": "remote",
                },
            ],
        },
    )
    assert preview.status_code == 200
    decision = preview.json()
    assert decision["selected_model_id"] == "local:a"
    assert decision["selected_model_id"] != "openai:c"
