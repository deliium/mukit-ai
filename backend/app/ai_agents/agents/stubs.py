"""Stub adapters for non-spine registered agents (ready but optional)."""

from __future__ import annotations

import logging
from typing import Any

from app.ai_agents.agents.common import BaseMusicAgent
from app.ai_agents.artifact_schemas import (
    AgentFormPlanV1,
    AgentOrchestrationPlanV1,
    AgentPerformancePlanV1,
    AgentProductionPlanV1,
    AgentRenderPlanV1,
    FormPlanSection,
)
from app.ai_agents.schemas import (
    AGENT_CONTENT_TYPES,
    AGENT_FORM_PLAN_SCHEMA,
    AGENT_ORCHESTRATION_PLAN_SCHEMA,
    AGENT_PERFORMANCE_PLAN_SCHEMA,
    AGENT_PRODUCTION_PLAN_SCHEMA,
    AGENT_RENDER_PLAN_SCHEMA,
    AgentArtifactKind,
    AgentArtifactV1,
    AgentRunRequest,
    AgentRunResult,
)

logger = logging.getLogger(__name__)

_STUB_CONTENT: dict[str, str] = {
    "structure_form": AGENT_FORM_PLAN_SCHEMA,
    "orchestration": AGENT_ORCHESTRATION_PLAN_SCHEMA,
    "performance_expression": AGENT_PERFORMANCE_PLAN_SCHEMA,
    "production": AGENT_PRODUCTION_PLAN_SCHEMA,
}

_STUB_SLOT: dict[str, str] = {
    "structure_form": "structure_plan",
    "orchestration": "orchestration_artifact",
    "performance_expression": "expression_artifact",
    "production": "production_artifact",
}


def _stub_payload(agent_id: str, content_type: str) -> dict[str, Any]:
    """Minimal valid typed payloads for non-spine stubs."""
    if content_type == AGENT_FORM_PLAN_SCHEMA:
        return AgentFormPlanV1(
            sections=[FormPlanSection(label="A", start_bar=1, bar_count=4)],
            comment=f"stub_{agent_id}",
        ).model_dump(mode="json")
    if content_type == AGENT_ORCHESTRATION_PLAN_SCHEMA:
        return AgentOrchestrationPlanV1(comment=f"stub_{agent_id}").model_dump(mode="json")
    if content_type == AGENT_PERFORMANCE_PLAN_SCHEMA:
        return AgentPerformancePlanV1(expression_notes=f"stub_{agent_id}").model_dump(
            mode="json"
        )
    if content_type == AGENT_PRODUCTION_PLAN_SCHEMA:
        return AgentProductionPlanV1(mix_notes=f"stub_{agent_id}").model_dump(mode="json")
    if content_type == AGENT_RENDER_PLAN_SCHEMA:
        return AgentRenderPlanV1(comment=f"stub_{agent_id}").model_dump(mode="json")
    return {"schema": content_type, "note": f"stub_{agent_id}", "mutates_composition": False}


class StubAgent(BaseMusicAgent):
    """Registered stub — typed recommendation artifacts; no draft mutation."""

    async def _run_impl(self, request: AgentRunRequest) -> AgentRunResult:
        content_type = _STUB_CONTENT.get(self._descriptor.id, "agent.recommendation.v1")
        if content_type not in AGENT_CONTENT_TYPES:
            content_type = "agent.recommendation.v1"
        kind = (
            AgentArtifactKind.PLAN
            if content_type.endswith("_plan.v1") or content_type == "composition.plan.v1"
            else AgentArtifactKind.RECOMMENDATION
        )
        payload = _stub_payload(self._descriptor.id, content_type)
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
        logger.info(
            "Stub agent run",
            extra={
                "agent_id": self._descriptor.id,
                "content_types": [content_type],
            },
        )
        return AgentRunResult(
            agent_id=self._descriptor.id,
            operation=request.operation,
            artifacts=[art],
            updated_context_slots=slots,
            provenance_stage=self._stage(f"agent_{self._descriptor.id}_run", runtime="stub"),
            warning_codes=["stub_adapter"],
        )
