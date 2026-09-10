"""Schema bounds and list-DTO safety for project history."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.project_history_schemas import (
    BRANCH_NAME_MAX_LENGTH,
    USER_INSTRUCTION_MAX_LENGTH,
    AffectedBarRange,
    DeclaredScope,
    DurableCommitRequest,
    ProjectRevisionConflictBody,
    RevisionListItem,
    RevisionListResponse,
    RevisionOperationType,
)
from app.services.project_history_store import normalize_branch_name


def test_operation_enum_includes_restore():
    assert RevisionOperationType.REVISION_RESTORE.value == "revision-restore"
    assert "generate-apply" in RevisionOperationType._value2member_map_


def test_affected_range_order_validated():
    with pytest.raises(ValidationError):
        AffectedBarRange(start_bar=4, end_bar=2)


def test_declared_scope_dedupes_track_ids():
    scope = DeclaredScope(track_ids=["a", "a", "b"])
    assert scope.track_ids == ["a", "b"]


def test_durable_commit_rejects_restore_operation():
    with pytest.raises(ValidationError):
        DurableCommitRequest.model_validate(
            {
                "branch_id": "b1",
                "expected_active_branch_id": "b1",
                "expected_working_version": 0,
                "expected_head_revision_id": "r1",
                "expected_source_fingerprint": "composition.snapshot.v1:null",
                "operation_type": "revision-restore",
            }
        )


def test_instruction_max_length():
    with pytest.raises(ValidationError):
        DurableCommitRequest.model_validate(
            {
                "branch_id": "b1",
                "expected_active_branch_id": "b1",
                "expected_working_version": 0,
                "expected_head_revision_id": "r1",
                "expected_source_fingerprint": "composition.snapshot.v1:null",
                "operation_type": "manual-checkpoint",
                "ai": {"user_instruction": "x" * (USER_INSTRUCTION_MAX_LENGTH + 1)},
            }
        )


def test_branch_name_max_length():
    assert BRANCH_NAME_MAX_LENGTH == 80


def test_unicode_branch_name_normalization():
    assert normalize_branch_name("Café") == normalize_branch_name("CAFÉ")
    assert normalize_branch_name("Original") == normalize_branch_name("original")
    # Compatibility equivalents collapse under NFKC+casefold.
    assert normalize_branch_name("ﬁnal") == normalize_branch_name("FINAL")


def test_revision_list_item_has_no_composition_field():
    item = RevisionListItem(
        id="r1",
        project_id="p1",
        sequence=1,
        operation_type=RevisionOperationType.PROJECT_CREATE,
        snapshot_fingerprint="composition.snapshot.v1:null",
        created_at="2026-01-01T00:00:00Z",
        has_user_instruction=True,
    )
    dumped = item.model_dump()
    assert "composition" not in dumped
    assert dumped["has_user_instruction"] is True
    assert "user_instruction" not in dumped


def test_revision_list_response_metadata_only():
    response = RevisionListResponse(revisions=[], next_before_sequence=None)
    assert "composition" not in response.model_dump()


def test_conflict_body_is_bounded_ids_only():
    body = ProjectRevisionConflictBody(
        project_id="p1",
        expected_active_branch_id="b1",
        current_active_branch_id="b2",
        expected_working_version=1,
        current_working_version=2,
        expected_source_fingerprint_prefix="abcd",
        current_working_fingerprint_prefix="efgh",
    )
    payload = body.model_dump()
    assert payload["code"] == "project_revision_conflict"
    assert "composition" not in payload
    assert "events" not in payload
    assert all(not key.endswith("_json") for key in payload)
