"""Deterministic fake agents for CI / ``LLM_FAKE_MODE=1``."""

from __future__ import annotations

import logging
import os
from typing import Any, Mapping

from app.ai_agents.binding import (
    bound_model_public,
    required_model_capabilities_for,
    resolve_agent_model,
    resource_hints_from_descriptor,
)
from app.ai_agents.errors import AgentError, AgentOperationUnsupportedError
from app.ai_agents.progressive_realize import (
    RealizeService,
    apply_realized_composition,
)
from app.ai_agents.artifact_schemas import (
    AgentFormPlanV1,
    AgentOrchestrationPlanV1,
    AgentPerformancePlanV1,
    AgentProductionPlanV1,
    FormPlanSection,
)
from app.ai_agents.agents.typed_emit import (
    AGENT_ARRANGEMENT_PLAN_SCHEMA,
    AGENT_COMPOSITION_PATCH_SCHEMA,
    AGENT_FORM_PLAN_SCHEMA,
    AGENT_HARMONY_PLAN_SCHEMA,
    AGENT_MOTIF_PLAN_SCHEMA,
    arrangement_plan_from_composition,
    composition_patch_payload,
    depends_on_edges,
    form_plan_from_composition,
    harmony_plan_from_composition,
    make_plan_artifact,
    motif_plan_from_composition,
)
from app.ai_agents.schemas import (
    AGENT_CONTENT_TYPES,
    AGENT_FORM_PLAN_SCHEMA,
    AGENT_ORCHESTRATION_PLAN_SCHEMA,
    AGENT_PERFORMANCE_PLAN_SCHEMA,
    AGENT_PRODUCTION_PLAN_SCHEMA,
    AGENT_SPINE_WORKFLOW_ID,
    AgentArtifactKind,
    AgentArtifactProvenance,
    AgentArtifactV1,
    AgentBriefV1,
    AgentCritiqueV1,
    AgentDescriptor,
    AgentOperation,
    AgentRunRequest,
    AgentRunResult,
    AgentStatus,
    AgentWorkflowPlanStep,
    AgentWorkflowPlanV1,
    CritiqueRecommendation,
    KNOWN_AGENT_IDS,
    MusicAgentCapability,
)

logger = logging.getLogger(__name__)

_DISPLAY: dict[str, str] = {
    "creative_director": "Creative Director",
    "structure_form": "Structure / Form",
    "harmony": "Harmony",
    "melody_motif": "Melody / Motif",
    "arrangement": "Arrangement",
    "orchestration": "Orchestration",
    "performance_expression": "Performance / Expression",
    "production": "Production",
    "critic": "Critic",
}

_OPS: dict[str, list[AgentOperation]] = {
    "creative_director": [AgentOperation.PLAN, AgentOperation.RUN],
    "structure_form": [AgentOperation.PLAN, AgentOperation.RUN],
    "harmony": [AgentOperation.PROPOSE, AgentOperation.RUN],
    "melody_motif": [AgentOperation.PROPOSE, AgentOperation.RUN],
    "arrangement": [AgentOperation.PROPOSE, AgentOperation.RUN],
    "orchestration": [AgentOperation.PROPOSE, AgentOperation.ADVISE, AgentOperation.RUN],
    "performance_expression": [AgentOperation.ADVISE, AgentOperation.RUN],
    "production": [AgentOperation.ADVISE, AgentOperation.RUN],
    "critic": [AgentOperation.CRITIQUE, AgentOperation.RUN],
}

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


def _fake_stub_payload(agent_id: str, content_type: str) -> dict[str, Any]:
    if content_type == AGENT_FORM_PLAN_SCHEMA:
        return AgentFormPlanV1(
            sections=[FormPlanSection(label="A", start_bar=1, bar_count=4)],
            comment=f"fake_{agent_id}",
        ).model_dump(mode="json")
    if content_type == AGENT_ORCHESTRATION_PLAN_SCHEMA:
        return AgentOrchestrationPlanV1(comment=f"fake_{agent_id}").model_dump(mode="json")
    if content_type == AGENT_PERFORMANCE_PLAN_SCHEMA:
        return AgentPerformancePlanV1(expression_notes=f"fake_{agent_id}").model_dump(
            mode="json"
        )
    if content_type == AGENT_PRODUCTION_PLAN_SCHEMA:
        return AgentProductionPlanV1(mix_notes=f"fake_{agent_id}").model_dump(mode="json")
    return {"schema": content_type, "note": f"fake_{agent_id}", "mutates_composition": False}


