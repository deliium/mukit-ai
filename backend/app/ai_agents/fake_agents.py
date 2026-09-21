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
from app.ai_agents.schemas import (
    AGENT_CONTENT_TYPES,
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
            artifacts=[brief_art, plan_art],
            updated_context_slots={"brief": brief_art, "workflow_plan": plan_art},
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
            artifacts=[art],
            updated_context_slots={"harmony_artifact": art},
            working_draft_update=realized,
            provenance_stage=self._stage("agent_harmony_propose"),
        )


class FakeMelodyMotifAgent(FakeAgent):
    async def _run_impl(self, request: AgentRunRequest) -> AgentRunResult:
        draft = request.context.working_draft_composition
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
            artifacts=[art],
            updated_context_slots={"melody_artifact": art},
            working_draft_update=realized,
            provenance_stage=self._stage("agent_melody_motif_propose"),
        )


class FakeArrangementAgent(FakeAgent):
    async def _run_impl(self, request: AgentRunRequest) -> AgentRunResult:
        draft = request.context.working_draft_composition
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
            artifacts=[art],
            updated_context_slots={"arrangement_candidate": art},
            working_draft_update=realized,
            provenance_stage=self._stage("agent_arrangement_propose"),
        )


class FakeCriticAgent(FakeAgent):
    async def _run_impl(self, request: AgentRunRequest) -> AgentRunResult:
        critique = AgentCritiqueV1(
            recommendation=CritiqueRecommendation.APPROVE,
            reason_codes=["fake_ok"],
            summary="Fake critic approves the spine candidate.",
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
            if content_type == "composition.plan.v1"
            else AgentArtifactKind.RECOMMENDATION
        )
        payload: dict[str, Any] = {
            "schema": content_type,
            "note": f"fake_{self._descriptor.id}",
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
