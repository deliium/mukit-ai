"""SQLite persistence for musical universes and memberships."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest

from app.db.connection import get_connection, reset_database_initialization_cache
from app.db import initialize_database
from app.musical_universe_schemas import MusicalUniverseError
from app.services import musical_universe_store as store
from app.services.project_store import create_project, delete_project


@pytest.fixture
def project_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    reset_database_initialization_cache()
    initialize_database()
    return db_path


def test_create_unique_name_and_revision_cas(
    project_db: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    project = create_project("Cue A", project_id="project-a", db_path=project_db)
    with caplog.at_level(logging.INFO, logger="app.services.musical_universe_store"):
        created = store.create_universe("  North  Theme ", project.id, db_path=project_db)
    assert created.document_revision == 1
    assert created.member_project_ids == [project.id]
    assert created.universe.entities == []
    assert "body_json" not in caplog.text
    assert "North" not in caplog.text
    inserted = [
        record
        for record in caplog.records
        if record.name == "app.services.musical_universe_store"
        and record.getMessage() == "Musical universe inserted"
    ]
    assert inserted[-1].universe_id == created.id
    assert inserted[-1].document_revision == 1
    assert inserted[-1].entity_count == 0
    assert inserted[-1].theme_count == 0

    other = create_project("Cue B", project_id="project-b", db_path=project_db)
    with pytest.raises(MusicalUniverseError) as conflict:
        store.create_universe("north theme", other.id, db_path=project_db)
    assert conflict.value.code == "musical_universe_name_conflict"
    assert conflict.value.http_status == 409

    renamed = created.universe.model_copy(update={"name": "South Theme"})
    with pytest.raises(MusicalUniverseError) as stale:
        store.update_universe_document(renamed, expected_revision=4, db_path=project_db)
    assert stale.value.code == "musical_universe_conflict"
    assert store.get_universe(created.id, db_path=project_db).document_revision == 1

    bumped = store.update_universe_document(renamed, expected_revision=1, db_path=project_db)
    assert bumped.document_revision == 2
    assert bumped.universe.name == "South Theme"
    assert bumped.member_project_ids == [project.id]


def test_caller_connection_is_not_committed(project_db: Path) -> None:
    project = create_project("Cue A", project_id="project-a", db_path=project_db)
    created = store.create_universe("Shared", project.id, db_path=project_db)
    renamed = created.universe.model_copy(update={"name": "Rolled Back"})
    conn = sqlite3.connect(project_db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        store.update_universe_document(
            renamed,
            expected_revision=1,
            connection=conn,
        )
        conn.rollback()
    finally:
        conn.close()
    reloaded = store.get_universe(created.id, db_path=project_db)
    assert reloaded.document_revision == 1
    assert reloaded.universe.name == "Shared"


def test_delete_member_project_keeps_universe_and_other_member(project_db: Path) -> None:
    first = create_project("Cue A", project_id="project-a", db_path=project_db)
    second = create_project("Cue B", project_id="project-b", db_path=project_db)
    created = store.create_universe("Franchise", first.id, db_path=project_db)
    store.add_member(created.id, second.id, db_path=project_db)
    delete_project(first.id, db_path=project_db)

    reloaded = store.get_universe(created.id, db_path=project_db)
    assert reloaded.member_project_ids == [second.id]
    with get_connection(project_db) as conn:
        universe_row = conn.execute(
            "SELECT id FROM musical_universes WHERE id = ?",
            (created.id,),
        ).fetchone()
        gone = conn.execute(
            "SELECT project_id FROM musical_universe_members WHERE project_id = ?",
            (first.id,),
        ).fetchone()
    assert universe_row is not None
    assert gone is None