class FakeAgent:
    """Base fake agent — deterministic artifacts; never mutates durable Composition."""

    def __init__(self, descriptor: AgentDescriptor) -> None:
        self._descriptor = descriptor

    @property
    def descriptor(self) -> AgentDescriptor:
        return self._descriptor

    async def run(self, request: AgentRunRequest) -> AgentRunResult:
        if request.agent_id != self._descriptor.id:
            raise AgentOperationUnsupportedError(
                f"Agent id mismatch: {request.agent_id}",
                agent_id=self._descriptor.id,
            )
        if request.operation not in self._descriptor.supported_operations:
            raise AgentOperationUnsupportedError(
                f"Unsupported operation {request.operation.value}",
                agent_id=self._descriptor.id,
            )
        logger.info(
            "Fake agent run",
            extra={
                "agent_id": self._descriptor.id,
                "operation": request.operation.value,
                "model_id": self._descriptor.bound_model_id,
            },
        )
        return await self._run_impl(request)

    async def _run_impl(self, request: AgentRunRequest) -> AgentRunResult:
        raise NotImplementedError

    def _stage(self, operation: str) -> dict[str, Any]:
        return {
            "operation": operation,
            "agent_id": self._descriptor.id,
            "agent_capability": self._descriptor.capability.value,
            "model_id": self._descriptor.bound_model_id,
            "runtime": "fake",
            "capability": (
                self._descriptor.required_model_capabilities[0].value
                if self._descriptor.required_model_capabilities
                else None
            ),
            "model_version": "fake-v1",
        }

    def _provenance(self, operation: str) -> AgentArtifactProvenance:
        stage = self._stage(operation)
        return AgentArtifactProvenance(
            operation=stage["operation"],
            model_id=stage.get("model_id"),
            runtime=stage.get("runtime"),
            capability=stage.get("capability"),
            agent_id=stage.get("agent_id"),
            agent_capability=stage.get("agent_capability"),
            model_version=stage.get("model_version"),
        )


class FakeCreativeDirectorAgent(FakeAgent):
    async def _run_impl(self, request: AgentRunRequest) -> AgentRunResult:
        brief = AgentBriefV1(
            intent="fake multi-agent spine brief",
            mood="calm",
            genre="classical",
            constraints=["preserve_melody"],
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
            provenance=self._provenance("agent_creative_director_plan"),
        )
        form_art = make_plan_artifact(
            kind=AgentArtifactKind.PLAN,
            producer_agent_id=self._descriptor.id,
            content_type=AGENT_FORM_PLAN_SCHEMA,
            payload=form_plan_from_composition(request.context.working_draft_composition),
            source_fingerprint=request.context.source_fingerprint,
            provenance=self._provenance("agent_creative_director_plan"),
            parent_artifact_ids=[brief_art.artifact_id],
        )
        plan_art = AgentArtifactV1(
            kind=AgentArtifactKind.WORKFLOW_PLAN,
            producer_agent_id=self._descriptor.id,
            content_type="agent.workflow_plan.v1",
            payload=plan.model_dump(mode="json"),
            source_fingerprint=request.context.source_fingerprint,
            parent_artifact_ids=[brief_art.artifact_id],
            provenance=self._provenance("agent_creative_director_plan"),
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
            provenance_stage=self._stage("agent_creative_director_plan"),
        )


