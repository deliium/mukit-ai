"""CAS singleton store for scheduling policy."""

from __future__ import annotations

import sqlite3

import pytest

from app.db import initialize_database, reset_database_initialization_cache
from app.scheduling_schemas import SchedulingError, SchedulingPolicyV1
from app.services import scheduling_policy_store as store


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(path))
    monkeypatch.delenv("AI_SCHEDULING_ENABLED", raising=False)
    reset_database_initialization_cache()
    initialize_database()
    return path


def test_get_policy_materializes_env_without_insert(db_path, monkeypatch) -> None:
    monkeypatch.setenv("AI_SCHEDULING_DEFAULT_MODE", "memory_safe")
    policy = store.get_policy(db_path=db_path)
    assert policy.mode == "memory_safe"
    assert policy.document_revision == 1
    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM scheduling_policy").fetchone()[0]
    assert count == 0


def test_put_policy_cas_insert_and_conflict(db_path) -> None:
    first = store.put_policy(
        SchedulingPolicyV1(mode="fastest_available", document_revision=2),
        expected_revision=1,
        db_path=db_path,
    )
    assert first.document_revision == 2
    assert first.mode == "fastest_available"

    with pytest.raises(SchedulingError) as exc_info:
        store.put_policy(
            SchedulingPolicyV1(mode="prefer_local", document_revision=2),
            expected_revision=1,
            db_path=db_path,
        )
    assert exc_info.value.code == "scheduling_conflict"

    second = store.put_policy(
        SchedulingPolicyV1(mode="prefer_local", document_revision=3, allow_public_cloud=False),
        expected_revision=2,
        db_path=db_path,
    )
    assert second.document_revision == 3
    assert store.get_policy(db_path=db_path).mode == "prefer_local"
