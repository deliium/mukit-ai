"""Stub adapters for non-spine registered agents (ready but optional)."""

from __future__ import annotations

import logging
from typing import Any

from app.ai_agents.agents.common import BaseMusicAgent
from app.ai_agents.schemas import (
    AGENT_CONTENT_TYPES,
    AgentArtifactKind,
    AgentArtifactV1,
    AgentRunRequest,
    AgentRunResult,
)

logger = logging.getLogger(__name__)

_STUB_CONTENT: dict[str, str] = {
    "structure_form": "composition.plan.v1",
    "orchestration": "orchestration.recommendation",
    "performance_expression": "expression.recommendation",
    "production": "production.notes",
}

_STUB_SLOT: dict[str, str] = {
    "structure_form": "structure_plan",
    "orchestration": "orchestration_artifact",
    "performance_expression": "expression_artifact",
    "production": "production_artifact",
}


class StubAgent(BaseMusicAgent):
    """Registered stub — typed recommendation artifacts; no draft mutation."""

    async def _run_impl(self, request: AgentRunRequest) -> AgentRunResult:
        content_type = _STUB_CONTENT.get(self._descriptor.id, "agent.recommendation.v1")
        if content_type not in AGENT_CONTENT_TYPES:
            content_type = "agent.recommendation.v1"
        kind = (
            AgentArtifactKind.PLAN
            if content_type == "composition.plan.v1"
            else AgentArtifactKind.RECOMMENDATION
        )
        payload: dict[str, Any] = {
            "schema": content_type,
            "note": f"stub_{self._descriptor.id}",
            "mutates_composition": False,
        }
        if self._descriptor.id == "production":
            payload["neural_job_ref"] = None
        art = AgentArtifactV1(
            kind=kind,
            producer_agent_id=self._descriptor.id,
            content_type=content_type,
            payload=payload,
            source_fingerprint=request.context.source_fingerprint,
            provenance=self._provenance(f"agent_{self._descriptor.id}_run", runtime="stub"),
        )
        slot = _STUB_SLOT.get(self._descriptor.id)
        slots = {slot: art} if slot else {}
        logger.debug(
            "Stub agent run",
            extra={"agent_id": self._descriptor.id, "content_type": content_type},
        )
        return AgentRunResult(
            agent_id=self._descriptor.id,
            operation=request.operation,
            artifacts=[art],
            updated_context_slots=slots,
            provenance_stage=self._stage(f"agent_{self._descriptor.id}_run", runtime="stub"),
            warning_codes=["stub_adapter"],
        )
