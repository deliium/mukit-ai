"""Open a planted composition.v1 project after upgrading old Alembic schemas."""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient

from app.db.connection import _alembic_config, reset_database_initialization_cache
from app.main import app
from tests.studio_acceptance.invariants import (
    assert_ardour_corruption_guards,
    assert_composition_source_of_truth,
    assert_v5_sidecar_tables,
)
from tests.test_composition_schema import valid_composition


def _v1_payload() -> dict:
    payload = valid_composition()
    payload["tracks"][0]["events"][0]["id"] = "keep-me"
    payload["tracks"][0]["events"][0]["pitch"] = "C4"
    return payload


def _tables(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    return {row[0] for row in rows}


def _version(db_path: Path) -> str:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    assert row is not None
    return str(row[0])


def _composition_text(db_path: Path) -> str:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT composition_json FROM projects WHERE id = 'v1-home'").fetchone()
    assert row is not None
    return str(row[0])


def _plant(db_path: Path, revision: str, raw: str) -> None:
    command.upgrade(_alembic_config(db_path), revision)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO projects (id, name, composition_json, created_at, updated_at)
            VALUES ('v1-home', 'V1 Home', ?, '2026-09-14T00:00:00Z', '2026-09-14T00:00:00Z')
            """,
            (raw,),
        )


@pytest.mark.parametrize("revision", ["20260914_0001", "20260925_0011"])
def test_open_v1_after_upgrade_to_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    revision: str,
) -> None:
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.delenv("COLLABORATION_ENABLED", raising=False)
    reset_database_initialization_cache()
    raw = json.dumps(_v1_payload(), ensure_ascii=False, separators=(",", ":"))
    _plant(db_path, revision, raw)
    if revision == "20260925_0011":
        assert "autonomous_runs" not in _tables(db_path)

    head = ScriptDirectory.from_config(_alembic_config(db_path)).get_current_head()
    command.upgrade(_alembic_config(db_path), head)
    assert _version(db_path) == head
    assert _composition_text(db_path) == raw
    assert "autonomous_runs" in _tables(db_path)

    caplog.set_level(logging.DEBUG)
    reset_database_initialization_cache()
    with TestClient(app) as client:
        opened = client.get("/projects/v1-home")
    assert opened.status_code == 200, opened.text
    composition = opened.json()["composition"]
    assert composition["schema_version"] == "composition.v2"
    assert composition["tracks"][0]["events"][0]["id"] == "keep-me"
    assert composition["tracks"][0]["events"][0]["pitch"] == "C4"
    dumped = json.dumps(opened.json())
    assert "composition.v3" not in dumped
    assert "composition.v4" not in dumped
    assert "composition.v5" not in dumped
    assert_composition_source_of_truth(composition)
    assert_v5_sidecar_tables(db_path)
    assert raw not in caplog.text
    stored = json.loads(_composition_text(db_path))
    assert stored["schema_version"] == "composition.v2"
    assert "composition.v3" not in stored
    assert "composition.v4" not in stored
    assert "composition.v5" not in stored


def test_v5_invariants_and_ardour_guards() -> None:
    assert_ardour_corruption_guards()
