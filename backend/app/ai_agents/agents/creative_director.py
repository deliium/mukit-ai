"""Creative Director — typed brief + form plan + workflow plan (no draft mutation)."""

from __future__ import annotations

import logging

from app.ai_agents.agents.common import BaseMusicAgent, selection_str
from app.ai_agents.agents.typed_emit import (
    AGENT_FORM_PLAN_SCHEMA,
    form_plan_from_composition,
    load_compiled_project_plan,
    make_plan_artifact,
)
from app.services.autonomous_project_plan import merge_director_text
from app.ai_agents.schemas import (
    AGENT_SPINE_WORKFLOW_ID,
    AgentArtifactKind,
    AgentArtifactV1,
    AgentBriefV1,
    AgentOperation,
    AgentRunRequest,
    AgentRunResult,
    AgentWorkflowPlanStep,
    AgentWorkflowPlanV1,
)

logger = logging.getLogger(__name__)


class CreativeDirectorAgent(BaseMusicAgent):
    """Language/planner-bound brief + spine workflow plan from composition context."""

    async def _run_impl(self, request: AgentRunRequest) -> AgentRunResult:
        draft = request.context.working_draft_composition
        selection = request.selection or {}
        compiled = load_compiled_project_plan(selection)
        if compiled is not None:
            return self._emit_project_plan(request, compiled)
        intent = selection_str(
            selection,
            "intent",
            default=f"Develop {draft.key} {draft.time_signature} composition at tempo {draft.tempo}",
        )
        mood = selection_str(selection, "mood", default="balanced") or "balanced"
        genre = selection_str(selection, "genre", default="contemporary") or "contemporary"
        constraints = selection.get("constraints")
        if not isinstance(constraints, list):
            constraints = ["preserve_melody", "preview_first"]
        constraints = [str(c)[:120] for c in constraints[:16]]

        brief = AgentBriefV1(
            intent=(intent or "multi-agent spine")[:500],
            mood=mood[:64],
            genre=genre[:64],
            constraints=constraints,
            stop_criteria=["critic_approve"],
        )
        plan = AgentWorkflowPlanV1(
            workflow_id=AGENT_SPINE_WORKFLOW_ID,
            steps=[
                AgentWorkflowPlanStep(agent_id="creative_director"),
                AgentWorkflowPlanStep(agent_id="harmony", operation=AgentOperation.PROPOSE),
                AgentWorkflowPlanStep(agent_id="melody_motif", operation=AgentOperation.PROPOSE),
                AgentWorkflowPlanStep(agent_id="arrangement", operation=AgentOperation.PROPOSE),
                AgentWorkflowPlanStep(agent_id="critic", operation=AgentOperation.CRITIQUE),
            ],
            max_revisions=0,
        )
        brief_art = AgentArtifactV1(
            kind=AgentArtifactKind.BRIEF,
            producer_agent_id=self._descriptor.id,
            content_type="agent.brief.v1",
            payload=brief.model_dump(mode="json"),
            source_fingerprint=request.context.source_fingerprint,
            provenance=self._provenance("agent_creative_director_plan", runtime="language_planner"),
        )
        form_art = make_plan_artifact(
            kind=AgentArtifactKind.PLAN,
            producer_agent_id=self._descriptor.id,
            content_type=AGENT_FORM_PLAN_SCHEMA,
            payload=form_plan_from_composition(draft),
            source_fingerprint=request.context.source_fingerprint,
            provenance=self._provenance("agent_creative_director_plan", runtime="language_planner"),
            parent_artifact_ids=[brief_art.artifact_id],
            depends_on=[],
        )
        plan_art = AgentArtifactV1(
            kind=AgentArtifactKind.WORKFLOW_PLAN,
            producer_agent_id=self._descriptor.id,
            content_type="agent.workflow_plan.v1",
            payload=plan.model_dump(mode="json"),
            source_fingerprint=request.context.source_fingerprint,
            parent_artifact_ids=[brief_art.artifact_id],
            provenance=self._provenance("agent_creative_director_plan", runtime="language_planner"),
        )
        logger.info(
            "Creative director artifacts produced",
            extra={
                "agent_id": self._descriptor.id,
                "content_types": [
                    brief_art.content_type,
                    form_art.content_type,
                    plan_art.content_type,
                ],
            },
        )
        return AgentRunResult(
            agent_id=self._descriptor.id,
            operation=request.operation,
            artifacts=[brief_art, form_art, plan_art],
            updated_context_slots={
                "brief": brief_art,
                "structure_plan": form_art,
                "workflow_plan": plan_art,
            },
            provenance_stage=self._stage("agent_creative_director_plan", runtime="language_planner"),
        )

    def _emit_project_plan(
        self,
        request: AgentRunRequest,
        compiled,
    ) -> AgentRunResult:
        merged = merge_director_text(compiled, compiled)
        art = make_plan_artifact(
            kind=AgentArtifactKind.PLAN,
            producer_agent_id=self._descriptor.id,
            content_type="project.plan.v1",
            payload=merged.model_dump(mode="json"),
            source_fingerprint=request.context.source_fingerprint,
            provenance=self._provenance("agent_creative_director_plan", runtime="language_planner"),
        )
        logger.info(
            "Creative director project plan produced",
            extra={
                "agent_id": self._descriptor.id,
                "operation": request.operation.value,
                "content_types": [art.content_type],
            },
        )
        logger.debug(
            "Director plan left the draft unchanged",
            extra={"agent_id": self._descriptor.id, "operation": request.operation.value},
        )
        return AgentRunResult(
            agent_id=self._descriptor.id,
            operation=request.operation,
            artifacts=[art],
            updated_context_slots={"project_plan": art},
            provenance_stage=self._stage("agent_creative_director_plan", runtime="language_planner"),
        )
