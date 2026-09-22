"""Accept/reject fixtures for typed agent artifact content schemas."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.ai_agents.artifact_schemas import (
    AGENT_ARRANGEMENT_PLAN_SCHEMA,
    AGENT_COMPOSITION_PATCH_SCHEMA,
    AGENT_FORM_PLAN_SCHEMA,
    AGENT_HARMONY_PLAN_SCHEMA,
    AGENT_MOTIF_PLAN_SCHEMA,
    AGENT_ORCHESTRATION_PLAN_SCHEMA,
    AGENT_PERFORMANCE_PLAN_SCHEMA,
    AGENT_PRODUCTION_PLAN_SCHEMA,
    AGENT_RENDER_PLAN_SCHEMA,
    AGENT_REVISION_PLAN_SCHEMA,
    AgentArrangementPlanV1,
    AgentCompositionPatchV1,
    AgentFormPlanV1,
    AgentHarmonyPlanV1,
    AgentMotifPlanV1,
    AgentOrchestrationPlanV1,
    AgentPerformancePlanV1,
    AgentProductionPlanV1,
    AgentRenderPlanV1,
    AgentRevisionPlanV1,
    FormPlanSection,
    FORBIDDEN_PLAYABLE_TOP_LEVEL,
    MusicAnalysisBoundedProjectionV1,
    project_music_analysis_bounded,
    typed_schema_load_count,
    validate_artifact_payload,
)
from app.ai_agents.schemas import CritiqueRecommendation


def test_typed_schema_load_count_positive():
    assert typed_schema_load_count() >= 10


@pytest.mark.parametrize(
    "builder",
    [
        lambda: AgentFormPlanV1(
            sections=[FormPlanSection(label="A", start_bar=1, bar_count=4)]
        ),
        lambda: AgentHarmonyPlanV1(
            key="C major", chord_events=[{"bar": 1, "chord": "C"}]
        ),
        lambda: AgentMotifPlanV1(motifs=[{"motif_label": "theme_a", "role": "melody"}]),
        lambda: AgentArrangementPlanV1(
            track_hints=[{"role": "melody", "instrument_family": "piano"}]
        ),
        lambda: AgentOrchestrationPlanV1(instruments=[{"family": "strings"}]),
        lambda: AgentPerformancePlanV1(velocity_curve="crescendo"),
        lambda: AgentProductionPlanV1(mix_notes="dry"),
        lambda: AgentRevisionPlanV1(
            critique_recommendation=CritiqueRecommendation.REVISE,
            revise_targets=["harmony"],
        ),
        lambda: AgentCompositionPatchV1(
            realize_service="reharmonize_candidate", op_refs=["nudge_tempo"]
        ),
        lambda: AgentRenderPlanV1(job_ref=None, comment="stub"),
    ],
)
def test_typed_plans_accept(builder):
    model = builder()
    dumped = model.model_dump(mode="json")
    assert "schema_version" in dumped
    validated = validate_artifact_payload(dumped["schema_version"], dumped)
    assert validated["schema_version"] == dumped["schema_version"]


@pytest.mark.parametrize("field", sorted(FORBIDDEN_PLAYABLE_TOP_LEVEL))
def test_typed_plans_reject_playable_fields(field):
    with pytest.raises((ValidationError, ValueError)):
        AgentHarmonyPlanV1.model_validate({"key": "C major", field: []})


def test_composition_patch_rejects_embedded_score():
    with pytest.raises((ValidationError, ValueError)):
        AgentCompositionPatchV1.model_validate(
            {
                "realize_service": "passthrough",
                "candidate_composition": {
                    "tracks": [{"events": [{"pitch": "C4"}]}],
                },
            }
        )


def test_music_analysis_bounded_projection():
    full = {
        "schema_version": "composition.analysis.v1",
        "source_fingerprint": "a" * 32,
        "algorithm_version": "test",
        "status": "ok",
        "warnings": [{"code": "sparse_density"}, {"code": "sparse_density"}],
        "resolved_scope": {"kind": "composition", "track_count": 2},
        "section_summaries": [{"label": "A"}, {"label": "B"}],
        "tonality": {"key": "C major"},
    }
    projected = project_music_analysis_bounded(full)
    model = MusicAnalysisBoundedProjectionV1.model_validate(projected)
    assert model.warning_count == 1
    assert "sparse_density" in model.warning_codes
    assert model.section_count == 2
    assert "tracks" not in projected
    assert "tonality" not in projected


def test_validate_oversized_analysis_rejected_for_durable():
    huge = {
        "schema_version": "composition.analysis.v1",
        "source_fingerprint": "b" * 32,
        "warnings": [],
        "resolved_scope": {},
        "blob": "x" * 20_000,
    }
    with pytest.raises(ValueError, match="durable byte cap"):
        validate_artifact_payload("composition.analysis.v1", huge)


def test_content_type_constants_stable():
    assert AGENT_FORM_PLAN_SCHEMA == "agent.form_plan.v1"
    assert AGENT_HARMONY_PLAN_SCHEMA == "agent.harmony_plan.v1"
    assert AGENT_MOTIF_PLAN_SCHEMA == "agent.motif_plan.v1"
    assert AGENT_ARRANGEMENT_PLAN_SCHEMA == "agent.arrangement_plan.v1"
    assert AGENT_ORCHESTRATION_PLAN_SCHEMA == "agent.orchestration_plan.v1"
    assert AGENT_PERFORMANCE_PLAN_SCHEMA == "agent.performance_plan.v1"
    assert AGENT_PRODUCTION_PLAN_SCHEMA == "agent.production_plan.v1"
    assert AGENT_REVISION_PLAN_SCHEMA == "agent.revision_plan.v1"
    assert AGENT_COMPOSITION_PATCH_SCHEMA == "agent.composition_patch.v1"
    assert AGENT_RENDER_PLAN_SCHEMA == "agent.render_plan.v1"
