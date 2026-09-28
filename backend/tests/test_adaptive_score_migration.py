"""Alembic upgrade/downgrade for adaptive_scores leaves composition bytes intact."""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.script import ScriptDirectory

from app.db.connection import _alembic_config, reset_database_initialization_cache
from tests.test_composition_schema import valid_composition


def _composition() -> str:
    payload = valid_composition()
    payload["tracks"][0]["events"][0]["pitch"] = "C4"
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


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


def _text(db_path: Path) -> str:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT composition_json FROM projects WHERE id = 'v1-home'"
        ).fetchone()
    assert row is not None
    return str(row[0])


def test_adaptive_scores_migration_round_trip(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    db_path = tmp_path / "projects.db"
    reset_database_initialization_cache()
    raw = _composition()
    command.upgrade(_alembic_config(db_path), "20260926_0017")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO projects (id, name, composition_json, created_at, updated_at)
            VALUES ('v1-home', 'V1 Home', ?, '2026-09-14T00:00:00Z', '2026-09-14T00:00:00Z')
            """,
            (raw,),
        )
    head = ScriptDirectory.from_config(_alembic_config(db_path)).get_current_head()
    assert head == "20260928_0018"
    caplog.set_level(logging.INFO)
    command.upgrade(_alembic_config(db_path), "head")
    assert _version(db_path) == "20260928_0018"
    assert "adaptive_scores" in _tables(db_path)
    assert _text(db_path) == raw
    log_blob = " ".join(record.getMessage() for record in caplog.records)
    assert "20260928_0018" in log_blob
    assert raw not in log_blob
    assert "C4" not in log_blob

    command.downgrade(_alembic_config(db_path), "20260926_0017")
    assert _version(db_path) == "20260926_0017"
    assert "adaptive_scores" not in _tables(db_path)
    assert _text(db_path) == raw

    command.upgrade(_alembic_config(db_path), "head")
    assert _version(db_path) == "20260928_0018"
    assert "adaptive_scores" in _tables(db_path)
    assert _text(db_path) == raw
