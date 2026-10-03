"""Flag-gated project membership checks.

``ai_agents`` must not import this module. HTTP handlers call it from routers.
"""

from __future__ import annotations

import logging

from app.collaboration_schemas import CollaborationError
from app.collaboration_settings import collaboration_enabled
from app.services.collaboration_permissions import role_allows
from app.services.collaboration_store import LOCAL_ACTOR_ID, get_actor, get_membership

from contextvars import ContextVar, Token

logger = logging.getLogger(__name__)

_request_actor_header: ContextVar[str | None] = ContextVar(
    "collaboration_actor_header",
    default=None,
)


def set_request_actor_header(value: str | None) -> Token[str | None]:
    return _request_actor_header.set(value)


def reset_request_actor_header(token: Token[str | None]) -> None:
    _request_actor_header.reset(token)


def request_actor_header() -> str | None:
    return _request_actor_header.get()


def resolve_request_actor(header_value: str | None) -> str | None:
    """Return the actor id when collaboration is on.

    Missing header resolves to ``local``. An unknown id is
    ``collaboration_actor_unknown``. When the flag is off the header is
    ignored and this returns ``None``.
    """
    if not collaboration_enabled():
        return None
    actor_id = (header_value or "").strip() or LOCAL_ACTOR_ID
    actor = get_actor(actor_id)
    if actor is None:
        logger.info(
            "Collaboration actor unknown",
            extra={
                "actor_id": actor_id,
                "code": "collaboration_actor_unknown",
            },
        )
        raise CollaborationError(
            "Unknown collaboration actor",
            code="collaboration_actor_unknown",
            details={"actor_id": actor_id},
        )
    logger.debug("Collaboration actor resolved", extra={"actor_id": actor_id})
    return actor_id


def authorize_studio_lab(action: str = "train_model_lab") -> str | None:
    """Authorize a studio-scoped Lab mutate when collaboration is on.

    Lab experiments are not project-owned. When collaboration is enabled the
    caller must be a known actor who owns at least one project (or the
    single-user ``local`` actor). When collaboration is off, returns ``None``
    without any membership lookup.
    """
    if not collaboration_enabled():
        return None
    actor_id = resolve_request_actor(request_actor_header())
    assert actor_id is not None
    if not role_allows("owner", action):
        # Defensive: action must be owner-capable in the freeze matrix.
        logger.info(
            "Collaboration studio Lab denied",
            extra={
                "actor_id": actor_id,
                "action": action,
                "code": "collaboration_role_denied",
            },
        )
        raise CollaborationError(
            "Role cannot perform this action",
            code="collaboration_role_denied",
            details={"action": action},
        )
    from app.db.connection import get_connection, get_project_db_path

    with get_connection(get_project_db_path()) as conn:
        row = conn.execute(
            """
            SELECT project_id FROM project_memberships
            WHERE actor_id = ? AND role = 'owner'
            LIMIT 1
            """,
            (actor_id,),
        ).fetchone()
    if row is None and actor_id != LOCAL_ACTOR_ID:
        logger.info(
            "Collaboration studio Lab denied",
            extra={
                "actor_id": actor_id,
                "action": action,
                "code": "collaboration_role_denied",
            },
        )
        raise CollaborationError(
            "Studio Lab mutates require an actor who owns a project",
            code="collaboration_role_denied",
            details={"action": action},
        )
    # local actor may train even before the first project exists (single-user).
    if row is None and actor_id == LOCAL_ACTOR_ID:
        logger.info(
            "Collaboration studio Lab allowed for local actor",
            extra={"actor_id": actor_id, "action": action},
        )
        return actor_id
    logger.info(
        "Collaboration studio Lab allowed",
        extra={"actor_id": actor_id, "action": action},
    )
    return actor_id


def authorize_current(project_id: str | None, action: str) -> str | None:
    """Authorize the captured actor header without importing FastAPI.

    Returns the actor id when the action is allowed. Returns ``None``
    before any membership lookup when collaboration is off or the project
    id is missing. A denied role raises ``CollaborationError``.
    """
    if not collaboration_enabled() or not project_id:
        return None
    actor_id = resolve_request_actor(request_actor_header())
    authorize_project(actor_id, project_id, action)
    logger.info(
        "[FIX] Collaboration action allowed for current actor",
        extra={"project_id": project_id, "action": action, "actor_id": actor_id},
    )
    return actor_id


def authorize_project(actor_id: str | None, project_id: str, action: str) -> str | None:
    """Check ``action`` for ``actor_id`` on ``project_id``.

    Returns the role when allowed. Returns ``None`` immediately when the
    flag is off, before any membership lookup. Role is evaluated before
    callers reach CAS.
    """
    if not collaboration_enabled():
        return None
    resolved = actor_id or LOCAL_ACTOR_ID
    membership = get_membership(project_id, resolved)
    if membership is None:
        logger.info(
            "Collaboration access denied",
            extra={
                "project_id": project_id,
                "actor_id": resolved,
                "action": action,
                "code": "collaboration_not_member",
            },
        )
        raise CollaborationError(
            "Actor is not a project member",
            code="collaboration_not_member",
            details={"action": action},
        )
    if not role_allows(membership.role, action):
        logger.info(
            "Collaboration access denied",
            extra={
                "project_id": project_id,
                "actor_id": resolved,
                "action": action,
                "role": membership.role,
                "code": "collaboration_role_denied",
            },
        )
        raise CollaborationError(
            "Role cannot perform this action",
            code="collaboration_role_denied",
            details={"action": action, "role": membership.role},
        )
    logger.debug(
        "Collaboration access allowed",
        extra={"project_id": project_id, "action": action, "role": membership.role},
    )
    return membership.role
