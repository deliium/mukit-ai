"""Domain errors for the V4 multi-agent layer (stable codes → HTTP)."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Stable error codes (never change lightly — clients may branch on them).
AGENT_NOT_FOUND = "agent_not_found"
AGENT_UNAVAILABLE = "agent_unavailable"
ARTIFACT_KIND_REJECTED = "artifact_kind_rejected"
WORKFLOW_TIMEOUT = "workflow_timeout"
WORKFLOW_REVISE_EXHAUSTED = "workflow_revise_exhausted"
AGENT_MODEL_UNRESOLVED = "agent_model_unresolved"
WORKING_DRAFT_INVALID = "working_draft_invalid"
AGENT_OPERATION_UNSUPPORTED = "agent_operation_unsupported"
WORKFLOW_NOT_FOUND = "workflow_not_found"

# Workspace / typed artifact graph (additive).
ARTIFACT_DEPENDENCY_UNSATISFIED = "artifact_dependency_unsatisfied"
ARTIFACT_IMMUTABLE_VIOLATION = "artifact_immutable_violation"
ARTIFACT_NOT_FOUND = "artifact_not_found"
ARTIFACT_PAYLOAD_REJECTED = "artifact_payload_rejected"
ARTIFACT_PLAYABLE_FIELDS_FORBIDDEN = "artifact_playable_fields_forbidden"
ARTIFACT_RETENTION_REJECTED = "artifact_retention_rejected"
WORKSPACE_QUOTA_EXCEEDED = "workspace_quota_exceeded"

_HTTP_STATUS: dict[str, int] = {
    AGENT_NOT_FOUND: 404,
    AGENT_UNAVAILABLE: 503,
    ARTIFACT_KIND_REJECTED: 422,
    WORKFLOW_TIMEOUT: 504,
    WORKFLOW_REVISE_EXHAUSTED: 422,
    AGENT_MODEL_UNRESOLVED: 503,
    WORKING_DRAFT_INVALID: 422,
    AGENT_OPERATION_UNSUPPORTED: 422,
    WORKFLOW_NOT_FOUND: 404,
    ARTIFACT_DEPENDENCY_UNSATISFIED: 422,
    ARTIFACT_IMMUTABLE_VIOLATION: 422,
    ARTIFACT_NOT_FOUND: 404,
    ARTIFACT_PAYLOAD_REJECTED: 422,
    ARTIFACT_PLAYABLE_FIELDS_FORBIDDEN: 422,
    ARTIFACT_RETENTION_REJECTED: 422,
    WORKSPACE_QUOTA_EXCEEDED: 429,
}


class AgentError(Exception):
    """Base domain error for ai_agents — sanitized client detail only."""

    code: str = "agent_error"
    http_status: int = 500

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        http_status: int | None = None,
        agent_id: str | None = None,
        workflow_id: str | None = None,
        artifact_id: str | None = None,
        project_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code
        if http_status is not None:
            self.http_status = http_status
        else:
            self.http_status = _HTTP_STATUS.get(self.code, self.http_status)
        self.agent_id = agent_id
        self.workflow_id = workflow_id
        self.artifact_id = artifact_id
        self.project_id = project_id
        self.details = details or {}
        logger.warning(
            "Agent domain error",
            extra={
                "code": self.code,
                "agent_id": agent_id,
                "workflow_id": workflow_id,
                "artifact_id_prefix": (artifact_id or "")[:12] or None,
                "project_id_prefix": (project_id or "")[:12] or None,
                "http_status": self.http_status,
            },
        )

    def to_http_detail(self) -> dict[str, Any]:
        """Sanitized FastAPI ``detail`` payload — never prompts."""
        body: dict[str, Any] = {"code": self.code, "message": str(self)}
        if self.agent_id:
            body["agent_id"] = self.agent_id
        if self.workflow_id:
            body["workflow_id"] = self.workflow_id
        if self.artifact_id:
            body["artifact_id"] = self.artifact_id[:80]
        if self.project_id:
            body["project_id"] = self.project_id[:80]
        # Only allow bounded scalar/list details (no nested blobs).
        safe_details: dict[str, Any] = {}
        for key, value in self.details.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                if isinstance(value, str) and len(value) > 200:
                    safe_details[key] = value[:200]
                else:
                    safe_details[key] = value
            elif isinstance(value, list) and all(isinstance(x, (str, int)) for x in value[:16]):
                safe_details[key] = value[:16]
        if safe_details:
            body["details"] = safe_details
        return body


class AgentNotFoundError(AgentError):
    code = AGENT_NOT_FOUND
    http_status = 404


class AgentUnavailableError(AgentError):
    code = AGENT_UNAVAILABLE
    http_status = 503


class ArtifactKindRejectedError(AgentError):
    code = ARTIFACT_KIND_REJECTED
    http_status = 422


class WorkflowTimeoutError(AgentError):
    code = WORKFLOW_TIMEOUT
    http_status = 504


class WorkflowReviseExhaustedError(AgentError):
    code = WORKFLOW_REVISE_EXHAUSTED
    http_status = 422


class AgentModelUnresolvedError(AgentError):
    code = AGENT_MODEL_UNRESOLVED
    http_status = 503


class WorkingDraftInvalidError(AgentError):
    code = WORKING_DRAFT_INVALID
    http_status = 422


class AgentOperationUnsupportedError(AgentError):
    code = AGENT_OPERATION_UNSUPPORTED
    http_status = 422


class WorkflowNotFoundError(AgentError):
    code = WORKFLOW_NOT_FOUND
    http_status = 404


class ArtifactDependencyUnsatisfiedError(AgentError):
    code = ARTIFACT_DEPENDENCY_UNSATISFIED
    http_status = 422


class ArtifactImmutableViolationError(AgentError):
    code = ARTIFACT_IMMUTABLE_VIOLATION
    http_status = 422


class ArtifactNotFoundError(AgentError):
    code = ARTIFACT_NOT_FOUND
    http_status = 404


class ArtifactPayloadRejectedError(AgentError):
    code = ARTIFACT_PAYLOAD_REJECTED
    http_status = 422


class ArtifactPlayableFieldsForbiddenError(AgentError):
    code = ARTIFACT_PLAYABLE_FIELDS_FORBIDDEN
    http_status = 422


class ArtifactRetentionRejectedError(AgentError):
    code = ARTIFACT_RETENTION_REJECTED
    http_status = 422


class WorkspaceQuotaExceededError(AgentError):
    code = WORKSPACE_QUOTA_EXCEEDED
    http_status = 429


def http_status_for_code(code: str) -> int:
    return _HTTP_STATUS.get(code, 500)


def map_agent_error_to_http(exc: AgentError) -> tuple[int, dict[str, Any]]:
    """Return ``(status_code, detail)`` for FastAPI ``HTTPException``."""
    status = exc.http_status or http_status_for_code(exc.code)
    return status, exc.to_http_detail()


logger.info(
    "AI agent error catalog loaded",
    extra={"code_count": len(_HTTP_STATUS)},
)
