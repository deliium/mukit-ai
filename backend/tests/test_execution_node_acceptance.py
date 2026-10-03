"""End-to-end acceptance for trusted LAN ExecutionNode routing."""

from __future__ import annotations

import asyncio
import logging
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ai_runtime.errors import ModelNotFoundError, ModelUnavailableError
from app.ai_runtime.invoke_text import ainvoke_text_for_resolved
from app.ai_runtime.operations import AiOperation
from app.ai_runtime.registry import clear_registry_for_tests, reload_registry
from app.ai_runtime.routing import resolve_model_for_operation, set_current_resolved_model
from app.db import initialize_database, reset_database_initialization_cache
from app.operation_trace import is_run_cancelled, mark_run_cancelled
from app.routers.execution_nodes import router as controller_router
from app.services import execution_node_runtime as live
from app.services import execution_node_service as service
from app.services import execution_node_tasks as tasks
from app.services.execution_node_fake import (
    FAKE_NODE_ID,
    ensure_fake_peer_registered,
    reset_fake_peer_state,
)

_TOKEN = "acceptance-token-fixture"


@pytest.fixture
def acceptance_env(tmp_path, monkeypatch):
    path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(path))
    monkeypatch.setenv("AI_EXECUTION_NODES_ENABLED", "1")
    monkeypatch.setenv("AI_EXECUTION_NODE_TOKEN", _TOKEN)
    monkeypatch.setenv("AI_EXECUTION_NODE_ROLE", "both")
    monkeypatch.setenv("AI_EXECUTION_NODE_FAKE", "1")
    monkeypatch.setenv("AI_EXECUTION_NODE_HEARTBEAT_TTL_SECONDS", "15")
    monkeypatch.delenv("AI_EXECUTION_NODE_FAKE_SLOW", raising=False)
    reset_database_initialization_cache()
    initialize_database()
    live.clear_live_state()
    tasks.clear_tasks()
    reset_fake_peer_state()
    clear_registry_for_tests()
    yield path
    clear_registry_for_tests()
    reset_fake_peer_state()
    tasks.clear_tasks()
    live.clear_live_state()


def test_discover_resolve_and_invoke(acceptance_env, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    node = ensure_fake_peer_registered(db_path=acceptance_env)
    assert node.availability == "available"
    listed = service.list_nodes(db_path=acceptance_env)
    assert listed[0].node_id == FAKE_NODE_ID
    assert any(m.id == "fake:language" for m in listed[0].installed_models)

    reload_registry()
    resolved = resolve_model_for_operation(
        AiOperation.GENERATE,
        {"model_id": "node:0123456789abcdef:fake:language"},
    )
    assert resolved.descriptor.runtime == "execution_node"
    set_current_resolved_model(resolved)
    text = asyncio.run(ainvoke_text_for_resolved(resolved, "motif", purpose="generate"))
    assert text == "fake-execution-node:generate"
    assert _TOKEN not in caplog.text


def test_cancel_cooperates_with_operation_trace(acceptance_env, monkeypatch) -> None:
    ensure_fake_peer_registered(db_path=acceptance_env)
    reload_registry()
    resolved = resolve_model_for_operation(
        AiOperation.GENERATE,
        {"model_id": "node:0123456789abcdef:fake:language"},
    )
    set_current_resolved_model(resolved)
    run_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    mark_run_cancelled(run_id)

    async def _run() -> str:
        from unittest.mock import patch

        with patch("app.operation_trace.current_run_id", return_value=run_id):
            try:
                return await ainvoke_text_for_resolved(
                    resolved, "slow", purpose="generate"
                )
            except Exception as exc:  # noqa: BLE001
                return f"{type(exc).__name__}:{getattr(exc, 'code', '')}"

    outcome = asyncio.run(_run())
    assert is_run_cancelled(run_id)
    assert "operation_cancelled" in outcome

    # Controller task index: missing entry → typed not_found.
    with pytest.raises(Exception) as exc_info:
        asyncio.run(service.cancel_task("task_deadbeefdeadbeef", db_path=acceptance_env))
    assert getattr(exc_info.value, "code", "") == "execution_node_task_not_found"


def test_heartbeat_ttl_makes_model_unavailable(acceptance_env) -> None:
    ensure_fake_peer_registered(db_path=acceptance_env)
    reload_registry()
    resolve_model_for_operation(
        AiOperation.GENERATE,
        {"model_id": "node:0123456789abcdef:fake:language"},
    )
    with live._lock:  # noqa: SLF001
        live._STATE[FAKE_NODE_ID].last_heartbeat_mono = time.monotonic() - 60
    assert live.refresh_availability(FAKE_NODE_ID, ttl_seconds=15) == "unavailable"
    clear_registry_for_tests()
    reload_registry()
    with pytest.raises((ModelNotFoundError, ModelUnavailableError)):
        resolve_model_for_operation(
            AiOperation.GENERATE,
            {"model_id": "node:0123456789abcdef:fake:language"},
        )


def test_unauthenticated_list_is_401(acceptance_env, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    ensure_fake_peer_registered(db_path=acceptance_env)
    app = FastAPI()
    app.include_router(controller_router)
    client = TestClient(app)
    response = client.get("/ai/execution-nodes")
    assert response.status_code == 401
    assert _TOKEN not in caplog.text
