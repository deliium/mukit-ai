"""Soft-fail capture never aborts primary writers."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from app.content_provenance_schemas import ContentProvenanceError
from app.db import initialize_database
from app.db.connection import get_connection, reset_database_initialization_cache
from app.project_history_schemas import DurableCommitRequest, RevisionOperationType
from app.services import content_provenance_store as store
from app.services.content_provenance_capture import (
    capture_revision_provenance,
    record_provenance_safe,
)
from app.services.project_history import commit_revision
from tests.test_composition_v2_schema import minimal_v2


@pytest.fixture
def project_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    reset_database_initialization_cache()
    initialize_database()
    return db_path


def test_record_provenance_safe_skips_cycle(
    project_db: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    store.insert_provenance_record(
        None,
        {
            "schema_version": "content.provenance.record.v1",
            "record_id": "cprov_0000000000000001",
            "project_id": "p1",
            "artifact_kind": "composition_revision",
            "artifact_id": "rev_a",
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
            "artifact_kind": "composition_revision",
            "artifact_id": "rev_b",
            "operation": "human_edit",
            "actor_kind": "human",
            "parent_record_ids": ["cprov_0000000000000001"],
            "parent_artifacts": [],
            "trust_class": "mukit_internal",
            "created_at": "2026-10-03T00:00:00Z",
        },
        db_path=project_db,
    )
    with caplog.at_level(logging.WARNING, logger="app.services.content_provenance_capture"):
        result = record_provenance_safe(
            None,
            {
                "schema_version": "content.provenance.record.v1",
                "record_id": "cprov_0000000000000001",
                "project_id": "p1",
                "artifact_kind": "composition_revision",
                "artifact_id": "rev_a",
                "operation": "human_edit",
                "actor_kind": "human",
                "parent_record_ids": ["cprov_0000000000000002"],
                "parent_artifacts": [],
                "trust_class": "mukit_internal",
                "created_at": "2026-10-03T00:00:00Z",
            },
            db_path=project_db,
        )
    assert result is None
    assert any(
        getattr(record, "code", None) == "provenance_cycle"
        for record in caplog.records
        if record.name == "app.services.content_provenance_capture"
    )
    with get_connection(project_db) as conn:
        assert store.count_provenance_records(conn, project_id="p1") == 2


def test_forced_cycle_during_commit_leaves_revision(
    project_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from copy import deepcopy

    from app.composition_schemas import CompositionV2
    from app.services import project_store as project_store_mod
    from app.services.project_history import get_revision_detail, list_branches

    created = project_store_mod.create_project(
        "Soft fail",
        composition=minimal_v2(),
        project_id="soft-1",
        db_path=project_db,
    )
    project_id = created.id
    branches = list_branches(project_id, db_path=project_db)
    branch = branches.branches[0]
    payload = deepcopy(minimal_v2())
    payload["tracks"][0]["events"] = [
        {
            "pitch": "D4",
            "start_tick": 0,
            "duration_ticks": 480,
            "velocity": 80,
        }
    ]

    def _boom(*_args, **_kwargs):
        raise ContentProvenanceError("provenance_cycle", "cycle", http_status=422)

    monkeypatch.setattr(
        "app.services.content_provenance_capture.insert_provenance_record",
        _boom,
    )

    request = DurableCommitRequest(
        branch_id=branch.id,
        expected_active_branch_id=branch.id,
        expected_working_version=branch.working_version,
        expected_head_revision_id=branch.head_revision_id,
        expected_source_fingerprint=branch.working_fingerprint,
        composition=CompositionV2.model_validate(payload),
        operation_type=RevisionOperationType.MANUAL_CHECKPOINT,
        name="soft-fail-checkpoint",
    )
    response = commit_revision(project_id, request, db_path=project_db)
    assert response.revision_created is True
    head = response.current_revision_id

    with get_connection(project_db) as conn:
        skipped = capture_revision_provenance(
            conn,
            project_id=project_id,
            revision_id=head,
            operation_type=RevisionOperationType.MANUAL_CHECKPOINT.value,
            fingerprint=response.working_fingerprint,
            prior_revision_id=branch.head_revision_id,
            revision_created=True,
        )
    assert skipped is None
    detail = get_revision_detail(project_id, head, db_path=project_db)
    assert detail.revision.id == head
    refreshed = list_branches(project_id, db_path=project_db)
    assert refreshed.branches[0].head_revision_id == head
