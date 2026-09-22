"""Tests for optional LLM critique adapter."""

from __future__ import annotations

from app.critique_schemas import CritiqueFindingV1
from app.services.composition_critique_scope import resolve_critique_scope
from app.services.llm_composition_critique import (
    filter_subjective_findings,
    run_model_critique,
)
from app.composition_schemas import CompositionV2
from tests.test_composition_v2_schema import minimal_v2


def test_fake_model_critique_with_locus():
    composition = CompositionV2.model_validate(minimal_v2())
    resolved = resolve_critique_scope(composition, None)
    findings, status = run_model_critique(
        analysis_report=None,
        resolved=resolved,
        include_model_critique=True,
        env={"LLM_FAKE_MODE": "1"},
    )
    assert status == "ok"
    assert len(findings) == 1
    assert findings[0].stratum == "subjective"
    assert findings[0].affected_range is not None


def test_model_critique_skipped_by_default():
    composition = CompositionV2.model_validate(minimal_v2())
    resolved = resolve_critique_scope(composition, None)
    findings, status = run_model_critique(
        analysis_report=None,
        resolved=resolved,
        include_model_critique=False,
        env={"LLM_FAKE_MODE": "1"},
    )
    assert status == "skipped"
    assert findings == []


def test_filter_rejects_no_locus():
    accepted, rejected = filter_subjective_findings(
        [
            {
                "stratum": "subjective",
                "category": "other",
                "code": "model_subjective_observation",
                "severity": "info",
                "explanation": "needs more emotion",
            },
            CritiqueFindingV1(
                stratum="subjective",
                category="other",
                code="model_subjective_observation",
                severity="info",
                explanation="ok",
                affected_tracks=["piano-1"],
            ),
        ]
    )
    assert rejected == 1
    assert len(accepted) == 1
