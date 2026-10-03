"""Security gates for ExecutionNode auth, SSRF, and no remote shell."""

from __future__ import annotations

import ast
import logging
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db import initialize_database, reset_database_initialization_cache
from app.routers.execution_nodes import router as controller_router
from app.routers.execution_worker import router as worker_router
from app.services import execution_node_runtime as live
from app.services import execution_node_tasks as tasks

_TOKEN = "security-token-fixture"
_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def secured(tmp_path, monkeypatch):
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
    app.include_router(worker_router)
    return TestClient(app)


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {_TOKEN}"}


def test_auth_matrix(secured: TestClient, monkeypatch, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    body = {
        "schema_version": "execution.node.registration.v1",
        "node_id": "node_0123456789abcdef",
        "display_name": "Box",
        "address": "http://10.0.0.9:8000",
        "capabilities": ["language_planner"],
        "installed_models": [],
        "resources": {"active_tasks": 0, "max_concurrency": 1},
    }
    ok = secured.post("/ai/execution-nodes/register", json=body, headers=_auth())
    assert ok.status_code == 201
    wrong = secured.post(
        "/ai/execution-nodes/register",
        json=body,
        headers={"Authorization": "Bearer wrong"},
    )
    assert wrong.status_code == 401
    missing = secured.post("/ai/execution-nodes/register", json=body)
    assert missing.status_code == 401
    query = secured.post("/ai/execution-nodes/register?token=" + _TOKEN, json=body)
    assert query.status_code == 401
    monkeypatch.setenv("AI_EXECUTION_NODES_ENABLED", "0")
    disabled = secured.post("/ai/execution-nodes/register", json=body, headers=_auth())
    assert disabled.status_code == 404
    app_records = [
        record
        for record in caplog.records
        if record.name.startswith("app.")
    ]
    assert all(_TOKEN not in record.getMessage() for record in app_records)
    assert all(_TOKEN not in str(getattr(record, "msg", "")) for record in app_records)


@pytest.mark.parametrize(
    "address",
    [
        "http://user:pass@10.0.0.1:8000",
        "http://169.254.1.1",
        "http://169.254.169.254",
        "http://metadata.google.internal",
    ],
)
def test_address_ssrf_rejected(secured: TestClient, address: str) -> None:
    response = secured.post(
        "/ai/execution-nodes/register",
        json={
            "schema_version": "execution.node.registration.v1",
            "node_id": "node_0123456789abcdef",
            "display_name": "Box",
            "address": address,
            "capabilities": ["language_planner"],
            "installed_models": [],
            "resources": {"active_tasks": 0, "max_concurrency": 1},
        },
        headers=_auth(),
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "execution_node_address_rejected"


def test_worker_shell_route_absent(secured: TestClient) -> None:
    response = secured.post("/execution/v1/shell", json={"command": "ls"}, headers=_auth())
    assert response.status_code == 404


def test_worker_dispatch_ast_forbids_subprocess_and_system() -> None:
    path = _ROOT / "app" / "services" / "execution_worker_dispatch.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    forbidden_names = {"subprocess", "system", "popen", "os"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in {"subprocess"}
        if isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] not in {"subprocess"}
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id == "os" and node.attr in {"system", "popen"}:
                raise AssertionError("os.system/popen forbidden in worker dispatch")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in {"system", "popen"}
    # Ensure the module source does not mention shell helpers.
    text = path.read_text(encoding="utf-8")
    assert "subprocess" not in text
    assert "os.system" not in text
    del forbidden_names


def test_worker_only_mount_condition() -> None:
    from app.execution_node_settings import execution_node_role, execution_nodes_enabled

    env = {
        "AI_EXECUTION_NODES_ENABLED": "1",
        "AI_EXECUTION_NODE_TOKEN": _TOKEN,
        "AI_EXECUTION_NODE_ROLE": "worker",
    }
    assert execution_nodes_enabled(env) is True
    assert execution_node_role(env) == "worker"
    worker_only = execution_nodes_enabled(env) and execution_node_role(env) == "worker"
    assert worker_only is True
    # main.py skips studio include_router when worker_only is true.


def test_worker_route_allowlist_snapshot() -> None:
    path = _ROOT / "app" / "routers" / "execution_worker.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    routes: set[str] = set()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            func = decorator.func
            if isinstance(func, ast.Attribute) and func.attr in {
                "get",
                "post",
                "put",
                "delete",
                "patch",
            }:
                if decorator.args and isinstance(decorator.args[0], ast.Constant):
                    routes.add(str(decorator.args[0].value))
    assert routes == {
        "/health",
        "/models",
        "/complete_text",
        "/tasks/{task_id}/cancel",
    }
    assert "/shell" not in routes
