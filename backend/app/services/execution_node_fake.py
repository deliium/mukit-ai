"""In-process fake ExecutionNode peer for CI (`AI_EXECUTION_NODE_FAKE=1`)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI

from app.execution_node_schemas import (
    ExecutionNodeHealthV1,
    ExecutionNodeHeartbeatV1,
    ExecutionNodeInstalledModelV1,
    ExecutionNodeRegistrationV1,
    ExecutionNodeV1,
)
from app.execution_node_settings import load_execution_node_settings
from app.routers.execution_worker import router as worker_router
from app.services import execution_node_runtime as live
from app.services import execution_node_service as service
from app.services.execution_worker_dispatch import worker_resource_snapshot

logger = logging.getLogger(__name__)

FAKE_NODE_ID = "node_0123456789abcdef"
FAKE_ADDRESS = "http://execution-node.fake"
_FAKE_APP: FastAPI | None = None


def get_fake_worker_asgi_app() -> FastAPI:
    """Return a process-local ASGI app mounting only the worker router."""
    global _FAKE_APP
    if _FAKE_APP is None:
        app = FastAPI(title="Fake ExecutionNode Worker")
        app.include_router(worker_router)
        _FAKE_APP = app
        logger.info("Fake execution node ASGI app created")
    return _FAKE_APP


def fake_installed_model() -> ExecutionNodeInstalledModelV1:
    return ExecutionNodeInstalledModelV1(
        id="fake:language",
        display_name="Fake language (execution node)",
        primary_capability="language_planner",
        runtime="fake",
        locality="local",
        status="ready",
        supported_operations=["generate", "region_edit"],
    )


def ensure_fake_peer_registered(*, db_path: Any = None) -> ExecutionNodeV1:
    """Register and heartbeat the fake peer on the controller."""
    settings = load_execution_node_settings()
    if not settings.enabled or not settings.fake:
        raise RuntimeError("Fake execution node is not enabled")
    resources, availability = worker_resource_snapshot()
    registration = ExecutionNodeRegistrationV1(
        node_id=FAKE_NODE_ID,
        display_name="Fake execution node",
        address=FAKE_ADDRESS,
        capabilities=["language_planner"],
        available_runtimes=["fake"],
        installed_models=[fake_installed_model()],
        resources=resources,
        health=ExecutionNodeHealthV1(status="ready", detail="fake"),
    )
    node = service.register_node(registration, settings=settings, db_path=db_path)
    node = service.heartbeat_node(
        FAKE_NODE_ID,
        ExecutionNodeHeartbeatV1(
            node_id=FAKE_NODE_ID,
            health=ExecutionNodeHealthV1(status="ready", detail="fake"),
            resources=resources,
            availability=availability,
            document_revision=node.document_revision,
            installed_models=[fake_installed_model()],
            capabilities=["language_planner"],
        ),
        settings=settings,
        db_path=db_path,
    )
    logger.info(
        "Fake execution node peer ready",
        extra={"node_id": FAKE_NODE_ID, "availability": node.availability},
    )
    return node


def reset_fake_peer_state() -> None:
    live.clear_live_state()
    global _FAKE_APP
    _FAKE_APP = None
