"""Tests for agent error code → HTTP mapping."""

from __future__ import annotations

from app.ai_agents.errors import (
    AGENT_MODEL_UNRESOLVED,
    AGENT_NOT_FOUND,
    ARTIFACT_KIND_REJECTED,
    WORKFLOW_REVISE_EXHAUSTED,
    WORKING_DRAFT_INVALID,
    AgentModelUnresolvedError,
    AgentNotFoundError,
    ArtifactKindRejectedError,
    WorkingDraftInvalidError,
    WorkflowReviseExhaustedError,
    http_status_for_code,
    map_agent_error_to_http,
)


def test_http_status_for_stable_codes():
    assert http_status_for_code(AGENT_NOT_FOUND) == 404
    assert http_status_for_code(ARTIFACT_KIND_REJECTED) == 422
    assert http_status_for_code(WORKFLOW_REVISE_EXHAUSTED) == 422
    assert http_status_for_code(AGENT_MODEL_UNRESOLVED) == 503
    assert http_status_for_code(WORKING_DRAFT_INVALID) == 422


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
