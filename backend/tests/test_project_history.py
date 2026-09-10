"""Domain orchestration tests for project history services."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from app.composition_schemas import CompositionV2
from app.db import initialize_database, reset_database_initialization_cache
from app.db.connection import get_connection
from app.project_history_schemas import (
    AffectedBarRange,
    AiProvenance,
    ApplyAsBranchRequest,
    BranchCreateRequest,
    BranchRenameRequest,
    DeclaredScope,
    DurableCommitRequest,
    RestoreRevisionRequest,
    RevisionNameRequest,
    RevisionOperationType,
)
from app.services import project_history as history
from app.services import project_store as store
from app.services.composition_change_summary import CompositionScopeError
from app.services.project_history_store import (
    ProjectRevisionConflictError,
    save_branch_draft,
)

FIXTURE_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "fixtures" / "composition_v2_expressive.json"
)


@pytest.fixture
def project_db(tmp_path, monkeypatch):
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    reset_database_initialization_cache()
    initialize_database()
    return db_path


@pytest.fixture
def expressive_payload() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _active_state(project_id: str, db_path):
    record = store.get_project(project_id, db_path=db_path)
    with get_connection(db_path) as conn:
        branch = conn.execute(
            "SELECT * FROM project_branches WHERE id = ?",
            (record.active_branch_id,),
        ).fetchone()
        revision = conn.execute(
            "SELECT sequence FROM project_revisions WHERE id = ?",
            (record.current_revision_id,),
        ).fetchone()
    return record, branch, int(revision["sequence"])


def test_list_revisions_is_metadata_only_and_paginates(project_db, expressive_payload):
    created = store.create_project("Hist", composition=expressive_payload, db_path=project_db)
    for index, pitch in enumerate(["D4", "E4", "F4"]):
        record, branch, _ = _active_state(created.id, project_db)
        payload = deepcopy(expressive_payload)
        payload["tracks"][0]["events"][0]["pitch"] = pitch
        history.commit_revision(
            created.id,
            DurableCommitRequest(
                branch_id=branch["id"],
                expected_active_branch_id=branch["id"],
                expected_working_version=int(branch["working_version"]),
                expected_head_revision_id=record.current_revision_id,
                expected_source_fingerprint=branch["working_fingerprint"],
                composition=CompositionV2.model_validate(payload),
                operation_type=RevisionOperationType.MANUAL_CHECKPOINT,
                name=f"cp-{index}",
            ),
            db_path=project_db,
        )

    page = history.list_revisions(created.id, limit=2, db_path=project_db)
    assert len(page.revisions) == 2
    assert page.next_before_sequence is not None
    for item in page.revisions:
        dumped = item.model_dump()
        assert "composition" not in dumped
        assert item.has_user_instruction is False

    page2 = history.list_revisions(
        created.id,
        limit=2,
        before_sequence=page.next_before_sequence,
        db_path=project_db,
    )
    assert page2.revisions
    assert {item.id for item in page.revisions}.isdisjoint({item.id for item in page2.revisions})


def test_unicode_equivalent_branch_names_conflict(project_db, expressive_payload):
    created = store.create_project("Branches", composition=expressive_payload, db_path=project_db)
    record, branch, _ = _active_state(created.id, project_db)
    history.create_branch(
        created.id,
        BranchCreateRequest(name="Café Alt", from_revision_id=record.current_revision_id),
        db_path=project_db,
    )
    with pytest.raises(history.ProjectHistoryValidationError):
        history.create_branch(
            created.id,
            BranchCreateRequest(name="CAFÉ ALT", from_revision_id=record.current_revision_id),
            db_path=project_db,
        )


def test_rename_branch_rejects_unicode_collision(project_db, expressive_payload):
    created = store.create_project("Rename", composition=expressive_payload, db_path=project_db)
    record, branch, _ = _active_state(created.id, project_db)
    alt = history.create_branch(
        created.id,
        BranchCreateRequest(name="Alt", from_revision_id=record.current_revision_id),
        db_path=project_db,
    )
    with pytest.raises(history.ProjectHistoryValidationError):
        history.rename_branch(
            created.id,
            alt.id,
            BranchRenameRequest(name="ORIGINAL"),
            db_path=project_db,
        )


def test_commit_rejects_scope_escape(project_db, expressive_payload):
    created = store.create_project("Scope", composition=expressive_payload, db_path=project_db)
    record, branch, _ = _active_state(created.id, project_db)
    payload = deepcopy(expressive_payload)
    payload["tracks"][0]["events"][0]["pitch"] = "D4"
    with pytest.raises(CompositionScopeError):
        history.commit_revision(
            created.id,
            DurableCommitRequest(
                branch_id=branch["id"],
                expected_active_branch_id=branch["id"],
                expected_working_version=int(branch["working_version"]),
                expected_head_revision_id=record.current_revision_id,
                expected_source_fingerprint=branch["working_fingerprint"],
                composition=CompositionV2.model_validate(payload),
                operation_type=RevisionOperationType.AI_REGION_EDIT_APPLY,
                declared_scope=DeclaredScope(
                    track_ids=["not-this-track"],
                    ranges=[AffectedBarRange(start_bar=1, end_bar=1)],
                ),
                ai=AiProvenance(provider="fake", model="fake-deterministic", user_instruction="brighten"),
            ),
            db_path=project_db,
        )


def test_commit_derive_scope_and_name_revision(project_db, expressive_payload):
    created = store.create_project("Commit", composition=expressive_payload, db_path=project_db)
    record, branch, _ = _active_state(created.id, project_db)
    payload = deepcopy(expressive_payload)
    payload["tracks"][0]["events"][0]["pitch"] = "D4"
    result = history.commit_revision(
        created.id,
        DurableCommitRequest(
            branch_id=branch["id"],
            expected_active_branch_id=branch["id"],
            expected_working_version=int(branch["working_version"]),
            expected_head_revision_id=record.current_revision_id,
            expected_source_fingerprint=branch["working_fingerprint"],
            composition=CompositionV2.model_validate(payload),
            operation_type=RevisionOperationType.GENERATE_APPLY,
            ai=AiProvenance(
                provider="fake",
                model="fake-deterministic",
                user_instruction="new chorus idea",
                candidate_id="cand-1",
                warning_codes=["soft_warning"],
            ),
        ),
        db_path=project_db,
    )
    assert result.revision_created is True
    detail = history.get_revision_detail(
        created.id,
        result.current_revision_id,
        db_path=project_db,
    )
    assert detail.composition is not None
    assert detail.revision.has_user_instruction is True
    assert detail.revision.affected_track_ids
    named = history.name_revision(
        created.id,
        result.current_revision_id,
        RevisionNameRequest(name="Keep this"),
        db_path=project_db,
    )
    assert named.name == "Keep this"


def test_cross_project_revision_detail_not_found(project_db, expressive_payload):
    first = store.create_project("A", composition=expressive_payload, db_path=project_db)
    second = store.create_project("B", composition=expressive_payload, db_path=project_db)
    with pytest.raises(history.ProjectHistoryNotFoundError):
        history.get_revision_detail(second.id, first.current_revision_id, db_path=project_db)


def test_restore_and_apply_as_branch(project_db, expressive_payload):
    created = store.create_project("Restore", composition=expressive_payload, db_path=project_db)
    record, branch, _ = _active_state(created.id, project_db)
    root_id = record.current_revision_id
    payload = deepcopy(expressive_payload)
    payload["tracks"][0]["events"][0]["pitch"] = "D4"
    committed = history.commit_revision(
        created.id,
        DurableCommitRequest(
            branch_id=branch["id"],
            expected_active_branch_id=branch["id"],
            expected_working_version=int(branch["working_version"]),
            expected_head_revision_id=root_id,
            expected_source_fingerprint=branch["working_fingerprint"],
            composition=CompositionV2.model_validate(payload),
            operation_type=RevisionOperationType.MANUAL_CHECKPOINT,
        ),
        db_path=project_db,
    )
    record, branch, _ = _active_state(created.id, project_db)
    restored = history.restore_revision_command(
        created.id,
        root_id,
        RestoreRevisionRequest(
            branch_id=branch["id"],
            expected_active_branch_id=branch["id"],
            expected_working_version=int(branch["working_version"]),
            expected_head_revision_id=record.current_revision_id,
        ),
        db_path=project_db,
    )
    assert restored.operation_type == RevisionOperationType.REVISION_RESTORE
    assert restored.revision_created is True

    record, branch, _ = _active_state(created.id, project_db)
    candidate = deepcopy(expressive_payload)
    candidate["tracks"][0]["events"][0]["pitch"] = "G4"
    branched = history.apply_as_branch_command(
        created.id,
        ApplyAsBranchRequest(
            name="Chorus B",
            source_branch_id=branch["id"],
            expected_active_branch_id=branch["id"],
            expected_working_version=int(branch["working_version"]),
            expected_head_revision_id=record.current_revision_id,
            expected_source_fingerprint=branch["working_fingerprint"],
            composition=CompositionV2.model_validate(candidate),
            operation_type=RevisionOperationType.DEVELOPMENT_APPLY,
        ),
        db_path=project_db,
    )
    assert branched.active_branch_name == "Chorus B"
    branches = history.list_branches(created.id, db_path=project_db)
    original = next(item for item in branches.branches if item.name == "Original")
    assert original.head_revision_id == restored.current_revision_id
    # Branch-filtered history must bind JOIN params before WHERE project_id
    # (regression: swapped placeholders returned an empty list).
    alt_history = history.list_revisions(
        created.id,
        branch_id=branched.active_branch_id,
        limit=20,
        db_path=project_db,
    )
    assert len(alt_history.revisions) >= 2
    assert any(item.id == branched.current_revision_id for item in alt_history.revisions)
    assert any(item.id == restored.current_revision_id for item in alt_history.revisions)


def test_dirty_draft_commit_resolves_source_without_snapshot(project_db, expressive_payload):
    created = store.create_project("Dirty", composition=expressive_payload, db_path=project_db)
    record, branch, _ = _active_state(created.id, project_db)
    dirty = deepcopy(expressive_payload)
    dirty["tracks"][0]["events"][0]["pitch"] = "A4"
    with get_connection(project_db) as conn:
        draft = save_branch_draft(
            conn,
            created.id,
            branch_id=branch["id"],
            expected_active_branch_id=branch["id"],
            expected_working_version=int(branch["working_version"]),
            composition=CompositionV2.model_validate(dirty),
        )
    candidate = deepcopy(dirty)
    candidate["tracks"][0]["events"][0]["pitch"] = "B4"
    result = history.commit_revision(
        created.id,
        DurableCommitRequest(
            branch_id=branch["id"],
            expected_active_branch_id=branch["id"],
            expected_working_version=draft.working_version,
            expected_head_revision_id=record.current_revision_id,
            expected_source_fingerprint=draft.working_fingerprint,
            composition=CompositionV2.model_validate(candidate),
            operation_type=RevisionOperationType.ARRANGEMENT_APPLY,
            checkpoint_dirty_draft=True,
        ),
        db_path=project_db,
    )
    assert result.revision_created is True
    assert len(result.created_revision_ids) >= 1


def test_stale_head_conflict_is_bounded(project_db, expressive_payload):
    created = store.create_project("Conflict", composition=expressive_payload, db_path=project_db)
    record, branch, _ = _active_state(created.id, project_db)
    payload = deepcopy(expressive_payload)
    payload["tempo"] = 120
    with pytest.raises(ProjectRevisionConflictError) as exc:
        history.commit_revision(
            created.id,
            DurableCommitRequest(
                branch_id=branch["id"],
                expected_active_branch_id=branch["id"],
                expected_working_version=int(branch["working_version"]),
                expected_head_revision_id="missing-head",
                expected_source_fingerprint=branch["working_fingerprint"],
                composition=CompositionV2.model_validate(payload),
                operation_type=RevisionOperationType.MANUAL_CHECKPOINT,
            ),
            db_path=project_db,
        )
    detail = exc.value.bounded_detail()
    assert detail["code"] == "project_revision_conflict"
    assert "composition" not in detail
    assert all("event" not in key for key in detail)
