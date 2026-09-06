import json
from pathlib import Path

import pytest

from app.db import initialize_database, reset_database_initialization_cache
from app.db.connection import get_connection
from app.services import project_store as store


@pytest.fixture
def project_db(tmp_path, monkeypatch):
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    reset_database_initialization_cache()
    initialize_database()
    return db_path


def test_migrations_apply_cleanly(project_db, caplog):
    with caplog.at_level("INFO"):
        reset_database_initialization_cache()
        initialize_database()

    with get_connection(project_db) as conn:
        versions = [row["version"] for row in conn.execute("SELECT version FROM schema_migrations")]
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

    assert "001_create_projects" in versions
    assert "projects" in tables
    assert "schema_migrations" in tables
    assert "Project database ready" in caplog.text or "Database migration applied" in caplog.text


def test_project_crud_duplicate_and_timestamps(project_db):
    created = store.create_project(
        "Song",
        composition={"schema_version": "composition.v1", "tracks": [], "sections": []},
        generation_provider="openai",
        generation_model="gpt-4o-mini",
        generation_prompt={"genre": "jazz"},
        db_path=project_db,
    )
    assert created.name == "Song"
    assert created.generation_provider == "openai"
    assert created.generation_model == "gpt-4o-mini"
    assert json.loads(created.generation_prompt_json)["genre"] == "jazz"
    assert "api_key" not in (created.generation_prompt_json or "")

    listed = store.list_projects(db_path=project_db)
    assert len(listed) == 1
    assert listed[0].id == created.id

    updated = store.update_project(
        created.id,
        name="Song Renamed",
        composition={"schema_version": "composition.v1", "tracks": [{"events": [1]}], "sections": []},
        db_path=project_db,
    )
    assert updated.name == "Song Renamed"
    assert updated.updated_at >= created.updated_at

    duplicated = store.duplicate_project(created.id, db_path=project_db)
    assert duplicated.id != created.id
    assert duplicated.name.endswith(" (copy)")
    assert duplicated.composition_json == updated.composition_json
    assert duplicated.generation_provider == "openai"

    store.delete_project(duplicated.id, db_path=project_db)
    with pytest.raises(store.ProjectNotFoundError):
        store.get_project(duplicated.id, db_path=project_db)


def test_missing_project_warns(project_db, caplog):
    with caplog.at_level("WARNING"):
        with pytest.raises(store.ProjectNotFoundError):
            store.get_project("missing", db_path=project_db)
    assert "Project not found" in caplog.text
