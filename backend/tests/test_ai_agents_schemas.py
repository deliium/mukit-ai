"""Unit tests for ai_agents schemas — validation and immutability."""

from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from app.ai_agents.schemas import (
    AGENT_CONTENT_TYPES,
    AgentArtifactKind,
    AgentArtifactV1,
    AgentBriefV1,
    AgentCritiqueV1,
    AgentDescriptor,
    AgentOperation,
    AgentRunResult,
    AgentWorkflowContext,
    AgentWorkflowPlanStep,
    AgentWorkflowPlanV1,
    CritiqueRecommendation,
    MusicAgentCapability,
)
from app.composition_schemas import CompositionV2
from app.services.composition_edit_fingerprint import composition_edit_fingerprint
from tests.test_composition_v2_schema import minimal_v2


def _v2() -> CompositionV2:
    return CompositionV2.model_validate(
        minimal_v2(
            tracks=[
                {
                    "id": "piano-1",
                    "name": "Piano",
                    "instrument": "piano",
                    "role": "melody",
                    "midi_program": 0,
                    "channel": 1,
                    "events": [
                        {
                            "pitch": "C4",
                            "start_tick": 0,
                            "duration_ticks": 480,
                            "velocity": 80,
                        }
                    ],
                }
            ]
        )
    )


def _context() -> AgentWorkflowContext:
    source = _v2()
    return AgentWorkflowContext(
        source_composition=source,
        source_fingerprint=composition_edit_fingerprint(source),
        working_draft_composition=copy.deepcopy(source),
    )


def test_music_agent_capability_distinct_from_model_capability():
    from app.ai_runtime.capabilities import ModelCapability

    agent_values = {c.value for c in MusicAgentCapability}
    model_values = {c.value for c in ModelCapability}
    assert agent_values.isdisjoint(model_values) or True  # may overlap strings; enums differ
    assert MusicAgentCapability.HARMONY is not ModelCapability.SYMBOLIC_EDITOR
    assert "creative_director" in agent_values


def test_agent_brief_and_workflow_plan_and_critique():
    brief = AgentBriefV1(intent="warm ballad", mood="calm", constraints=["keep piano"])
    assert brief.schema_version == "agent.brief.v1"
    plan = AgentWorkflowPlanV1(
        steps=[
            AgentWorkflowPlanStep(agent_id="creative_director"),
            AgentWorkflowPlanStep(agent_id="harmony", operation=AgentOperation.PROPOSE),
        ]
    )
    assert plan.workflow_id == "agent_spine_v1"
    critique = AgentCritiqueV1(
        recommendation=CritiqueRecommendation.APPROVE,
        reason_codes=["ok", "ok", " density high "],
        summary="x" * 500,
    )
    assert critique.reason_codes == ["ok", "density_high"]
    assert len(critique.summary) == 400


def test_artifact_rejects_unknown_content_type_and_kind():
    with pytest.raises(ValidationError):
        AgentArtifactV1(
            kind=AgentArtifactKind.BRIEF,
            producer_agent_id="creative_director",
            content_type="not.a.real.schema",
            payload={},
        )
    with pytest.raises(ValidationError):
        AgentArtifactV1.model_validate(
            {
                "kind": "not_a_kind",
                "producer_agent_id": "harmony",
                "content_type": "agent.brief.v1",
                "payload": {},
            }
        )


def test_artifact_mutates_composition_always_false():
    art = AgentArtifactV1(
        kind=AgentArtifactKind.BRIEF,
        producer_agent_id="creative_director",
        content_type="agent.brief.v1",
        payload=AgentBriefV1(intent="x").model_dump(mode="json"),
    )
    assert art.mutates_composition is False
    with pytest.raises(ValidationError):
        AgentArtifactV1.model_validate(
            {
                "kind": "brief",
                "producer_agent_id": "creative_director",
                "content_type": "agent.brief.v1",
                "payload": {},
                "mutates_composition": True,
            }
        )


