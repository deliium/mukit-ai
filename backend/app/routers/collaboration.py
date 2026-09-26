"""Collaboration status, actors, and project membership routes."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from app.collaboration_schemas import (
    CollaborationActorCreateRequest,
    CollaborationActorResponse,
    CollaborationCommentCreateRequest,
    CollaborationCommentResponse,
    CollaborationError,
    CollaborationMemberGrantRequest,
    CollaborationMemberRolePatch,
    CollaborationMembershipResponse,
    CollaborationReviewDecisionRequest,
    CollaborationReviewResponse,
    CollaborationStatusResponse,
    CollaborationActivityResponse,
)
from app.collaboration_settings import collaboration_enabled
from app.routers.collaboration_guard import (
    ActorId,
    enforce_project,
    raise_collaboration,
)
from app.services.collaboration_access import resolve_request_actor
from app.services.collaboration_store import (
    change_role,
    create_actor,
    grant_member,
    list_actors,
    list_members,
    revoke_member,
)
from app.services.collaboration_comments import create_comment, list_comments
from app.services.collaboration_reviews import (
    approve_review,
    list_reviews,
    open_review,
    reject_review,
)
from app.services.collaboration_activity import list_activity

logger = logging.getLogger(__name__)

router = APIRouter(tags=["collaboration"])


def _disabled() -> HTTPException:
    return raise_collaboration(
        CollaborationError(
            "Collaboration is disabled",
            code="collaboration_disabled",
        )
    )


def _guard_enabled() -> None:
    if not collaboration_enabled():
        raise _disabled()


@router.get("/collaboration/status", response_model=CollaborationStatusResponse)
async def collaboration_status(actor_id: ActorId) -> CollaborationStatusResponse:
    if not collaboration_enabled():
        logger.info("Collaboration status", extra={"enabled": False})
        return CollaborationStatusResponse(enabled=False)
    resolved = actor_id or resolve_request_actor(None)
    logger.info("Collaboration status", extra={"enabled": True, "actor_id": resolved})
    return CollaborationStatusResponse(enabled=True, actor_id=resolved)


@router.post("/collaboration/actors", response_model=CollaborationActorResponse, status_code=201)
async def create_collaboration_actor(
    body: CollaborationActorCreateRequest,
    _actor_id: ActorId,
) -> CollaborationActorResponse:
    _guard_enabled()
    try:
        created = create_actor(body.display_name)
    except CollaborationError as exc:
        raise raise_collaboration(exc) from exc
    return CollaborationActorResponse(id=created.id, display_name=created.display_name)


@router.get("/collaboration/actors", response_model=list[CollaborationActorResponse])
async def list_collaboration_actors(_actor_id: ActorId) -> list[CollaborationActorResponse]:
    _guard_enabled()
    return [
        CollaborationActorResponse(id=actor.id, display_name=actor.display_name)
        for actor in list_actors()
    ]


@router.get(
    "/projects/{project_id}/members",
    response_model=list[CollaborationMembershipResponse],
)
async def list_project_members(
    project_id: str,
    actor_id: ActorId,
) -> list[CollaborationMembershipResponse]:
    _guard_enabled()
    enforce_project(actor_id, project_id, "read")
    return [
        CollaborationMembershipResponse(
            project_id=row.project_id,
            actor_id=row.actor_id,
            role=row.role,  # type: ignore[arg-type]
            created_at=row.created_at,
        )
        for row in list_members(project_id)
    ]


@router.post(
    "/projects/{project_id}/members",
    response_model=CollaborationMembershipResponse,
    status_code=201,
)
async def grant_project_member(
    project_id: str,
    body: CollaborationMemberGrantRequest,
    actor_id: ActorId,
) -> CollaborationMembershipResponse:
    _guard_enabled()
    enforce_project(actor_id, project_id, "share")
    try:
        row = grant_member(project_id, body.actor_id, body.role)
    except CollaborationError as exc:
        raise raise_collaboration(exc) from exc
    return CollaborationMembershipResponse(
        project_id=row.project_id,
        actor_id=row.actor_id,
        role=row.role,  # type: ignore[arg-type]
        created_at=row.created_at,
    )


@router.patch(
    "/projects/{project_id}/members/{member_actor_id}",
    response_model=CollaborationMembershipResponse,
)
async def patch_project_member(
    project_id: str,
    member_actor_id: str,
    body: CollaborationMemberRolePatch,
    actor_id: ActorId,
) -> CollaborationMembershipResponse:
    _guard_enabled()
    enforce_project(actor_id, project_id, "share")
    try:
        row = change_role(project_id, member_actor_id, body.role)
    except CollaborationError as exc:
        raise raise_collaboration(exc) from exc
    return CollaborationMembershipResponse(
        project_id=row.project_id,
        actor_id=row.actor_id,
        role=row.role,  # type: ignore[arg-type]
        created_at=row.created_at,
    )


@router.delete("/projects/{project_id}/members/{member_actor_id}", status_code=204)
async def delete_project_member(
    project_id: str,
    member_actor_id: str,
    actor_id: ActorId,
) -> None:
    _guard_enabled()
    enforce_project(actor_id, project_id, "share")
    try:
        revoke_member(project_id, member_actor_id)
    except CollaborationError as exc:
        raise raise_collaboration(exc) from exc


def _comment_response(row) -> CollaborationCommentResponse:
    return CollaborationCommentResponse(
        id=row.id,
        project_id=row.project_id,
        author_actor_id=row.author_actor_id,
        target_kind=row.target_kind,  # type: ignore[arg-type]
        body=row.body,
        revision_id=row.revision_id,
        section_id=row.section_id,
        start_bar=row.start_bar,
        end_bar=row.end_bar,
        track_id=row.track_id,
        render_id=row.render_id,
        created_at=row.created_at,
    )


@router.post("/projects/{project_id}/comments", response_model=CollaborationCommentResponse, status_code=201)
async def post_project_comment(
    project_id: str,
    body: CollaborationCommentCreateRequest,
    actor_id: ActorId,
) -> CollaborationCommentResponse:
    _guard_enabled()
    enforce_project(actor_id, project_id, "comment")
    if not actor_id:
        raise _disabled()
    try:
        row = create_comment(project_id, actor_id, body)
    except CollaborationError as exc:
        raise raise_collaboration(exc) from exc
    return _comment_response(row)


@router.get("/projects/{project_id}/comments", response_model=list[CollaborationCommentResponse])
async def get_project_comments(
    project_id: str,
    actor_id: ActorId,
    limit: int = Query(default=50, ge=1, le=100),
) -> list[CollaborationCommentResponse]:
    _guard_enabled()
    enforce_project(actor_id, project_id, "read")
    return [_comment_response(row) for row in list_comments(project_id, limit=limit)]


def _review_response(row) -> CollaborationReviewResponse:
    return CollaborationReviewResponse(
        id=row.id,
        project_id=row.project_id,
        revision_id=row.revision_id,
        status=row.status,  # type: ignore[arg-type]
        origin=row.origin,  # type: ignore[arg-type]
        opened_by_actor_id=row.opened_by_actor_id,
        decided_by_actor_id=row.decided_by_actor_id,
        created_at=row.created_at,
        decided_at=row.decided_at,
    )


@router.post(
    "/projects/{project_id}/revisions/{revision_id}/reviews",
    response_model=CollaborationReviewResponse,
    status_code=201,
)
async def post_revision_review(
    project_id: str,
    revision_id: str,
    actor_id: ActorId,
) -> CollaborationReviewResponse:
    _guard_enabled()
    enforce_project(actor_id, project_id, "review_open")
    if not actor_id:
        raise _disabled()
    try:
        row = open_review(project_id, revision_id, actor_id)
    except CollaborationError as exc:
        raise raise_collaboration(exc) from exc
    return _review_response(row)


@router.post(
    "/projects/{project_id}/reviews/{review_id}/approve",
    response_model=CollaborationReviewResponse,
)
async def post_review_approve(
    project_id: str,
    review_id: str,
    actor_id: ActorId,
    body: CollaborationReviewDecisionRequest | None = None,
) -> CollaborationReviewResponse:
    _guard_enabled()
    enforce_project(actor_id, project_id, "review_decide")
    if not actor_id:
        raise _disabled()
    try:
        row = approve_review(project_id, review_id, actor_id, note=None if body is None else body.note)
    except CollaborationError as exc:
        raise raise_collaboration(exc) from exc
    return _review_response(row)


@router.post(
    "/projects/{project_id}/reviews/{review_id}/reject",
    response_model=CollaborationReviewResponse,
)
async def post_review_reject(
    project_id: str,
    review_id: str,
    actor_id: ActorId,
    body: CollaborationReviewDecisionRequest | None = None,
) -> CollaborationReviewResponse:
    _guard_enabled()
    enforce_project(actor_id, project_id, "review_decide")
    if not actor_id:
        raise _disabled()
    try:
        row = reject_review(project_id, review_id, actor_id, note=None if body is None else body.note)
    except CollaborationError as exc:
        raise raise_collaboration(exc) from exc
    return _review_response(row)


@router.get("/projects/{project_id}/reviews", response_model=list[CollaborationReviewResponse])
async def get_project_reviews(
    project_id: str,
    actor_id: ActorId,
) -> list[CollaborationReviewResponse]:
    _guard_enabled()
    enforce_project(actor_id, project_id, "read")
    return [_review_response(row) for row in list_reviews(project_id)]


@router.get("/projects/{project_id}/activity", response_model=list[CollaborationActivityResponse])
async def get_project_activity(
    project_id: str,
    actor_id: ActorId,
    limit: int = Query(default=50, ge=1, le=100),
) -> list[CollaborationActivityResponse]:
    _guard_enabled()
    enforce_project(actor_id, project_id, "read")
    return [
        CollaborationActivityResponse(
            id=row.id,
            project_id=row.project_id,
            kind=row.kind,  # type: ignore[arg-type]
            actor_id=row.actor_id,
            revision_id=row.revision_id,
            comment_id=row.comment_id,
            review_id=row.review_id,
            render_id=row.render_id,
            decision=row.decision,  # type: ignore[arg-type]
            ai_provider=row.ai_provider,
            ai_model=row.ai_model,
            summary=row.summary,
            created_at=row.created_at,
        )
        for row in list_activity(project_id, limit=limit)
    ]



