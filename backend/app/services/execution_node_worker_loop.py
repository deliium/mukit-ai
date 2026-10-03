"""Worker auto-register / heartbeat loop against the controller URL."""

from __future__ import annotations

import asyncio
import logging
import secrets
from typing import Any

import httpx

from app.execution_node_schemas import (
    ExecutionNodeHealthV1,
    ExecutionNodeHeartbeatV1,
    ExecutionNodeRegistrationV1,
    ExecutionNodeResourcesV1,
)
from app.execution_node_settings import ExecutionNodeSettings, load_execution_node_settings
from app.services.execution_worker_dispatch import worker_catalog

logger = logging.getLogger(__name__)

_task: asyncio.Task[Any] | None = None
_stop = asyncio.Event()
_node_id: str | None = None
_revision = 1


def _stable_node_id() -> str:
    global _node_id
    if _node_id is None:
        _node_id = f"node_{secrets.token_hex(8)}"
    return _node_id


async def _post_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    token: str,
    payload: dict[str, Any],
) -> httpx.Response:
    return await client.post(
        url,
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )


async def worker_loop_once(
    settings: ExecutionNodeSettings,
    *,
    client: httpx.AsyncClient,
    public_address: str,
) -> None:
    """One register-or-heartbeat cycle."""
    global _revision
    if not settings.token or not settings.controller_url:
        return
    node_id = _stable_node_id()
    catalog = worker_catalog(node_id=node_id)
    models = list(catalog.installed_models)
    base = settings.controller_url.rstrip("/")
    register_url = f"{base}/ai/execution-nodes/register"
    heartbeat_url = f"{base}/ai/execution-nodes/{node_id}/heartbeat"
    resources = ExecutionNodeResourcesV1(active_tasks=0, max_concurrency=2)
    registration = ExecutionNodeRegistrationV1(
        node_id=node_id,
        display_name="Mukit execution worker",
        address=public_address,
        capabilities=["language_planner"],
        available_runtimes=["fake", "local_openai_compatible"],
        installed_models=models,
        resources=resources,
        health=ExecutionNodeHealthV1(status="ready"),
    )
    response = await _post_json(
        client,
        register_url,
        token=settings.token,
        payload=registration.model_dump(mode="json"),
    )
    if response.status_code in {200, 201}:
        body = response.json()
        _revision = int(body.get("document_revision") or 1)
        logger.info(
            "Worker registered with controller",
            extra={"node_id": node_id, "document_revision": _revision},
        )
    else:
        logger.warning(
            "Worker register failed",
            extra={"status_code": response.status_code, "code": "execution_node_unavailable"},
        )
        return

    heartbeat = ExecutionNodeHeartbeatV1(
        node_id=node_id,
        health=ExecutionNodeHealthV1(status="ready"),
        resources=resources,
        availability="available",
        document_revision=_revision,
        installed_models=models,
        capabilities=["language_planner"],
    )
    hb = await _post_json(
        client,
        heartbeat_url,
        token=settings.token,
        payload=heartbeat.model_dump(mode="json"),
    )
    if hb.status_code == 200:
        _revision = int(hb.json().get("document_revision") or (_revision + 1))
        logger.debug(
            "Worker heartbeat ok",
            extra={"node_id": node_id, "document_revision": _revision},
        )
    else:
        logger.warning(
            "Worker heartbeat failed",
            extra={"status_code": hb.status_code, "code": "execution_node_unavailable"},
        )


async def _loop_main(public_address: str) -> None:
    settings = load_execution_node_settings()
    logger.info(
        "Worker heartbeat loop start",
        extra={
            "interval": settings.heartbeat_interval_seconds,
            "has_controller_url": bool(settings.controller_url),
        },
    )
    async with httpx.AsyncClient(timeout=15.0) as client:
        while not _stop.is_set():
            try:
                settings = load_execution_node_settings()
                await worker_loop_once(
                    settings,
                    client=client,
                    public_address=public_address,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Worker loop iteration failed",
                    extra={"error_type": type(exc).__name__},
                )
            try:
                await asyncio.wait_for(
                    _stop.wait(),
                    timeout=float(settings.heartbeat_interval_seconds),
                )
            except TimeoutError:
                continue
    logger.info("Worker heartbeat loop stop")


def start_worker_loop(*, public_address: str = "http://127.0.0.1:8000") -> None:
    """Start the background loop when role is worker and controller URL is set."""
    global _task
    try:
        settings = load_execution_node_settings()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Worker loop not started",
            extra={"error_type": type(exc).__name__},
        )
        return
    if not settings.enabled or settings.role not in {"worker", "both"}:
        return
    if not settings.controller_url:
        logger.info("Worker loop skipped; controller URL unset")
        return
    if _task is not None and not _task.done():
        return
    _stop.clear()
    _task = asyncio.create_task(_loop_main(public_address))
    logger.info("Worker heartbeat loop task created")


async def stop_worker_loop() -> None:
    global _task
    _stop.set()
    if _task is not None:
        try:
            await asyncio.wait_for(_task, timeout=5.0)
        except Exception:  # noqa: BLE001
            _task.cancel()
        _task = None
    logger.info("Worker heartbeat loop stopped")