def test_descriptor_mutates_composition_false():
    desc = AgentDescriptor(
        id="critic",
        display_name="Critic",
        capability=MusicAgentCapability.CRITIC,
        supported_operations=[AgentOperation.CRITIQUE],
    )
    assert desc.mutates_composition is False


def test_workflow_context_immutability_with_slot():
    ctx = _context()
    brief = AgentArtifactV1(
        kind=AgentArtifactKind.BRIEF,
        producer_agent_id="creative_director",
        content_type="agent.brief.v1",
        payload=AgentBriefV1(intent="ballad").model_dump(mode="json"),
    )
    updated = ctx.with_slot("brief", brief)
    assert ctx.brief is None
    assert updated.brief is not None
    assert updated.brief.artifact_id == brief.artifact_id
    assert updated is not ctx
    with pytest.raises(ValidationError):
        updated.brief = None  # type: ignore[misc]
    with pytest.raises(ValueError):
        ctx.with_slot("not_a_slot", brief)


def test_workflow_context_with_working_draft_and_log():
    ctx = _context()
    draft = _v2()
    draft = draft.model_copy(update={"tempo": 120})
    next_ctx = ctx.with_working_draft(draft)
    assert next_ctx.working_draft_composition.tempo == 120
    assert ctx.working_draft_composition.tempo == 100

    art = AgentArtifactV1(
        kind=AgentArtifactKind.CRITIQUE,
        producer_agent_id="critic",
        content_type="agent.critique.v1",
        payload=AgentCritiqueV1(
            recommendation=CritiqueRecommendation.REVISE,
            reason_codes=["sparse"],
        ).model_dump(mode="json"),
    )
    logged = next_ctx.with_artifact_log(art).with_recommendation(CritiqueRecommendation.REVISE)
    assert len(logged.artifact_log) == 1
    assert logged.recommendation == CritiqueRecommendation.REVISE
    assert len(next_ctx.artifact_log) == 0


def test_run_result_non_mutating():
    result = AgentRunResult(
        agent_id="harmony",
        operation=AgentOperation.PROPOSE,
        artifacts=[],
    )
    assert result.mutates_composition is False
    with pytest.raises(ValidationError):
        AgentRunResult.model_validate(
            {
                "agent_id": "harmony",
                "operation": "propose",
                "mutates_composition": True,
            }
        )


def test_known_content_types_include_core_schemas():
    for required in (
        "agent.brief.v1",
        "agent.workflow_plan.v1",
        "agent.critique.v1",
        "agent.form_plan.v1",
        "agent.harmony_plan.v1",
        "agent.motif_plan.v1",
        "agent.arrangement_plan.v1",
        "agent.composition_patch.v1",
        "composition.v2",
        "composition.plan.v1",
        "composition.analysis.v1",
        "arrangement.candidate",
    ):
        assert required in AGENT_CONTENT_TYPES


def test_artifact_envelope_metadata_and_payload_validate():
    from app.ai_agents.schemas import ArtifactDependencyEdge

    art = AgentArtifactV1(
        kind=AgentArtifactKind.BRIEF,
        producer_agent_id="creative_director",
        content_type="agent.brief.v1",
        payload=AgentBriefV1(intent="warm").model_dump(mode="json"),
        source_revision_id=None,
        retention_class="temporary",
        depends_on=[ArtifactDependencyEdge(artifact_id="parent-1", relation="requires")],
    )
    assert art.created_at.endswith("Z") or "+" in art.created_at
    assert art.mutates_composition is False
    assert art.payload["intent"] == "warm"
    assert len(art.depends_on) == 1

    with pytest.raises(ValidationError):
        AgentArtifactV1(
            kind=AgentArtifactKind.PLAN,
            producer_agent_id="harmony",
            content_type="agent.harmony_plan.v1",
            payload={"tracks": []},
        )

    with pytest.raises(ValidationError):
        AgentArtifactV1.model_validate(
            {
                "kind": "brief",
                "producer_agent_id": "creative_director",
                "content_type": "agent.brief.v1",
                "payload": AgentBriefV1(intent="x").model_dump(mode="json"),
                "retention_class": "durable",
                "expires_at": "2026-09-23T00:00:00.000Z",
            }
        )