class FakeHarmonyAgent(FakeAgent):
    async def _run_impl(self, request: AgentRunRequest) -> AgentRunResult:
        draft = request.context.working_draft_composition
        nudged = draft.model_copy(update={"tempo": min(200, int(draft.tempo) + 1)})
        realized = apply_realized_composition(
            current_draft=draft,
            realized=nudged,
            service=RealizeService.REHARMONIZE_CANDIDATE,
        )
        harmony_plan = make_plan_artifact(
            kind=AgentArtifactKind.PLAN,
            producer_agent_id=self._descriptor.id,
            content_type=AGENT_HARMONY_PLAN_SCHEMA,
            payload=harmony_plan_from_composition(realized),
            source_fingerprint=request.context.source_fingerprint,
            provenance=self._provenance("agent_harmony_propose"),
            parent_artifact_ids=[request.context.brief.artifact_id]
            if request.context.brief
            else [],
            depends_on=depends_on_edges(request.context.brief),
        )
        patch = make_plan_artifact(
            kind=AgentArtifactKind.CANDIDATE_PATCH,
            producer_agent_id=self._descriptor.id,
            content_type=AGENT_COMPOSITION_PATCH_SCHEMA,
            payload=composition_patch_payload(
                realize_service="reharmonize_candidate",
                op_refs=["fake_tempo_nudge"],
                recipe="fake_harmony",
                source_fingerprint=request.context.source_fingerprint,
            ),
            source_fingerprint=request.context.source_fingerprint,
            provenance=self._provenance("agent_harmony_propose"),
            parent_artifact_ids=[harmony_plan.artifact_id],
            depends_on=depends_on_edges(harmony_plan),
        )
        art = AgentArtifactV1(
            kind=AgentArtifactKind.CANDIDATE_PATCH,
            producer_agent_id=self._descriptor.id,
            content_type="reharmonize.candidate",
            payload={
                "schema": "reharmonize.candidate",
                "tempo": realized.tempo,
                "note": "fake_harmony_nudge",
            },
            source_fingerprint=request.context.source_fingerprint,
            provenance=self._provenance("agent_harmony_propose"),
        )
        return AgentRunResult(
            agent_id=self._descriptor.id,
            operation=request.operation,
            artifacts=[harmony_plan, patch, art],
            updated_context_slots={"harmony_artifact": harmony_plan},
            working_draft_update=realized,
            provenance_stage=self._stage("agent_harmony_propose"),
        )


class FakeMelodyMotifAgent(FakeAgent):
    async def _run_impl(self, request: AgentRunRequest) -> AgentRunResult:
        draft = request.context.working_draft_composition
        motif_plan = make_plan_artifact(
            kind=AgentArtifactKind.PLAN,
            producer_agent_id=self._descriptor.id,
            content_type=AGENT_MOTIF_PLAN_SCHEMA,
            payload=motif_plan_from_composition(draft),
            source_fingerprint=request.context.source_fingerprint,
            provenance=self._provenance("agent_melody_motif_propose"),
            parent_artifact_ids=[
                *(
                    [request.context.brief.artifact_id]
                    if request.context.brief
                    else []
                ),
                *(
                    [request.context.harmony_artifact.artifact_id]
                    if request.context.harmony_artifact
                    else []
                ),
            ],
            depends_on=depends_on_edges(
                request.context.brief, request.context.harmony_artifact
            ),
        )
        patch = make_plan_artifact(
            kind=AgentArtifactKind.CANDIDATE_PATCH,
            producer_agent_id=self._descriptor.id,
            content_type=AGENT_COMPOSITION_PATCH_SCHEMA,
            payload=composition_patch_payload(
                realize_service="motif_apply",
                op_refs=["fake_passthrough"],
                recipe="fake_melody",
                source_fingerprint=request.context.source_fingerprint,
            ),
            source_fingerprint=request.context.source_fingerprint,
            provenance=self._provenance("agent_melody_motif_propose"),
            parent_artifact_ids=[motif_plan.artifact_id],
            depends_on=depends_on_edges(motif_plan),
        )
        art = AgentArtifactV1(
            kind=AgentArtifactKind.CANDIDATE_PATCH,
            producer_agent_id=self._descriptor.id,
            content_type="melody.draft",
            payload={"schema": "melody.draft", "note": "fake_melody_passthrough"},
            source_fingerprint=request.context.source_fingerprint,
            provenance=self._provenance("agent_melody_motif_propose"),
        )
        realized = apply_realized_composition(
            current_draft=draft,
            realized=draft,
            service=RealizeService.MOTIF_APPLY,
        )
        return AgentRunResult(
            agent_id=self._descriptor.id,
            operation=request.operation,
            artifacts=[motif_plan, patch, art],
            updated_context_slots={"melody_artifact": motif_plan},
            working_draft_update=realized,
            provenance_stage=self._stage("agent_melody_motif_propose"),
        )


