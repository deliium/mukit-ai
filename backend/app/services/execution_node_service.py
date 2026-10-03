"""Controller orchestration for ExecutionNode registration and cancel."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.execution_node_schemas import (
    ExecutionNodeError,
    ExecutionNodeHeartbeatV1,
    ExecutionNodeRegistrationV1,
    ExecutionNodeV1,
    validate_execution_node_address,
)
from app.execution_node_settings import ExecutionNodeSettings, load_execution_node_settings
from app.operation_trace import mark_run_cancelled
from app.services import execution_node_runtime as live
from app.services import execution_node_store as store
from app.services import execution_node_tasks as tasks

logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _require_enabled(settings: ExecutionNodeSettings) -> None:
    if not settings.enabled:
        raise ExecutionNodeError(
            "execution_nodes_disabled",
            "Execution nodes are disabled.",
        )


def _project_node(
    durable: ExecutionNodeV1,
    *,
    ttl_seconds: int,
) -> ExecutionNodeV1:
    availability = live.refresh_availability(durable.node_id, ttl_seconds=ttl_seconds)
    state = live.get_live(durable.node_id)
    if state is None:
        return durable.model_copy(update={"availability": "unavailable"})
    return durable.model_copy(
        update={
            "availability": availability,
            "health": state.health,
            "resources": state.resources,
            "installed_models": state.installed_models or durable.installed_models,
            "capabilities": state.capabilities or durable.capabilities,
            "last_heartbeat_at": durable.last_heartbeat_at,
        }
    )


def register_node(
    body: ExecutionNodeRegistrationV1,
    *,
    settings: ExecutionNodeSettings | None = None,
    db_path: Path | str | None = None,
) -> ExecutionNodeV1:
    cfg = settings or load_execution_node_settings()
    _require_enabled(cfg)
    address = validate_execution_node_address(
        body.address,
        allow_hostname=cfg.allow_hostname,
        extra_cidrs=list(cfg.address_allow_cidrs),
    )
    if store.count_nodes(db_path=db_path) >= cfg.max_nodes:
        existing = store.get_node(body.node_id, db_path=db_path)
        if existing is None:
            raise ExecutionNodeError(
                "execution_node_busy",
                "Maximum execution node count reached.",
                details={"max_nodes": cfg.max_nodes},
            )

    previous = store.get_node(body.node_id, db_path=db_path)
    revision = 1 if previous is None else previous.document_revision + 1
    from app.execution_node_schemas import ExecutionNodeHealthV1

    if body.health is not None:
        health = body.health
    elif previous is not None:
        health = previous.health
    else:
        health = ExecutionNodeHealthV1(status="unavailable")

    node = ExecutionNodeV1(
        node_id=body.node_id,
        display_name=body.display_name,
        address=address,
        role=body.role,
        capabilities=body.capabilities,
        hardware=body.hardware,
        available_runtimes=body.available_runtimes,
        installed_models=body.installed_models,
        health=health,
        resources=body.resources,
        availability="unavailable",
        last_heartbeat_at=None if previous is None else previous.last_heartbeat_at,
        document_revision=revision,
    )
    store.upsert_node(node, db_path=db_path)
    live.seed_unavailable(
        node.node_id,
        health=node.health,
        resources=node.resources,
        installed_models=list(node.installed_models),
        capabilities=list(node.capabilities),
    )
    logger.info(
        "Execution node registered",
        extra={"node_id": node.node_id, "document_revision": node.document_revision},
    )
    return _project_node(node, ttl_seconds=cfg.heartbeat_ttl_seconds)


def heartbeat_node(
    node_id: str,
    body: ExecutionNodeHeartbeatV1,
    *,
    settings: ExecutionNodeSettings | None = None,
    db_path: Path | str | None = None,
) -> ExecutionNodeV1:
    cfg = settings or load_execution_node_settings()
    _require_enabled(cfg)
    if body.node_id != node_id:
        raise ExecutionNodeError(
            "execution_node_conflict",
            "Heartbeat node_id does not match path.",
            details={"node_id": node_id},
        )
    current = store.get_node(node_id, db_path=db_path)
    if current is None:
        raise ExecutionNodeError(
            "execution_node_not_found",
            "Execution node was not found.",
            details={"node_id": node_id},
        )
    if body.document_revision != current.document_revision:
        raise ExecutionNodeError(
            "execution_node_conflict",
            "Execution node revision conflict.",
            details={
                "node_id": node_id,
                "expected_revision": current.document_revision,
            },
        )
    next_revision = current.document_revision + 1
    updated = current.model_copy(
        update={
            "capabilities": body.capabilities if body.capabilities is not None else current.capabilities,
            "installed_models": (
                body.installed_models if body.installed_models is not None else current.installed_models
            ),
            "health": body.health,
            "resources": body.resources,
            "availability": body.availability,
            "last_heartbeat_at": _utc_now(),
            "document_revision": next_revision,
        }
    )
    store.cas_update_node(
        updated,
        expected_revision=current.document_revision,
        db_path=db_path,
    )
    live.apply_heartbeat(
        node_id,
        availability=body.availability,
        health=body.health,
        resources=body.resources,
        installed_models=body.installed_models,
        capabilities=body.capabilities,
    )
    logger.info(
        "Execution node heartbeat",
        extra={
            "node_id": node_id,
            "document_revision": next_revision,
            "availability": body.availability,
        },
    )
    return _project_node(updated, ttl_seconds=cfg.heartbeat_ttl_seconds)


def list_nodes(
    *,
    settings: ExecutionNodeSettings | None = None,
    db_path: Path | str | None = None,
) -> list[ExecutionNodeV1]:
    cfg = settings or load_execution_node_settings()
    _require_enabled(cfg)
    return [
        _project_node(node, ttl_seconds=cfg.heartbeat_ttl_seconds)
        for node in store.list_nodes(db_path=db_path)
    ]


def get_node(
    node_id: str,
    *,
    settings: ExecutionNodeSettings | None = None,
    db_path: Path | str | None = None,
) -> ExecutionNodeV1:
    cfg = settings or load_execution_node_settings()
    _require_enabled(cfg)
    node = store.get_node(node_id, db_path=db_path)
    if node is None:
        raise ExecutionNodeError(
            "execution_node_not_found",
            "Execution node was not found.",
            details={"node_id": node_id},
        )
    return _project_node(node, ttl_seconds=cfg.heartbeat_ttl_seconds)


def delete_node(
    node_id: str,
    *,
    settings: ExecutionNodeSettings | None = None,
    db_path: Path | str | None = None,
) -> None:
    cfg = settings or load_execution_node_settings()
    _require_enabled(cfg)
    store.delete_node(node_id, db_path=db_path)
    live.drop_live(node_id)
    logger.info("Execution node deleted", extra={"node_id": node_id})


async def cancel_task(
    task_id: str,
    *,
    settings: ExecutionNodeSettings | None = None,
    db_path: Path | str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    cfg = settings or load_execution_node_settings()
    _require_enabled(cfg)
    entry = tasks.require_task(task_id)
    if entry.operation_run_id:
        mark_run_cancelled(entry.operation_run_id)
    node = store.get_node(entry.node_id, db_path=db_path)
    if node is None:
        raise ExecutionNodeError(
            "execution_node_not_found",
            "Execution node was not found.",
            details={"node_id": entry.node_id},
        )
    availability = live.refresh_availability(node.node_id, ttl_seconds=cfg.heartbeat_ttl_seconds)
    if availability == "unavailable":
        raise ExecutionNodeError(
            "execution_node_unavailable",
            "Execution node is unavailable.",
            details={"node_id": node.node_id},
        )
    url = f"{node.address.rstrip('/')}{entry.cancel_path}"
    headers = {"Authorization": f"Bearer {cfg.token}"}
    owns_client = http_client is None
    if http_client is not None:
        client = http_client
    elif "execution-node.fake" in node.address:
        from app.services.execution_node_fake import get_fake_worker_asgi_app

        transport = httpx.ASGITransport(app=get_fake_worker_asgi_app())
        client = httpx.AsyncClient(
            transport=transport,
            base_url=node.address.rstrip("/"),
            timeout=10.0,
        )
    else:
        client = httpx.AsyncClient(timeout=10.0)
    try:
        response = await client.post(url, headers=headers)
    finally:
        if owns_client:
            await client.aclose()
    logger.info(
        "Execution node cancel forwarded",
        extra={
            "task_id": task_id,
            "node_id": node.node_id,
            "status_code": response.status_code,
        },
    )
    tasks.pop_task(task_id)
    if response.status_code >= 400:
        raise ExecutionNodeError(
            "execution_node_unavailable",
            "Worker cancel failed.",
            details={"node_id": node.node_id, "status_code": response.status_code},
        )
    return {"task_id": task_id, "status": "cancelled", "node_id": node.node_id}


def execution_nodes_readiness_block(
    settings: ExecutionNodeSettings | None = None,
) -> dict[str, Any]:
    """Soft /ready block. Never flips overall readiness to false."""
    try:
        cfg = settings or load_execution_node_settings()
    except ExecutionNodeError as exc:
        return {
            "enabled": True,
            "role": None,
            "node_count": 0,
            "available_count": 0,
            "status": "misconfigured",
            "code": exc.code,
        }
    if not cfg.enabled:
        return {
            "enabled": False,
            "role": cfg.role,
            "node_count": 0,
            "available_count": 0,
            "status": "disabled",
            "code": None,
        }
    try:
        nodes = store.list_nodes()
        available = 0
        for node in nodes:
            if live.refresh_availability(node.node_id, ttl_seconds=cfg.heartbeat_ttl_seconds) == "available":
                available += 1
        status = "ready" if available > 0 or cfg.role == "worker" else "idle"
        return {
            "enabled": True,
            "role": cfg.role,
            "node_count": len(nodes),
            "available_count": available,
            "status": status,
            "code": None,
            "fake": cfg.fake,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Execution nodes readiness probe failed",
            extra={"error_type": type(exc).__name__},
        )
        return {
            "enabled": True,
            "role": cfg.role,
            "node_count": 0,
            "available_count": 0,
            "status": "error",
            "code": "execution_node_unavailable",
        }
