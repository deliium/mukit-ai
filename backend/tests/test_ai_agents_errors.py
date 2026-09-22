"""Tests for agent error code → HTTP mapping."""

from __future__ import annotations

from app.ai_agents.errors import (
    AGENT_MODEL_UNRESOLVED,
    AGENT_NOT_FOUND,
    ARTIFACT_DEPENDENCY_UNSATISFIED,
    ARTIFACT_IMMUTABLE_VIOLATION,
    ARTIFACT_KIND_REJECTED,
    ARTIFACT_NOT_FOUND,
    ARTIFACT_PAYLOAD_REJECTED,
    ARTIFACT_PLAYABLE_FIELDS_FORBIDDEN,
    ARTIFACT_RETENTION_REJECTED,
    WORKFLOW_REVISE_EXHAUSTED,
    WORKING_DRAFT_INVALID,
    WORKSPACE_QUOTA_EXCEEDED,
    AgentModelUnresolvedError,
    AgentNotFoundError,
    ArtifactDependencyUnsatisfiedError,
    ArtifactImmutableViolationError,
    ArtifactKindRejectedError,
    ArtifactNotFoundError,
    ArtifactPayloadRejectedError,
    ArtifactPlayableFieldsForbiddenError,
    ArtifactRetentionRejectedError,
    WorkingDraftInvalidError,
    WorkflowReviseExhaustedError,
    WorkspaceQuotaExceededError,
    http_status_for_code,
    map_agent_error_to_http,
)


def test_http_status_for_stable_codes():
    assert http_status_for_code(AGENT_NOT_FOUND) == 404
    assert http_status_for_code(ARTIFACT_KIND_REJECTED) == 422
    assert http_status_for_code(WORKFLOW_REVISE_EXHAUSTED) == 422
    assert http_status_for_code(AGENT_MODEL_UNRESOLVED) == 503
    assert http_status_for_code(WORKING_DRAFT_INVALID) == 422
    assert http_status_for_code(ARTIFACT_DEPENDENCY_UNSATISFIED) == 422
    assert http_status_for_code(ARTIFACT_IMMUTABLE_VIOLATION) == 422
    assert http_status_for_code(ARTIFACT_NOT_FOUND) == 404
    assert http_status_for_code(ARTIFACT_PAYLOAD_REJECTED) == 422
    assert http_status_for_code(ARTIFACT_PLAYABLE_FIELDS_FORBIDDEN) == 422
    assert http_status_for_code(ARTIFACT_RETENTION_REJECTED) == 422
    assert http_status_for_code(WORKSPACE_QUOTA_EXCEEDED) == 429


def test_map_agent_error_sanitized_detail():
    exc = AgentNotFoundError("missing", agent_id="harmony")
    status, detail = map_agent_error_to_http(exc)
    assert status == 404
    assert detail["code"] == AGENT_NOT_FOUND
    assert detail["agent_id"] == "harmony"
    assert "prompt" not in str(detail).lower()


def test_exception_subclasses_carry_codes():
    assert ArtifactKindRejectedError("bad").code == ARTIFACT_KIND_REJECTED
    assert WorkflowReviseExhaustedError("done").code == WORKFLOW_REVISE_EXHAUSTED
    assert AgentModelUnresolvedError("x", agent_id="critic").agent_id == "critic"
    assert WorkingDraftInvalidError("y").http_status == 422
    assert ArtifactDependencyUnsatisfiedError("dep").code == ARTIFACT_DEPENDENCY_UNSATISFIED
    assert ArtifactImmutableViolationError("mut").http_status == 422
    assert ArtifactNotFoundError("gone").http_status == 404
    assert ArtifactPayloadRejectedError("bad").code == ARTIFACT_PAYLOAD_REJECTED
    assert ArtifactPlayableFieldsForbiddenError("tracks").code == ARTIFACT_PLAYABLE_FIELDS_FORBIDDEN
    assert ArtifactRetentionRejectedError("ttl").code == ARTIFACT_RETENTION_REJECTED
    assert WorkspaceQuotaExceededError("full").http_status == 429


def test_workspace_error_detail_prefixes_only():
    exc = ArtifactNotFoundError(
        "missing artifact",
        artifact_id="abcd1234-artifact",
        project_id="proj-9999",
        details={"reason": "not_found", "missing": ["harmony_plan"]},
    )
    _, detail = map_agent_error_to_http(exc)
    assert detail["artifact_id"] == "abcd1234-artifact"
    assert detail["project_id"] == "proj-9999"
    assert detail["details"]["missing"] == ["harmony_plan"]
    assert detail["details"]["reason"] == "not_found"