class FakeArrangementAgent(FakeAgent):
    async def _run_impl(self, request: AgentRunRequest) -> AgentRunResult:
        draft = request.context.working_draft_composition
        arrangement_plan = make_plan_artifact(
            kind=AgentArtifactKind.PLAN,
            producer_agent_id=self._descriptor.id,
            content_type=AGENT_ARRANGEMENT_PLAN_SCHEMA,
            payload=arrangement_plan_from_composition(draft),
            source_fingerprint=request.context.source_fingerprint,
            provenance=self._provenance("agent_arrangement_propose"),
            parent_artifact_ids=[
                *(
                    [request.context.structure_plan.artifact_id]
                    if request.context.structure_plan
                    else []
                ),
                *(
                    [request.context.harmony_artifact.artifact_id]
                    if request.context.harmony_artifact
                    else []
                ),
            ],
            depends_on=depends_on_edges(
                request.context.structure_plan, request.context.harmony_artifact
            ),
        )
        patch = make_plan_artifact(
            kind=AgentArtifactKind.CANDIDATE_PATCH,
            producer_agent_id=self._descriptor.id,
            content_type=AGENT_COMPOSITION_PATCH_SCHEMA,
            payload=composition_patch_payload(
                realize_service="arrangement_candidate",
                op_refs=["fake_passthrough"],
                recipe="fake_arrangement",
                source_fingerprint=request.context.source_fingerprint,
            ),
            source_fingerprint=request.context.source_fingerprint,
            provenance=self._provenance("agent_arrangement_propose"),
            parent_artifact_ids=[arrangement_plan.artifact_id],
            depends_on=depends_on_edges(arrangement_plan),
        )
        art = AgentArtifactV1(
            kind=AgentArtifactKind.CANDIDATE_PATCH,
            producer_agent_id=self._descriptor.id,
            content_type="arrangement.candidate",
            payload={
                "schema": "arrangement.candidate",
                "note": "fake_arrangement_passthrough",
                "track_count": len(draft.tracks),
            },
            source_fingerprint=request.context.source_fingerprint,
            provenance=self._provenance("agent_arrangement_propose"),
        )
        realized = apply_realized_composition(
            current_draft=draft,
            realized=draft,
            service=RealizeService.ARRANGEMENT_CANDIDATE,
        )
        return AgentRunResult(
            agent_id=self._descriptor.id,
            operation=request.operation,
            artifacts=[arrangement_plan, patch, art],
            updated_context_slots={"arrangement_candidate": arrangement_plan},
            working_draft_update=realized,
            provenance_stage=self._stage("agent_arrangement_propose"),
        )


class FakeCriticAgent(FakeAgent):
    async def _run_impl(self, request: AgentRunRequest) -> AgentRunResult:
        params = getattr(request, "parameters", None) or {}
        emit_climax = bool(params.get("fake_emit_climax_finding"))
        findings = []
        if emit_climax:
            from app.critique_schemas import (
                CritiqueAffectedRange,
                CritiqueFindingEvidence,
                CritiqueFindingV1,
            )

            findings = [
                CritiqueFindingV1(
                    stratum="stylistic",
                    category="contrast",
                    code="climax_lacks_contrast",
                    severity="info",
                    explanation="Fake climax contrast observation for CI.",
                    affected_range=CritiqueAffectedRange(start_bar=5, end_bar=8),
                    evidence=CritiqueFindingEvidence(
                        metrics={"fake": True},
                        refs=["section_index:1"],
                    ),
                )
            ]
        critique = AgentCritiqueV1(
            recommendation=CritiqueRecommendation.APPROVE,
            reason_codes=["fake_ok"],
            summary="Fake critic approves the spine candidate.",
            findings=findings,
            model_critique_status="skipped",
        )
        art = AgentArtifactV1(
            kind=AgentArtifactKind.CRITIQUE,
            producer_agent_id=self._descriptor.id,
            content_type="agent.critique.v1",
            payload=critique.model_dump(mode="json"),
            source_fingerprint=request.context.source_fingerprint,
            provenance=self._provenance("agent_critic_critique"),
        )
        return AgentRunResult(
            agent_id=self._descriptor.id,
            operation=request.operation,
            artifacts=[art],
            updated_context_slots={"critique": art},
            recommendation=CritiqueRecommendation.APPROVE,
            provenance_stage=self._stage("agent_critic_critique"),
        )


