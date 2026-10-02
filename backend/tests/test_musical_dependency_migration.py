"""Alembic upgrade and downgrade for musical dependency edges."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from alembic import command

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
            "SELECT composition_json FROM projects WHERE id = 'cue-a'"
        ).fetchone()
    assert row is not None
    return str(row[0])


def test_musical_dependency_migration_upgrade_and_downgrade(tmp_path: Path) -> None:
    db_path = tmp_path / "projects.db"
    reset_database_initialization_cache()
    raw = _composition()
    cfg = _alembic_config(db_path)
    command.upgrade(cfg, "20261001_0020")
    assert _version(db_path) == "20261001_0020"
    assert "musical_dependency_edges" not in _tables(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO projects (id, name, composition_json, created_at, updated_at)
            VALUES ('cue-a', 'Cue A', ?, '2026-10-02T00:00:00Z', '2026-10-02T00:00:00Z')
            """,
            (raw,),
        )

    command.upgrade(cfg, "20261002_0021")
    assert _version(db_path) == "20261002_0021"
    assert "musical_dependency_edges" in _tables(db_path)
    assert _text(db_path) == raw

    command.downgrade(cfg, "20261001_0020")
    assert _version(db_path) == "20261001_0020"
    assert "musical_dependency_edges" not in _tables(db_path)
    assert _text(db_path) == raw
