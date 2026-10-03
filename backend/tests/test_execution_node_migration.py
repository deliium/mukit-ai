"""Alembic upgrade and downgrade for execution_nodes."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command

from app.db.connection import _alembic_config, reset_database_initialization_cache


def _tables(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {row[0] for row in rows}


def _version(db_path: Path) -> str:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    assert row is not None
    return str(row[0])


def test_execution_nodes_migration_upgrades_and_downgrades(tmp_path: Path) -> None:
    db_path = tmp_path / "projects.db"
    reset_database_initialization_cache()
    cfg = _alembic_config(db_path)
    command.upgrade(cfg, "20261002_0023")
    assert "execution_nodes" not in _tables(db_path)
    command.upgrade(cfg, "20261003_0024")
    assert _version(db_path) == "20261003_0024"
    assert "execution_nodes" in _tables(db_path)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM execution_nodes").fetchone()[0] == 0
    command.downgrade(cfg, "20261002_0023")
    assert _version(db_path) == "20261002_0023"
    assert "execution_nodes" not in _tables(db_path)
