"""Worker typed inference surface. No shell or studio project routers."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.execution_node_schemas import (
    ExecutionNodeCatalogV1,
    ExecutionNodeError,
    ExecutionTaskResultV1,
    ExecutionTaskV1,
    map_execution_node_error_to_http,
)
from app.execution_node_settings import load_execution_node_settings
from app.services import execution_worker_dispatch as dispatch
from app.services.execution_node_auth import authorize_execution_node_request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/execution/v1", tags=["execution-worker"])
_bearer = HTTPBearer(auto_error=False, scheme_name="ExecutionWorkerBearer")


def _raise(exc: ExecutionNodeError) -> None:
    status, detail = map_execution_node_error_to_http(exc)
    raise HTTPException(status_code=status, detail=detail) from exc


async def require_worker_access(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> None:
    del credentials
    try:
        settings = load_execution_node_settings()
    except ExecutionNodeError as exc:
        _raise(exc)
        return
    if not settings.enabled:
        _raise(
            ExecutionNodeError(
                "execution_nodes_disabled",
                "Execution nodes are disabled.",
            )
        )
    if settings.role not in {"worker", "both"}:
        _raise(
            ExecutionNodeError(
                "execution_nodes_disabled",
                "Worker role is not enabled.",
            )
        )
    decision = authorize_execution_node_request(
        configured_token=settings.token,
        authorization_header=request.headers.get("authorization"),
        peer_host=None if request.client is None else request.client.host,
        query_token_present="token" in request.query_params,
    )
    if decision.verdict != "allow":
        _raise(
            ExecutionNodeError(
                "execution_node_unauthorized",
                "Execution node authorization failed.",
            )
        )


@router.get("/health")
async def worker_health(
    _: Annotated[None, Depends(require_worker_access)],
) -> dict[str, Any]:
    settings = load_execution_node_settings()
    return {
        "status": "ready",
        "role": settings.role,
        "fake": settings.fake,
    }


@router.get("/models", response_model=ExecutionNodeCatalogV1)
async def worker_models(
    _: Annotated[None, Depends(require_worker_access)],
) -> ExecutionNodeCatalogV1:
    return dispatch.worker_catalog(node_id=dispatch.default_worker_node_id())


@router.post("/complete_text", response_model=ExecutionTaskResultV1)
async def worker_complete_text(
    body: ExecutionTaskV1,
    _: Annotated[None, Depends(require_worker_access)],
) -> ExecutionTaskResultV1:
    try:
        return await dispatch.dispatch_complete_text(body)
    except ExecutionNodeError as exc:
        _raise(exc)
        raise


@router.post("/tasks/{task_id}/cancel")
async def worker_cancel_task(
    task_id: str,
    _: Annotated[None, Depends(require_worker_access)],
) -> dict[str, str]:
    try:
        return dispatch.cancel_worker_task(task_id)
    except ExecutionNodeError as exc:
        _raise(exc)
        raise
