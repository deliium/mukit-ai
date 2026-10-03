"""In-memory live availability / heartbeat TTL for registered nodes."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

from app.execution_node_schemas import (
    ExecutionNodeHealthV1,
    ExecutionNodeInstalledModelV1,
    ExecutionNodeResourcesV1,
    HeartbeatAvailability,
    NodeAvailability,
)

logger = logging.getLogger(__name__)


@dataclass
class LiveNodeState:
    availability: NodeAvailability
    last_heartbeat_mono: float | None
    health: ExecutionNodeHealthV1
    resources: ExecutionNodeResourcesV1
    installed_models: list[ExecutionNodeInstalledModelV1] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)


_lock = threading.Lock()
_STATE: dict[str, LiveNodeState] = {}


def clear_live_state() -> None:
    with _lock:
        _STATE.clear()


def seed_unavailable(
    node_id: str,
    *,
    health: ExecutionNodeHealthV1 | None = None,
    resources: ExecutionNodeResourcesV1 | None = None,
    installed_models: list[ExecutionNodeInstalledModelV1] | None = None,
    capabilities: list[str] | None = None,
) -> None:
    with _lock:
        _STATE[node_id] = LiveNodeState(
            availability="unavailable",
            last_heartbeat_mono=None,
            health=health or ExecutionNodeHealthV1(status="unavailable"),
            resources=resources or ExecutionNodeResourcesV1(),
            installed_models=list(installed_models or []),
            capabilities=list(capabilities or []),
        )


def apply_heartbeat(
    node_id: str,
    *,
    availability: HeartbeatAvailability,
    health: ExecutionNodeHealthV1,
    resources: ExecutionNodeResourcesV1,
    installed_models: list[ExecutionNodeInstalledModelV1] | None,
    capabilities: list[str] | None,
) -> None:
    with _lock:
        previous = _STATE.get(node_id)
        _STATE[node_id] = LiveNodeState(
            availability=availability,
            last_heartbeat_mono=time.monotonic(),
            health=health,
            resources=resources,
            installed_models=list(
                installed_models
                if installed_models is not None
                else (previous.installed_models if previous else [])
            ),
            capabilities=list(
                capabilities if capabilities is not None else (previous.capabilities if previous else [])
            ),
        )


def drop_live(node_id: str) -> None:
    with _lock:
        _STATE.pop(node_id, None)


def refresh_availability(node_id: str, *, ttl_seconds: int) -> NodeAvailability:
    """Expire heartbeat TTL to unavailable. Returns current availability."""
    with _lock:
        state = _STATE.get(node_id)
        if state is None:
            return "unavailable"
        if state.last_heartbeat_mono is None:
            state.availability = "unavailable"
            return "unavailable"
        age = time.monotonic() - state.last_heartbeat_mono
        if age > ttl_seconds and state.availability != "unavailable":
            state.availability = "unavailable"
            logger.warning(
                "Execution node heartbeat TTL expired",
                extra={"node_id": node_id, "code": "execution_node_unavailable"},
            )
        return state.availability


def get_live(node_id: str) -> LiveNodeState | None:
    with _lock:
        state = _STATE.get(node_id)
        if state is None:
            return None
        return LiveNodeState(
            availability=state.availability,
            last_heartbeat_mono=state.last_heartbeat_mono,
            health=state.health.model_copy(deep=True),
            resources=state.resources.model_copy(deep=True),
            installed_models=[m.model_copy(deep=True) for m in state.installed_models],
            capabilities=list(state.capabilities),
        )


def list_live_ids() -> list[str]:
    with _lock:
        return sorted(_STATE.keys())
