"""SQLite store tests for composer profiles (CRUD, CAS, caps, secrets)."""

from __future__ import annotations

import pytest

from app.composer_profile_schemas import ComposerProfileError, PreferenceFields
from app.db import initialize_database, reset_database_initialization_cache
from app.db.connection import get_connection
from app.services import composer_profile_store as store


@pytest.fixture
def project_db(tmp_path, monkeypatch):
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    reset_database_initialization_cache()
    initialize_database()
    return db_path


def test_migration_creates_composer_profiles_table(project_db):
    with get_connection(project_db) as conn:
        revision = conn.execute("SELECT version_num FROM alembic_version").fetchone()[
            "version_num"
        ]
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert revision == "20260922_0004"
    assert "composer_profiles" in tables


def test_create_list_get_update_delete(project_db):
    created = store.create_profile(name="My Cinematic Style", db_path=project_db)
    assert created.id.startswith("prof_")
    assert created.name == "My Cinematic Style"
    assert created.schema_version == "composer.profile.v1"

    listed = store.list_profiles(db_path=project_db)
    assert len(listed) == 1
    assert listed[0].id == created.id
    assert listed[0].source_count == 0

    fetched = store.get_profile(created.id, db_path=project_db)
    assert fetched.updated_at == created.updated_at

    updated = store.update_profile(
        created.id,
        expected_updated_at=created.updated_at,
        name="Cinematic v2",
        explicit=PreferenceFields(midi_mean_band="high"),
        db_path=project_db,
    )
    assert updated.name == "Cinematic v2"
    assert updated.explicit.midi_mean_band == "high"
    assert updated.created_at == created.created_at

    store.delete_profile(created.id, db_path=project_db)
    with pytest.raises(ComposerProfileError) as exc:
        store.get_profile(created.id, db_path=project_db)
    assert exc.value.code == "composer_profile_not_found"


def test_name_conflict_casefold(project_db):
    store.create_profile(name="Epic Brass", db_path=project_db)
    with pytest.raises(ComposerProfileError) as exc:
        store.create_profile(name="epic brass", db_path=project_db)
    assert exc.value.code == "composer_profile_name_conflict"
    assert exc.value.http_status == 409


def test_cas_conflict(project_db):
    created = store.create_profile(name="CAS Profile", db_path=project_db)
    with pytest.raises(ComposerProfileError) as exc:
        store.update_profile(
            created.id,
            expected_updated_at="1999-01-01T00:00:00Z",
            name="Nope",
            db_path=project_db,
        )
    assert exc.value.code == "composer_profile_conflict"
    assert exc.value.http_status == 409


def test_max_profiles_cap(project_db, monkeypatch):
    monkeypatch.setenv("COMPOSER_PROFILE_MAX_PROFILES", "2")
    store.create_profile(name="One", db_path=project_db)
    store.create_profile(name="Two", db_path=project_db)
    with pytest.raises(ComposerProfileError) as exc:
        store.create_profile(name="Three", db_path=project_db)
    assert exc.value.code == "composer_profile_cap_exceeded"


def test_secret_field_rejected(project_db):
    with pytest.raises(ComposerProfileError) as exc:
        store.create_profile(
            name="Leaky",
            notes="here is my api_key sk-abcdefghijklmnopqrstuvwxyz",
            db_path=project_db,
        )
    assert exc.value.code == "composer_profile_forbidden_payload"
