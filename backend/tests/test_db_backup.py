"""Offline SQLite backup and restore of the project database."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest

from app.db.backup import main
from app.db.connection import reset_database_initialization_cache, run_alembic_upgrade
from app.services.project_store import create_project, get_project


def _live_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "live" / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.delenv("DATASET_ROOT", raising=False)
    reset_database_initialization_cache()
    run_alembic_upgrade(db_path)
    return db_path


def test_backup_round_trip_keeps_both_projects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    db_path = _live_db(tmp_path, monkeypatch)
    first = create_project("Alpha", project_id="alpha", db_path=db_path)
    second = create_project("Beta", project_id="beta", db_path=db_path)
    backup_path = tmp_path / "var" / "backups" / "projects.db"
    caplog.set_level(logging.INFO)

    assert main(["backup", "--dest", str(backup_path)]) == 0
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM projects WHERE id = ?", (second.id,))
    assert get_project(first.id, db_path=db_path).name == "Alpha"
    with pytest.raises(Exception):
        get_project(second.id, db_path=db_path)

    restored = tmp_path / "var" / "restore" / "projects.db"
    assert main(["restore", "--from", str(backup_path), "--dest", str(restored)]) == 0
    assert get_project(first.id, db_path=restored).name == "Alpha"
    assert get_project(second.id, db_path=restored).name == "Beta"
    assert any(record.message == "backup_finish" and record.byte_size > 0 for record in caplog.records)
    assert str(db_path) not in caplog.text
    assert "Alpha" not in caplog.text
    assert "Beta" not in caplog.text


def test_restore_refuses_live_database_and_dataset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    db_path = _live_db(tmp_path, monkeypatch)
    create_project("Only", project_id="only", db_path=db_path)
    backup_path = tmp_path / "backups" / "projects.db"
    assert main(["backup", "--dest", str(backup_path)]) == 0
    caplog.set_level(logging.WARNING)

    assert main(["restore", "--from", str(backup_path), "--dest", str(db_path)]) == 2
    assert main(["backup", "--dest", str(db_path)]) == 2
    dataset = tmp_path / "datasets"
    dataset.mkdir()
    monkeypatch.setenv("DATASET_ROOT", str(dataset))
    nested = dataset / "projects.db"
    assert main(["restore", "--from", str(backup_path), "--dest", str(nested)]) == 2
    assert any(getattr(record, "reason", None) == "project_db_path" for record in caplog.records)
    assert any(getattr(record, "reason", None) == "inside_dataset_root" for record in caplog.records)
    assert str(dataset) not in caplog.text


def test_missing_source_exits_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "missing.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    assert main(["backup", "--dest", str(tmp_path / "out.db")]) == 2
    assert main(["restore", "--from", str(tmp_path / "nope.db"), "--dest", str(tmp_path / "restored.db")]) == 2
