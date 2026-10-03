"""SQLite store for execution node registration documents."""

from __future__ import annotations

import pytest

from app.db import initialize_database, reset_database_initialization_cache
from app.execution_node_schemas import ExecutionNodeError, ExecutionNodeV1
from app.services import execution_node_store as store


def _node(**overrides) -> ExecutionNodeV1:
    payload = {
        "schema_version": "execution.node.v1",
        "node_id": "node_0123456789abcdef",
        "display_name": "Spare box",
        "address": "http://192.168.1.20:8000",
        "role": "worker",
        "capabilities": ["language_planner"],
        "hardware": {"device_class": "cpu"},
        "available_runtimes": ["fake"],
        "installed_models": [
            {
                "id": "fake:language",
                "display_name": "Fake",
                "primary_capability": "language_planner",
                "runtime": "fake",
                "status": "ready",
            }
        ],
        "health": {"status": "ready"},
        "resources": {"active_tasks": 0, "max_concurrency": 1},
        "availability": "unavailable",
        "document_revision": 1,
    }
    payload.update(overrides)
    return ExecutionNodeV1.model_validate(payload)


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(path))
    reset_database_initialization_cache()
    initialize_database()
    return path


def test_upsert_get_list_delete(db_path) -> None:
    node = _node()
    store.upsert_node(node, db_path=db_path)
    loaded = store.get_node(node.node_id, db_path=db_path)
    assert loaded is not None
    assert loaded.display_name == "Spare box"
    assert loaded.availability == "unavailable"
    assert store.count_nodes(db_path=db_path) == 1
    assert [item.node_id for item in store.list_nodes(db_path=db_path)] == [node.node_id]
    store.delete_node(node.node_id, db_path=db_path)
    assert store.get_node(node.node_id, db_path=db_path) is None
    with pytest.raises(ExecutionNodeError) as exc:
        store.delete_node(node.node_id, db_path=db_path)
    assert exc.value.code == "execution_node_not_found"


def test_cas_heartbeat_conflict(db_path) -> None:
    store.upsert_node(_node(document_revision=1), db_path=db_path)
    updated = _node(
        document_revision=2,
        availability="available",
        last_heartbeat_at="2026-10-03T06:00:00Z",
    )
    store.cas_update_node(updated, expected_revision=1, db_path=db_path)
    loaded = store.get_node(updated.node_id, db_path=db_path)
    assert loaded is not None
    assert loaded.document_revision == 2
    assert loaded.availability == "available"
    stale = _node(document_revision=3, availability="busy")
    with pytest.raises(ExecutionNodeError) as exc:
        store.cas_update_node(stale, expected_revision=1, db_path=db_path)
    assert exc.value.code == "execution_node_conflict"
