"""Manifest honesty and digest assembly tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.db import initialize_database
from app.db.connection import get_connection, reset_database_initialization_cache
from app.services import content_provenance_store as store
from app.services.content_provenance_manifest import assemble_provenance_manifest


@pytest.fixture
def project_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    reset_database_initialization_cache()
    initialize_database()
    return db_path


def test_assemble_manifest_honesty_false_and_digest_stable(project_db: Path) -> None:
    store.insert_provenance_record(
        None,
        {
            "schema_version": "content.provenance.record.v1",
            "record_id": "cprov_0000000000000001",
            "project_id": "p1",
            "artifact_kind": "composition_revision",
            "artifact_id": "rev_a",
            "artifact_fingerprint_prefix": "ab" * 8,
            "operation": "human_edit",
            "actor_kind": "human",
            "parent_record_ids": [],
            "parent_artifacts": [],
            "trust_class": "mukit_internal",
            "created_at": "2026-10-03T00:00:00Z",
        },
        db_path=project_db,
    )
    store.insert_provenance_record(
        None,
        {
            "schema_version": "content.provenance.record.v1",
            "record_id": "cprov_0000000000000002",
            "project_id": "p1",
            "artifact_kind": "neural_render",
            "artifact_id": "nar_1",
            "artifact_fingerprint_prefix": "cd" * 8,
            "operation": "neural_render",
            "actor_kind": "ai",
            "parent_record_ids": ["cprov_0000000000000001"],
            "parent_artifacts": [],
            "trust_class": "mukit_internal",
            "created_at": "2026-10-03T00:00:01Z",
        },
        db_path=project_db,
    )
    with get_connection(project_db) as conn:
        first = assemble_provenance_manifest(
            conn,
            project_id="p1",
            artifact_kind="neural_render",
            artifact_id="nar_1",
            c2pa_attached=True,
            c2pa_fake_mode=True,
            c2pa_status="fake",
        )
        second = assemble_provenance_manifest(
            conn,
            project_id="p1",
            artifact_kind="neural_render",
            artifact_id="nar_1",
            c2pa_attached=True,
            c2pa_fake_mode=True,
            c2pa_status="fake",
        )
    assert first.honesty.cryptographic is False
    assert first.manifest_digest_prefix == second.manifest_digest_prefix
    assert len(first.records) >= 2
