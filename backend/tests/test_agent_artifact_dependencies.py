"""Unit tests for artifact dependency registry validation."""

from __future__ import annotations

import pytest

from app.ai_agents.artifact_dependencies import (
    ARTIFACT_DEPENDENCY_UNSATISFIED,
    ArtifactDependencyError,
    validate_artifact_dependencies,
)
from app.ai_agents.artifact_schemas import (
    AgentArrangementPlanV1,
    AgentCompositionPatchV1,
    AgentFormPlanV1,
    AgentHarmonyPlanV1,
    AgentMotifPlanV1,
    FormPlanSection,
)
from app.ai_agents.schemas import (
    AgentArtifactKind,
    AgentArtifactV1,
    AgentBriefV1,
    AgentCritiqueV1,
    ArtifactDependencyEdge,
    CritiqueRecommendation,
)


def _art(content_type: str, payload: dict, **kwargs) -> AgentArtifactV1:
    kind = AgentArtifactKind.PLAN
    if content_type == "agent.brief.v1":
        kind = AgentArtifactKind.BRIEF
    elif content_type == "agent.critique.v1":
        kind = AgentArtifactKind.CRITIQUE
    elif content_type == "agent.composition_patch.v1":
        kind = AgentArtifactKind.CANDIDATE_PATCH
    return AgentArtifactV1(
        kind=kind,
        producer_agent_id="test",
        content_type=content_type,
        payload=payload,
        source_fingerprint=kwargs.get("source_fingerprint", "f" * 32),
        source_revision_id=kwargs.get("source_revision_id"),
        depends_on=kwargs.get("depends_on", []),
        parent_artifact_ids=kwargs.get("parent_artifact_ids", []),
    )


def test_motif_plan_requires_brief_and_harmony():
    brief = _art(
        "agent.brief.v1",
        AgentBriefV1(intent="ballad").model_dump(mode="json"),
    )
    harmony = _art(
        "agent.harmony_plan.v1",
        AgentHarmonyPlanV1(key="C major").model_dump(mode="json"),
    )
    motif = _art(
        "agent.motif_plan.v1",
        AgentMotifPlanV1(motifs=[{"motif_label": "a"}]).model_dump(mode="json"),
        depends_on=[
            ArtifactDependencyEdge(artifact_id=brief.artifact_id),
            ArtifactDependencyEdge(artifact_id=harmony.artifact_id),
        ],
        parent_artifact_ids=[brief.artifact_id, harmony.artifact_id],
    )
    validate_artifact_dependencies(motif, available_artifacts=[brief, harmony])

    motif_no_edges = _art(
        "agent.motif_plan.v1",
        AgentMotifPlanV1(motifs=[{"motif_label": "a"}]).model_dump(mode="json"),
    )
    with pytest.raises(ArtifactDependencyError) as exc:
        validate_artifact_dependencies(motif_no_edges, available_artifacts=[brief])
    assert exc.value.code == ARTIFACT_DEPENDENCY_UNSATISFIED
    assert "harmony_plan" in exc.value.missing


def test_arrangement_plan_requires_revision_or_fingerprint():
    form = _art(
        "agent.form_plan.v1",
        AgentFormPlanV1(
            sections=[FormPlanSection(label="A", start_bar=1, bar_count=4)]
        ).model_dump(mode="json"),
    )
    harmony = _art(
        "agent.harmony_plan.v1",
        AgentHarmonyPlanV1().model_dump(mode="json"),
    )
    arrangement = AgentArrangementPlanV1(
        track_hints=[{"role": "melody"}]
    ).model_dump(mode="json")
    art = AgentArtifactV1(
        kind=AgentArtifactKind.PLAN,
        producer_agent_id="arrangement",
        content_type="agent.arrangement_plan.v1",
        payload=arrangement,
        source_fingerprint=None,
        source_revision_id=None,
    )
    with pytest.raises(ArtifactDependencyError) as exc:
        validate_artifact_dependencies(
            art, available_artifacts=[form, harmony], source_fingerprint=None
        )
    assert "source_revision_or_fingerprint" in exc.value.missing

    art_ok = art.model_copy(update={"source_fingerprint": "c" * 32})
    # model_copy may skip payload re-validate; construct fresh
    art_ok = _art(
        "agent.arrangement_plan.v1",
        arrangement,
        source_fingerprint="c" * 32,
    )
    validate_artifact_dependencies(art_ok, available_artifacts=[form, harmony])


def test_revision_plan_requires_critique_revise():
    critique_approve = _art(
        "agent.critique.v1",
        AgentCritiqueV1(recommendation=CritiqueRecommendation.APPROVE).model_dump(
            mode="json"
        ),
    )
    revision_payload = {
        "schema_version": "agent.revision_plan.v1",
        "critique_recommendation": "revise",
        "revise_targets": ["harmony"],
    }
    rev = _art("agent.revision_plan.v1", revision_payload)
    with pytest.raises(ArtifactDependencyError):
        validate_artifact_dependencies(rev, available_artifacts=[critique_approve])

    critique_revise = _art(
        "agent.critique.v1",
        AgentCritiqueV1(recommendation=CritiqueRecommendation.REVISE).model_dump(
            mode="json"
        ),
    )
    validate_artifact_dependencies(rev, available_artifacts=[critique_revise])


def test_composition_patch_requires_realize_plan():
    patch = _art(
        "agent.composition_patch.v1",
        AgentCompositionPatchV1(realize_service="passthrough").model_dump(mode="json"),
    )
    with pytest.raises(ArtifactDependencyError) as exc:
        validate_artifact_dependencies(patch, available_artifacts=[])
    assert "realize_plan" in exc.value.missing

    harmony = _art(
        "agent.harmony_plan.v1",
        AgentHarmonyPlanV1().model_dump(mode="json"),
    )
    validate_artifact_dependencies(patch, available_artifacts=[harmony])
