"""HTTP mapping for collaboration access. Routers import this; services do not import FastAPI."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException

from app.collaboration_schemas import CollaborationError, map_collaboration_error_to_http
from app.collaboration_settings import collaboration_enabled
from app.services.collaboration_access import (
    authorize_project,
    request_actor_header,
    resolve_request_actor,
)


def raise_collaboration(exc: CollaborationError) -> HTTPException:
    status, detail = map_collaboration_error_to_http(exc)
    return HTTPException(status_code=status, detail=detail)


def optional_actor(
    x_mukit_actor: Annotated[str | None, Header()] = None,
) -> str | None:
    """Resolve ``X-Mukit-Actor``. Unknown ids are 401 only when the flag is on."""
    try:
        return resolve_request_actor(x_mukit_actor)
    except CollaborationError as exc:
        raise raise_collaboration(exc) from exc


ActorId = Annotated[str | None, Depends(optional_actor)]


def enforce_project(actor_id: str | None, project_id: str | None, action: str) -> str | None:
    """Authorize when the flag is on and a project id is present. Otherwise return."""
    if not project_id:
        return None
    try:
        authorize_project(actor_id, project_id, action)
    except CollaborationError as exc:
        raise raise_collaboration(exc) from exc
    return actor_id


def enforce_header(header: str | None, project_id: str | None, action: str) -> str | None:
    """Authorize a raw ``X-Mukit-Actor`` value when the flag is on and a project id exists.

    Preview routes with no project id stay open, including when the header is set.
    """
    if not collaboration_enabled() or not project_id:
        return None
    try:
        actor_id = resolve_request_actor(header)
        authorize_project(actor_id, project_id, action)
    except CollaborationError as exc:
        raise raise_collaboration(exc) from exc
    return actor_id


def enforce_current(project_id: str | None, action: str) -> str | None:
    """Authorize using the actor header captured for this request."""
    return enforce_header(request_actor_header(), project_id, action)
