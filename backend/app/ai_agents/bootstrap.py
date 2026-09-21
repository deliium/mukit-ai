"""Bootstrap agent registry — fake under ``LLM_FAKE_MODE=1``, else real spine adapters."""

from __future__ import annotations

import logging
import os
from typing import Mapping

from app.ai_agents.agents import build_all_real_agents
from app.ai_agents.fake_agents import build_all_fake_agents
from app.ai_agents.registry import register_agent
from app.ai_runtime import registry as model_registry

logger = logging.getLogger(__name__)


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def bootstrap_agents(env: Mapping[str, str] | None = None) -> int:
    """Register all nine agents.

    When ``LLM_FAKE_MODE=1``, bind deterministic fake agents (CI default).
    Otherwise bind spine real service wrappers + stub adapters for the rest.
    Side-effect light: does not write projects; model registry may reload.
    """
    source = dict(env) if env is not None else dict(os.environ)
    fake_mode = _truthy(source.get("LLM_FAKE_MODE"))

    try:
        if not model_registry.get_registry(source):
            model_registry.reload_registry(source)
    except Exception:  # noqa: BLE001
        logger.warning("Model registry warm failed during agent bootstrap", exc_info=True)

    if fake_mode:
        agents = build_all_fake_agents(env=source)
        adapter_kind = "fake"
    else:
        agents = build_all_real_agents(env=source)
        adapter_kind = "real"

    for agent in agents:
        register_agent(agent, replace=True)

    logger.info(
        "Agent bootstrap complete",
        extra={
            "agent_count": len(agents),
            "fake_mode": fake_mode,
            "adapter_kind": adapter_kind,
            "agent_ids": [a.descriptor.id for a in agents],
        },
    )
    return len(agents)
