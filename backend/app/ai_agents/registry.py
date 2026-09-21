"""In-process AgentRegistry with reload support for tests."""

from __future__ import annotations

import logging
import threading
from typing import Mapping

from app.ai_agents.errors import AgentNotFoundError, AgentUnavailableError
from app.ai_agents.protocols import MusicAgent
from app.ai_agents.schemas import AgentDescriptor, AgentStatus

logger = logging.getLogger(__name__)

_lock = threading.RLock()
_agents: dict[str, MusicAgent] = {}
_descriptors: dict[str, AgentDescriptor] = {}


def clear_registry_for_tests() -> None:
    with _lock:
        _agents.clear()
        _descriptors.clear()
        logger.debug("Agent registry cleared for tests")


def register_agent(agent: MusicAgent, *, replace: bool = True) -> None:
    descriptor = agent.descriptor
    with _lock:
        if not replace and descriptor.id in _agents:
            raise ValueError(f"Agent already registered: {descriptor.id}")
        _agents[descriptor.id] = agent
        _descriptors[descriptor.id] = descriptor
        logger.info(
            "Agent registered",
            extra={
                "agent_id": descriptor.id,
                "status": descriptor.status.value,
                "bound_model_id": descriptor.bound_model_id,
            },
        )


def get_agent(agent_id: str) -> MusicAgent:
    with _lock:
        agent = _agents.get(agent_id)
    if agent is None:
        raise AgentNotFoundError(f"Agent not found: {agent_id}", agent_id=agent_id)
    if agent.descriptor.status == AgentStatus.UNAVAILABLE:
        raise AgentUnavailableError(
            f"Agent unavailable: {agent_id}",
            agent_id=agent_id,
        )
    return agent


def get_descriptor(agent_id: str) -> AgentDescriptor:
    with _lock:
        desc = _descriptors.get(agent_id)
    if desc is None:
        raise AgentNotFoundError(f"Agent not found: {agent_id}", agent_id=agent_id)
    return desc


def list_descriptors(
    *,
    capability: str | None = None,
    status: str | None = None,
) -> list[AgentDescriptor]:
    with _lock:
        items = list(_descriptors.values())
    if capability:
        items = [d for d in items if d.capability.value == capability]
    if status:
        items = [d for d in items if d.status.value == status]
    items.sort(key=lambda d: d.id)
    logger.debug(
        "Agent list filtered",
        extra={"capability": capability, "status": status, "count": len(items)},
    )
    return items


def list_agent_ids() -> list[str]:
    with _lock:
        return sorted(_agents.keys())


def reload_agent_registry(env: Mapping[str, str] | None = None) -> None:
    """Rebuild registry from bootstrap (fake agents when LLM_FAKE_MODE=1)."""
    from app.ai_agents.bootstrap import bootstrap_agents

    with _lock:
        _agents.clear()
        _descriptors.clear()
    bootstrap_agents(env=env)
    logger.info(
        "Agent registry reloaded",
        extra={"agent_count": len(list_agent_ids()), "agent_ids": list_agent_ids()},
    )


def ensure_registry(env: Mapping[str, str] | None = None) -> None:
    """Lazy bootstrap — fail-soft if already populated."""
    with _lock:
        if _agents:
            return
    try:
        reload_agent_registry(env)
    except Exception:  # noqa: BLE001 — startup must not crash on optional models
        logger.warning("Agent registry bootstrap failed (fail-soft)", exc_info=True)
