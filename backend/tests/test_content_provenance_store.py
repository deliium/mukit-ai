"""SQLite provenance records, cycle refusal, caps, and project-delete GC."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from app.content_provenance_schemas import ContentProvenanceError
from app.content_provenance_settings import load_content_provenance_settings
from app.db import initialize_database
from app.db.connection import get_connection, reset_database_initialization_cache
from app.services import content_provenance_store as store
from app.services.project_store import create_project, delete_project


@pytest.fixture
def project_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    reset_database_initialization_cache()
    initialize_database()
    return db_path


def _record(
    index: int,
    *,
    project_id: str,
    operation: str = "human_edit",
    artifact_kind: str = "composition_revision",
    artifact_id: str | None = None,
    parents: list[str] | None = None,
) -> dict:
    return {
        "schema_version": "content.provenance.record.v1",
        "record_id": f"cprov_{index:016x}",
        "project_id": project_id,
        "artifact_kind": artifact_kind,
        "artifact_id": artifact_id or f"rev_{index}",
        "artifact_fingerprint_prefix": f"{index:02x}" * 8,
        "operation": operation,
        "actor_kind": "human" if operation == "human_edit" else "ai",
        "parent_record_ids": parents or [],
        "parent_artifacts": [],
        "trust_class": "mukit_internal",
        "created_at": "2026-10-03T00:00:00Z",
    }


def test_insert_chain_of_three_and_walk(project_db: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="app.services.content_provenance_store"):
        store.insert_provenance_record(None, _record(1, project_id="p1"), db_path=project_db)
        store.insert_provenance_record(
            None,
            _record(2, project_id="p1", parents=["cprov_0000000000000001"]),
            db_path=project_db,
        )
        store.insert_provenance_record(
            None,
            _record(
                3,
                project_id="p1",
                artifact_kind="neural_render",
                artifact_id="nar_1",
                operation="neural_render",
                parents=["cprov_0000000000000002"],
            ),
            db_path=project_db,
        )
    with get_connection(project_db) as conn:
        assert store.count_provenance_records(conn, project_id="p1") == 3
        leaf = store.list_records_for_artifact(
            conn, artifact_kind="neural_render", artifact_id="nar_1", project_id="p1"
        )
        walked = store.walk_parent_records(conn, leaf)
    assert [item.record_id for item in walked] == [
        "cprov_0000000000000003",
        "cprov_0000000000000002",
        "cprov_0000000000000001",
    ]
    inserted = [
        record
        for record in caplog.records
        if record.name == "app.services.content_provenance_store"
        and record.getMessage() == "Provenance record inserted"
    ]
    assert len(inserted) == 3
    assert inserted[0].operation == "human_edit"
    assert "body_json" not in caplog.text


def test_refuse_self_parent_and_diamond_cycle(project_db: Path) -> None:
    store.insert_provenance_record(None, _record(1, project_id="p1"), db_path=project_db)
    store.insert_provenance_record(
        None,
        _record(2, project_id="p1", parents=["cprov_0000000000000001"]),
        db_path=project_db,
    )
    store.insert_provenance_record(
        None,
        _record(3, project_id="p1", parents=["cprov_0000000000000002"]),
        db_path=project_db,
    )

    with pytest.raises(ContentProvenanceError) as self_edge:
        store.insert_provenance_record(
            None,
            {
                **_record(10, project_id="p1"),
                "record_id": "cprov_0000000000000010",
                "parent_record_ids": ["cprov_0000000000000010"],
            },
            db_path=project_db,
        )
    assert self_edge.value.code == "provenance_cycle"

    # Upsert leaf's ancestor to point at descendant → cycle.
    with pytest.raises(ContentProvenanceError) as cycle:
        store.insert_provenance_record(
            None,
            {
                **_record(1, project_id="p1"),
                "parent_record_ids": ["cprov_0000000000000003"],
            },
            db_path=project_db,
        )
    assert cycle.value.code == "provenance_cycle"
    with get_connection(project_db) as conn:
        assert store.count_provenance_records(conn, project_id="p1") == 3


def test_project_cap(project_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTENT_PROVENANCE_MAX_RECORDS_PER_PROJECT", "16")
    for index in range(1, 17):
        store.insert_provenance_record(
            None,
            _record(index, project_id="cap"),
            db_path=project_db,
        )
    with pytest.raises(ContentProvenanceError) as limited:
        store.insert_provenance_record(
            None,
            _record(17, project_id="cap"),
            db_path=project_db,
        )
    assert limited.value.code == "provenance_project_cap"
    settings = load_content_provenance_settings()
    assert settings.max_records_per_project == 16


def test_idempotent_neural_upsert(project_db: Path) -> None:
    first = store.insert_provenance_record(
        None,
        _record(
            1,
            project_id="p1",
            artifact_kind="neural_render",
            artifact_id="nar_x",
            operation="neural_render",
        ),
        db_path=project_db,
    )
    second = store.insert_provenance_record(
        None,
        {
            **_record(
                2,
                project_id="p1",
                artifact_kind="neural_render",
                artifact_id="nar_x",
                operation="neural_render",
            ),
            "model_id": "fake:musicgen",
            "artifact_fingerprint_prefix": "ff" * 8,
        },
        db_path=project_db,
    )
    assert second.record_id == first.record_id
    assert second.model_id == "fake:musicgen"
    with get_connection(project_db) as conn:
        assert store.count_provenance_records(conn, project_id="p1") == 1


def test_project_delete_removes_rows(project_db: Path) -> None:
    create_project("Provenance GC", project_id="proj-gc", db_path=project_db)
    store.insert_provenance_record(
        None,
        _record(1, project_id="proj-gc"),
        db_path=project_db,
    )
    with get_connection(project_db) as conn:
        assert store.count_provenance_records(conn, project_id="proj-gc") == 1
    delete_project("proj-gc", db_path=project_db)
    with get_connection(project_db) as conn:
        assert store.count_provenance_records(conn, project_id="proj-gc") == 0
