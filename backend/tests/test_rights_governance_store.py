"""SQLite rights registry CAS, upsert, and project-delete GC."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from app.db import initialize_database
from app.db.connection import get_connection, reset_database_initialization_cache
from app.rights_governance_schemas import RightsGovernanceError, project_allowed_uses
from app.services import rights_governance_store as store
from app.services.project_store import create_project, delete_project


@pytest.fixture
def project_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    reset_database_initialization_cache()
    initialize_database()
    return db_path


def _entry_payload(
    *,
    source_id: str = "proj_rights",
    use_policy: str = "reference_only",
    ownership_class: str = "licensed",
    license: str | None = "CC-BY-4.0",
    entry_version: int = 1,
) -> dict:
    return {
        "schema_version": "rights.registry.entry.v1",
        "entry_id": "rights_" + "e5" * 8,
        "source_kind": "project",
        "source_id": source_id,
        "ownership_class": ownership_class,
        "use_policy": use_policy,
        "allowed_uses": project_allowed_uses(use_policy),  # type: ignore[arg-type]
        "license": license,
        "license_spdx": license,
        "verification_status": "verified",
        "entry_version": entry_version,
        "created_at": "2026-10-03T00:00:00Z",
        "updated_at": "2026-10-03T00:00:00Z",
    }


def test_upsert_get_and_cas_conflict(project_db: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="app.services.rights_governance_store"):
        stored = store.upsert_rights_entry(_entry_payload(), db_path=project_db)
    assert stored.use_policy == "reference_only"
    loaded = store.get_rights_entry("project", "proj_rights", db_path=project_db)
    assert loaded is not None
    assert loaded.entry_id == stored.entry_id
    assert loaded.entry_version == 1

    updated = store.upsert_rights_entry(
        _entry_payload(use_policy="training_allowed", license="CC0-1.0"),
        expected_version=1,
        db_path=project_db,
    )
    assert updated.entry_version == 2
    assert updated.use_policy == "training_allowed"

    with pytest.raises(RightsGovernanceError) as raised:
        store.upsert_rights_entry(
            _entry_payload(use_policy="no_training", license=None),
            expected_version=1,
            db_path=project_db,
        )
    assert raised.value.code == "rights_cas_conflict"
    assert raised.value.http_status == 409
    assert "attribution" not in caplog.text.lower() or "body" not in caplog.text


def test_project_delete_gcs_rights_rows(project_db: Path) -> None:
    record = create_project("Rights GC", composition=None, db_path=project_db)
    store.upsert_rights_entry(
        _entry_payload(source_id=record.id, use_policy="reference_only", license=None),
        db_path=project_db,
    )
    assert store.get_rights_entry("project", record.id, db_path=project_db) is not None
    delete_project(record.id, db_path=project_db)
    assert store.get_rights_entry("project", record.id, db_path=project_db) is None


def test_list_by_prefix(project_db: Path) -> None:
    store.upsert_rights_entry(_entry_payload(source_id="proj_alpha"), db_path=project_db)
    store.upsert_rights_entry(
        _entry_payload(
            source_id="proj_alpha:rev1",
        )
        | {
            "source_kind": "composition_revision",
            "entry_id": "rights_" + "f6" * 8,
            "license": None,
            "license_spdx": None,
        },
        db_path=project_db,
    )
    listed = store.list_entries_by_source_prefix("project", "proj_a", db_path=project_db)
    assert len(listed) == 1
    assert listed[0].source_id == "proj_alpha"
