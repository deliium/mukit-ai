"""Collaboration DTOs and domain errors. extra=forbid on every model."""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

COLLABORATION_ERROR_HTTP: dict[str, int] = {
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

TargetKind = Literal["project", "revision", "section", "bar_range", "track", "render"]
MembershipRole = Literal["owner", "editor", "commenter", "viewer"]
GrantableRole = Literal["editor", "commenter", "viewer"]
ReviewStatus = Literal["open", "approved", "rejected"]
ReviewOrigin = Literal["ai", "human"]
ActivityKind = Literal["user_edit", "ai_edit", "approval", "comment", "render"]
ReviewDecision = Literal["approved", "rejected"]


class CollaborationError(Exception):
    """Domain error for collaboration routes. Codes map to HTTP in the contract."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = COLLABORATION_ERROR_HTTP.get(code, 422)
        self.details = details or {}


def map_collaboration_error_to_http(exc: CollaborationError) -> tuple[int, dict[str, Any]]:
    """Return ``(status, sanitized detail)`` for ``HTTPException``."""
    detail: dict[str, Any] = {
        "code": exc.code,
        "message": str(exc)[:240],
    }
    if exc.details:
        safe = {
            key: value
            for key, value in exc.details.items()
            if isinstance(value, (str, int, float, bool)) or value is None
        }
        if safe:
            detail["details"] = safe
    return exc.http_status, detail


class CollaborationStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    actor_id: str | None = None


class CollaborationActorCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(..., min_length=1, max_length=80)


class CollaborationActorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    display_name: str


class CollaborationMembershipResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    actor_id: str
    role: MembershipRole
    created_at: str


class CollaborationMemberGrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(..., min_length=1, max_length=80)
    role: GrantableRole


class CollaborationMemberRolePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: GrantableRole


class ProjectCollaborationBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: MembershipRole
    accepted_revision_id: str | None = None


class CollaborationCommentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_kind: TargetKind
    body: str = Field(..., min_length=1, max_length=2000)
    revision_id: str | None = None
    section_id: str | None = None
    start_bar: int | None = Field(default=None, ge=1)
    end_bar: int | None = Field(default=None, ge=1)
    track_id: str | None = None
    render_id: str | None = None


class CollaborationCommentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    project_id: str
    author_actor_id: str
    target_kind: TargetKind
    body: str
    revision_id: str | None = None
    section_id: str | None = None
    start_bar: int | None = None
    end_bar: int | None = None
    track_id: str | None = None
    render_id: str | None = None
    created_at: str


class CollaborationReviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    project_id: str
    revision_id: str
    status: ReviewStatus
    origin: ReviewOrigin
    opened_by_actor_id: str
    decided_by_actor_id: str | None = None
    created_at: str
    decided_at: str | None = None


class CollaborationReviewDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=500)


class CollaborationActivityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    project_id: str
    kind: ActivityKind
    actor_id: str | None = None
    revision_id: str | None = None
    comment_id: str | None = None
    review_id: str | None = None
    render_id: str | None = None
    decision: ReviewDecision | None = None
    ai_provider: str | None = None
    ai_model: str | None = None
    summary: str | None = None
    created_at: str
