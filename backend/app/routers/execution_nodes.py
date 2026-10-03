"""Controller HTTP surface for trusted LAN ExecutionNode peers."""

from __future__ import annotations

import logging
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.execution_node_schemas import (
    ExecutionNodeError,
    ExecutionNodeHeartbeatV1,
    ExecutionNodeRegistrationV1,
    ExecutionNodeV1,
    map_execution_node_error_to_http,
)
from app.execution_node_settings import load_execution_node_settings
from app.services import execution_node_service as service
from app.services.execution_node_auth import authorize_execution_node_request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai/execution-nodes", tags=["execution-nodes"])
_bearer = HTTPBearer(auto_error=False, scheme_name="ExecutionNodeBearer")


def _raise(exc: ExecutionNodeError) -> None:
    status, detail = map_execution_node_error_to_http(exc)
    raise HTTPException(status_code=status, detail=detail) from exc


async def require_execution_node_access(
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


@router.post("/register", response_model=ExecutionNodeV1, status_code=201)
async def register_execution_node(
    body: ExecutionNodeRegistrationV1,
    _: Annotated[None, Depends(require_execution_node_access)],
) -> ExecutionNodeV1:
    started = time.perf_counter()
    try:
        node = service.register_node(body)
        logger.info(
            "execution node register route",
            extra={
                "node_id": node.node_id,
                "duration_ms": int((time.perf_counter() - started) * 1000),
            },
        )
        return node
    except ExecutionNodeError as exc:
        _raise(exc)
        raise


@router.post("/{node_id}/heartbeat", response_model=ExecutionNodeV1)
async def heartbeat_execution_node(
    node_id: str,
    body: ExecutionNodeHeartbeatV1,
    _: Annotated[None, Depends(require_execution_node_access)],
) -> ExecutionNodeV1:
    try:
        return service.heartbeat_node(node_id, body)
    except ExecutionNodeError as exc:
        _raise(exc)
        raise


@router.get("", response_model=list[ExecutionNodeV1])
async def list_execution_nodes(
    _: Annotated[None, Depends(require_execution_node_access)],
) -> list[ExecutionNodeV1]:
    try:
        return service.list_nodes()
    except ExecutionNodeError as exc:
        _raise(exc)
        raise


@router.get("/{node_id}", response_model=ExecutionNodeV1)
async def get_execution_node(
    node_id: str,
    _: Annotated[None, Depends(require_execution_node_access)],
) -> ExecutionNodeV1:
    try:
        return service.get_node(node_id)
    except ExecutionNodeError as exc:
        _raise(exc)
        raise


@router.delete("/{node_id}", status_code=204)
async def delete_execution_node(
    node_id: str,
    _: Annotated[None, Depends(require_execution_node_access)],
) -> None:
    try:
        service.delete_node(node_id)
    except ExecutionNodeError as exc:
        _raise(exc)
        raise


@router.post("/tasks/{task_id}/cancel")
async def cancel_execution_task(
    task_id: str,
    _: Annotated[None, Depends(require_execution_node_access)],
) -> dict[str, Any]:
    try:
        return await service.cancel_task(task_id)
    except ExecutionNodeError as exc:
        _raise(exc)
        raise