class FakeStubAgent(FakeAgent):
    """Registered stub for non-spine agents."""

    async def _run_impl(self, request: AgentRunRequest) -> AgentRunResult:
        content_type = _STUB_CONTENT.get(self._descriptor.id, "agent.recommendation.v1")
        if content_type not in AGENT_CONTENT_TYPES:
            content_type = "agent.recommendation.v1"
        kind = (
            AgentArtifactKind.PLAN
            if content_type.endswith("_plan.v1") or content_type == "composition.plan.v1"
            else AgentArtifactKind.RECOMMENDATION
        )
        payload = _fake_stub_payload(self._descriptor.id, content_type)
        art = AgentArtifactV1(
            kind=kind,
            producer_agent_id=self._descriptor.id,
            content_type=content_type,
            payload=payload,
            source_fingerprint=request.context.source_fingerprint,
            provenance=self._provenance(f"agent_{self._descriptor.id}_run"),
        )
        slot = _STUB_SLOT.get(self._descriptor.id)
        slots = {slot: art} if slot else {}
        return AgentRunResult(
            agent_id=self._descriptor.id,
            operation=request.operation,
            artifacts=[art],
            updated_context_slots=slots,
            provenance_stage=self._stage(f"agent_{self._descriptor.id}_run"),
            warning_codes=["fake_stub"],
        )


_FACTORIES: dict[str, type[FakeAgent]] = {
    "creative_director": FakeCreativeDirectorAgent,
    "harmony": FakeHarmonyAgent,
    "melody_motif": FakeMelodyMotifAgent,
    "arrangement": FakeArrangementAgent,
    "critic": FakeCriticAgent,
}


def build_fake_agent(agent_id: str, *, env: Mapping[str, str] | None = None) -> FakeAgent:
    if agent_id not in KNOWN_AGENT_IDS:
        raise AgentError(f"Unknown agent id: {agent_id}", agent_id=agent_id, code="agent_not_found")

    bound_model_id: str | None = None
    status = AgentStatus.READY
    health_detail = "fake"
    resource = resource_hints_from_descriptor(locality_preference="local")
    try:
        resolved = resolve_agent_model(agent_id, env=env)
        bound_model_id = resolved.resolved_model_id
        resource = resource_hints_from_descriptor(
            locality_preference="local",
            bound=resolved.descriptor,
        )
        _ = bound_model_public(resolved)
    except Exception as exc:  # noqa: BLE001 — degrade, do not crash bootstrap
        logger.warning(
            "Fake agent binding degraded",
            extra={"agent_id": agent_id, "reason": type(exc).__name__},
        )
        status = AgentStatus.DEGRADED
        bound_model_id = "fake:unresolved"
        health_detail = "binding_degraded"

    descriptor = AgentDescriptor(
        id=agent_id,
        display_name=_DISPLAY[agent_id],
        capability=MusicAgentCapability(agent_id),
        supported_operations=_OPS[agent_id],
        accepted_artifact_types=["composition.v2", "agent.brief.v1", "agent.workflow_plan.v1"],
        produced_artifact_types=["agent.brief.v1", "agent.critique.v1", "arrangement.candidate"],
        required_model_capabilities=required_model_capabilities_for(agent_id),
        resource=resource,
        status=status,
        bound_model_id=bound_model_id,
        health_detail=health_detail,
    )
    cls = _FACTORIES.get(agent_id, FakeStubAgent)
    return cls(descriptor)


def build_all_fake_agents(*, env: Mapping[str, str] | None = None) -> list[FakeAgent]:
    source = env if env is not None else os.environ
    return [build_fake_agent(agent_id, env=source) for agent_id in KNOWN_AGENT_IDS]
