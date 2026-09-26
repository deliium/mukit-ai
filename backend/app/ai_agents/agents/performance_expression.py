"""Performance expression agent. Advise stays a plan until apply_expression."""

from __future__ import annotations

import logging

from app.ai_agents.agents.common import BaseMusicAgent
from app.ai_agents.artifact_schemas import AgentPerformancePlanV1
from app.ai_agents.progressive_realize import RealizeService, apply_realized_composition
from app.ai_agents.schemas import (
    AGENT_PERFORMANCE_PLAN_SCHEMA,
    AgentArtifactKind,
    AgentArtifactV1,
    AgentRunRequest,
    AgentRunResult,
)
from app.services.composition_expression import realize_expression_marks

logger = logging.getLogger(__name__)


class PerformanceExpressionAgent(BaseMusicAgent):
    async def _run_impl(self, request: AgentRunRequest) -> AgentRunResult:
        selection = request.selection or {}
        apply_expression = bool(selection.get("apply_expression"))
        plan = AgentPerformancePlanV1(
            velocity_curve="section_density",
            expression_notes="dynamics_and_velocity",
        )
        art = AgentArtifactV1(
            kind=AgentArtifactKind.PLAN,
            producer_agent_id=self._descriptor.id,
            content_type=AGENT_PERFORMANCE_PLAN_SCHEMA,
            payload=plan.model_dump(mode="json"),
            source_fingerprint=request.context.source_fingerprint,
        )
        if not apply_expression:
            logger.info(
                "Expression advise plan only",
                extra={"stage_id": "expression", "changed_note_count": 0, "dynamic_mark_count": 0},
            )
            return AgentRunResult(
                agent_id=self._descriptor.id,
                operation=request.operation,
                artifacts=[art],
                updated_context_slots={"expression_artifact": art},
            )
        bands = {
            int(item["start_bar"]): str(item["density"])
            for item in selection.get("section_bands") or []
            if isinstance(item, dict) and "start_bar" in item
        }
        realized = realize_expression_marks(
            request.context.working_draft_composition,
            bands,
        )
        updated = apply_realized_composition(
            current_draft=request.context.working_draft_composition,
            realized=realized,
            service=RealizeService.EXPRESSION_CANDIDATE,
        )
        return AgentRunResult(
            agent_id=self._descriptor.id,
            operation=request.operation,
            artifacts=[art],
            updated_context_slots={"expression_artifact": art},
            working_draft_update=updated,
        )
