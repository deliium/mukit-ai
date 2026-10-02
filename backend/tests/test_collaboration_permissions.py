"""Role matrix, flag parser, origin sets, and collaboration error mapping."""

from __future__ import annotations

import logging

import pytest
from pydantic import ValidationError

from app.collaboration_schemas import (
    COLLABORATION_ERROR_HTTP,
    CollaborationActorCreateRequest,
    CollaborationCommentCreateRequest,
    CollaborationError,
    CollaborationMemberGrantRequest,
    CollaborationStatusResponse,
    map_collaboration_error_to_http,
)
from app.collaboration_settings import collaboration_enabled
from app.project_history_schemas import RevisionOperationType
from app.services.collaboration_permissions import (
    ACTIONS,
    AI_ORIGIN_OPERATIONS,
    HUMAN_ORIGIN_OPERATIONS,
    review_allowed_for_operation,
    revision_origin,
    role_allows,
)


def test_flag_off_for_unset_empty_and_zero():
    assert collaboration_enabled({}) is False
    assert collaboration_enabled({"COLLABORATION_ENABLED": ""}) is False
    assert collaboration_enabled({"COLLABORATION_ENABLED": "0"}) is False
    assert collaboration_enabled({"COLLABORATION_ENABLED": " 0 "}) is False


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "Yes", "on", " ON "])
def test_flag_on_for_documented_truthy_strings(raw: str):
    assert collaboration_enabled({"COLLABORATION_ENABLED": raw}) is True


def test_unrecognized_flag_is_off_and_does_not_log_the_value(caplog):
    caplog.set_level(logging.WARNING)
    assert collaboration_enabled({"COLLABORATION_ENABLED": "maybe-secret"}) is False
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert warnings
    assert warnings[-1].code == "collaboration_flag_unrecognized"
    assert "maybe-secret" not in caplog.text


def test_settings_loader_logs_enabled_once_for_default_env(caplog, monkeypatch):
    monkeypatch.delenv("COLLABORATION_ENABLED", raising=False)
    import app.collaboration_settings as settings

    settings._default_logged = False
    caplog.set_level(logging.INFO)
    assert collaboration_enabled() is False
    assert collaboration_enabled() is False
    info_records = [
        record
        for record in caplog.records
        if record.levelno == logging.INFO and getattr(record, "enabled", None) is False
    ]
    assert len(info_records) == 1


def test_role_matrix_matches_the_action_table():
    assert ACTIONS == {
        "read",
        "write_score",
        "write_audio",
        "comment",
        "review_open",
        "review_decide",
        "share",
        "delete_project",
        "train_adapter",
    }
    for action in ACTIONS:
        assert role_allows("owner", action) is True
    for action in ("read", "write_score", "write_audio", "comment", "review_open"):
        assert role_allows("editor", action) is True
    for action in ("review_decide", "share", "delete_project", "train_adapter"):
        assert role_allows("editor", action) is False
    assert role_allows("commenter", "read") is True
    assert role_allows("commenter", "comment") is True
    for action in ACTIONS - {"read", "comment"}:
        assert role_allows("commenter", action) is False
    assert role_allows("viewer", "read") is True
    for action in ACTIONS - {"read"}:
        assert role_allows("viewer", action) is False
    assert role_allows("stranger", "read") is False
    assert role_allows("owner", "not-an-action") is False


def test_origin_sets_partition_every_revision_operation():
    values = {item.value for item in RevisionOperationType}
    assert values == AI_ORIGIN_OPERATIONS | HUMAN_ORIGIN_OPERATIONS
    assert AI_ORIGIN_OPERATIONS.isdisjoint(HUMAN_ORIGIN_OPERATIONS)
    assert "project-create" in HUMAN_ORIGIN_OPERATIONS
    assert "pre-ai-checkpoint" in HUMAN_ORIGIN_OPERATIONS
    assert revision_origin("generate-apply") == "ai"
    assert revision_origin("manual-checkpoint") == "human"
    assert review_allowed_for_operation("project-create") is False
    assert review_allowed_for_operation("generate-apply") is True
    assert review_allowed_for_operation("pre-ai-checkpoint") is True


def test_error_codes_map_to_contract_statuses():
    expected = {
        "collaboration_disabled": 404,
        "collaboration_actor_unknown": 401,
        "collaboration_not_member": 403,
        "collaboration_role_denied": 403,
        "collaboration_review_open": 409,
        "collaboration_review_decided": 409,
        "collaboration_member_exists": 409,
        "collaboration_owner_required": 422,
        "collaboration_review_not_allowed": 422,
        "comment_anchor_missing": 422,
        "persistence_secret_rejected": 422,
    }
    assert COLLABORATION_ERROR_HTTP == expected
    status, detail = map_collaboration_error_to_http(
        CollaborationError("denied", code="collaboration_role_denied", details={"action": "share"})
    )
    assert status == 403
    assert detail["code"] == "collaboration_role_denied"
    assert detail["details"]["action"] == "share"


def test_dtos_forbid_extra_fields():
    with pytest.raises(ValidationError):
        CollaborationStatusResponse.model_validate({"enabled": False, "secret": "x"})
    with pytest.raises(ValidationError):
        CollaborationActorCreateRequest.model_validate({"display_name": "Ada", "role": "owner"})
    with pytest.raises(ValidationError):
        CollaborationMemberGrantRequest.model_validate({"actor_id": "a", "role": "owner"})
    with pytest.raises(ValidationError):
        CollaborationCommentCreateRequest.model_validate(
            {"target_kind": "section", "body": "hi", "events": []}
        )
