"""Tests for RevisionPlan builder from critique findings."""

from __future__ import annotations

from app.ai_agents.revision_plan_builder import build_revision_plan_from_findings
from app.ai_agents.schemas import CritiqueRecommendation
from app.critique_schemas import CritiqueAffectedRange, CritiqueFindingV1


def test_builder_maps_harmony_and_ranges():
    findings = [
        CritiqueFindingV1(
            stratum="hard_constraint",
            category="harmony",
            code="requested_key_mismatch",
            severity="error",
            affected_range=CritiqueAffectedRange(start_bar=2, end_bar=4),
            affected_tracks=["bass-1"],
        ),
        CritiqueFindingV1(
            stratum="subjective",
            category="other",
            code="model_subjective_observation",
            severity="info",
            affected_range=CritiqueAffectedRange(start_bar=1, end_bar=16),
        ),
    ]
    plan = build_revision_plan_from_findings(findings, pass_index=1)
    assert plan.pass_index == 1
    assert "harmony" in plan.target_agent_ids
    assert plan.affected_tracks == ["bass-1"]
    assert plan.affected_ranges[0].start_bar == 2
    assert "model_subjective_observation" not in plan.revise_targets


def test_builder_climax_maps_melody_and_arrangement():
    findings = [
        CritiqueFindingV1(
            stratum="hard_constraint",
            category="contrast",
            code="climax_lacks_contrast",
            severity="error",
            affected_range=CritiqueAffectedRange(start_bar=5, end_bar=8),
            affected_tracks=["piano-1"],
        )
    ]
    plan = build_revision_plan_from_findings(findings, pass_index=2)
    assert "melody_motif" in plan.target_agent_ids or "arrangement" in plan.target_agent_ids
    assert plan.preserve_outside_targets is True


def test_builder_ignores_technical_unless_policy():
    findings = [
        CritiqueFindingV1(
            stratum="technical",
            category="density",
            code="dense_overlapping_material",
            severity="warning",
            affected_range=CritiqueAffectedRange(start_bar=1, end_bar=2),
        )
    ]
    plan = build_revision_plan_from_findings(
        findings, pass_index=1, revise_on_technical=False
    )
    # Falls back to all findings when Critic says revise and no hard drivers.
    assert plan.critique_recommendation == CritiqueRecommendation.REVISE
    plan2 = build_revision_plan_from_findings(
        findings, pass_index=1, revise_on_technical=True
    )
    assert "arrangement" in plan2.target_agent_ids
