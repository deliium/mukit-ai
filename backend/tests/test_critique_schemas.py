"""Unit tests for CritiqueFindingV1 + extended agent.critique.v1."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.ai_agents.artifact_schemas import validate_artifact_payload
from app.ai_agents.schemas import AgentCritiqueV1, CritiqueRecommendation
from app.critique_schemas import (
    CRITIQUE_FINDING_MAX,
    CritiqueFindingEvidence,
    CritiqueFindingV1,
    CritiqueScopeDigest,
)


def test_finding_accepts_valid_and_caps_explanation():
    finding = CritiqueFindingV1(
        stratum="stylistic",
        category="contrast",
        code="climax_lacks_contrast",
        severity="info",
        explanation="x" * 600,
        suggested_action="raise density",
        evidence=CritiqueFindingEvidence(
            metrics={"prior_note_load": 0.4, "climax_note_load": 0.41},
            refs=["section_index:1"],
        ),
        affected_range={"start_bar": 9, "end_bar": 12},
    )
    assert finding.code == "climax_lacks_contrast"
    assert len(finding.explanation) == 500
    assert finding.stratum == "stylistic"


def test_subjective_cannot_be_severity_error():
    with pytest.raises(ValidationError):
        CritiqueFindingV1(
            stratum="subjective",
            category="other",
            code="model_subjective_observation",
            severity="error",
            explanation="too bland",
        )


def test_evidence_rejects_playable_fields():
    with pytest.raises(ValidationError):
        CritiqueFindingEvidence(metrics={"tracks": 1})


def test_critique_merges_reason_codes_from_findings():
    critique = AgentCritiqueV1(
        recommendation=CritiqueRecommendation.APPROVE,
        findings=[
            CritiqueFindingV1(
                stratum="stylistic",
                category="contrast",
                code="climax_lacks_contrast",
                explanation="near-identical density",
            ),
            CritiqueFindingV1(
                stratum="technical",
                category="collision",
                code="overlapping_same_pitch_timing",
                severity="warning",
                explanation="overlap",
            ),
        ],
        scope=CritiqueScopeDigest(kind="composition"),
    )
    assert critique.reason_codes == [
        "climax_lacks_contrast",
        "overlapping_same_pitch_timing",
    ]
    assert critique.stratum_counts.stylistic == 1
    assert critique.stratum_counts.technical == 1
    assert critique.engine_version.startswith("critique.engine")
    assert critique.schema_version == "agent.critique.v1"


def test_oversized_findings_list_rejected():
    findings = [
        CritiqueFindingV1(
            stratum="stylistic",
            category="other",
            code=f"code_{i}",
            explanation="x",
        )
        for i in range(CRITIQUE_FINDING_MAX + 1)
    ]
    with pytest.raises(ValidationError):
        AgentCritiqueV1(
            recommendation=CritiqueRecommendation.APPROVE,
            findings=findings,
        )


def test_validate_artifact_payload_accepts_extended_critique():
    critique = AgentCritiqueV1(
        recommendation=CritiqueRecommendation.APPROVE,
        reason_codes=["fake_ok"],
        summary="ok",
        findings=[
            CritiqueFindingV1(
                stratum="stylistic",
                category="contrast",
                code="climax_lacks_contrast",
                explanation="near identical",
            )
        ],
    )
    payload = validate_artifact_payload("agent.critique.v1", critique.model_dump(mode="json"))
    assert payload["findings"][0]["code"] == "climax_lacks_contrast"
    assert "tracks" not in payload
