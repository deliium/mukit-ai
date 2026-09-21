"""Acceptance spine agent order for multi-agent workflow."""

from __future__ import annotations

from app.ai_agents.schemas import AgentOperation

# Fixed deterministic order for architecture / acceptance tests.
SPINE_AGENT_IDS: tuple[str, ...] = (
    "creative_director",
    "harmony",
    "melody_motif",
    "arrangement",
    "critic",
)

SPINE_OPERATIONS: dict[str, AgentOperation] = {
    "creative_director": AgentOperation.PLAN,
    "harmony": AgentOperation.PROPOSE,
    "melody_motif": AgentOperation.PROPOSE,
    "arrangement": AgentOperation.PROPOSE,
    "critic": AgentOperation.CRITIQUE,
}

# On Critic revise, re-enter here (not creative_director).
SPINE_REVISE_ENTRY_AGENT = "harmony"

PIPELINE_ID = "agent_spine_v1"
