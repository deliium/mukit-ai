"""Tests for critique recommendation policy."""

from __future__ import annotations

from app.ai_agents.schemas import CritiqueRecommendation
from app.critique_schemas import CritiqueFindingV1
from app.services.composition_critique_policy import recommend_from_findings


def _finding(**kwargs) -> CritiqueFindingV1:
    defaults = {
        "stratum": "stylistic",
        "category": "other",
        "code": "section_lacks_contrast",
        "severity": "info",
        "explanation": "obs",
    }
    defaults.update(kwargs)
    return CritiqueFindingV1(**defaults)


def test_hard_error_forces_revise():
    rec = recommend_from_findings(
        [
            _finding(
                stratum="hard_constraint",
                category="structure",
                code="requested_structure_mismatch",
                severity="error",
            ),
            _finding(stratum="stylistic", code="climax_lacks_contrast"),
        ]
    )
    assert rec == CritiqueRecommendation.REVISE


def test_stylistic_only_approves():
    rec = recommend_from_findings([_finding(code="climax_lacks_contrast")])
    assert rec == CritiqueRecommendation.APPROVE


def test_subjective_only_approves():
    rec = recommend_from_findings(
        [
            _finding(
                stratum="subjective",
                code="model_subjective_observation",
                severity="warning",
            )
        ]
    )
    assert rec == CritiqueRecommendation.APPROVE


def test_technical_default_approve_unless_flag():
    technical = [
        _finding(
            stratum="technical",
            category="collision",
            code="overlapping_same_pitch_timing",
            severity="warning",
        )
    ]
    assert recommend_from_findings(technical) == CritiqueRecommendation.APPROVE
    assert (
        recommend_from_findings(technical, revise_on_technical=True)
        == CritiqueRecommendation.REVISE
    )


def test_evaluation_failed_revises():
    assert (
        recommend_from_findings([], evaluation_failed=True)
        == CritiqueRecommendation.REVISE
    )


def test_empty_findings_approve():
    assert recommend_from_findings([]) == CritiqueRecommendation.APPROVE
