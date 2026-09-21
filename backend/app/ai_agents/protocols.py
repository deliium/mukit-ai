"""MusicAgent protocol — common invoke surface for all agents."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.ai_agents.schemas import AgentDescriptor, AgentRunRequest, AgentRunResult


@runtime_checkable
class MusicAgent(Protocol):
    """In-process agent capability — never writes projects or SQLite."""

    @property
    def descriptor(self) -> AgentDescriptor:
        """Public discovery descriptor (``mutates_composition`` always false)."""

    async def run(self, request: AgentRunRequest) -> AgentRunResult:
        """Execute one agent operation against a context snapshot."""
