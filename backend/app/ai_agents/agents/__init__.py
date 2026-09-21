"""Real + stub agent factory for non-fake bootstrap."""

from __future__ import annotations

import logging
import os
from typing import Mapping

from app.ai_agents.agents.arrangement import ArrangementAgent
from app.ai_agents.agents.common import BaseMusicAgent, build_agent_descriptor, ensure_known_agent
from app.ai_agents.agents.creative_director import CreativeDirectorAgent
from app.ai_agents.agents.critic import CriticAgent
from app.ai_agents.agents.harmony import HarmonyAgent
from app.ai_agents.agents.melody_motif import MelodyMotifAgent
from app.ai_agents.agents.stubs import StubAgent
from app.ai_agents.schemas import KNOWN_AGENT_IDS

logger = logging.getLogger(__name__)

_SPINE_FACTORIES: dict[str, type[BaseMusicAgent]] = {
    "creative_director": CreativeDirectorAgent,
    "harmony": HarmonyAgent,
    "melody_motif": MelodyMotifAgent,
    "arrangement": ArrangementAgent,
    "critic": CriticAgent,
}


def build_real_agent(agent_id: str, *, env: Mapping[str, str] | None = None) -> BaseMusicAgent:
    ensure_known_agent(agent_id)
    descriptor = build_agent_descriptor(
        agent_id,
        env=env,
        health_detail="real" if agent_id in _SPINE_FACTORIES else "stub",
    )
    cls = _SPINE_FACTORIES.get(agent_id, StubAgent)
    logger.debug(
        "Built agent adapter",
        extra={"agent_id": agent_id, "adapter": cls.__name__},
    )
    return cls(descriptor)


def build_all_real_agents(*, env: Mapping[str, str] | None = None) -> list[BaseMusicAgent]:
    source = env if env is not None else os.environ
    return [build_real_agent(agent_id, env=source) for agent_id in KNOWN_AGENT_IDS]
