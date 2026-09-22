"""Unit tests for revision loop schemas and mode → max_passes mapping."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.ai_agents.revision_loop_schemas import (
    REVISION_MODE_MAX_PASSES,
    RevisionLoopUsage,
    RevisionMode,
    RevisionPassRecordV1,
    RevisionPassValidationResult,
    RevisionStopReason,
    max_passes_for_mode,
    resolve_max_passes,
)
from app.ai_agents.artifact_schemas import AgentRevisionPlanV1
from app.ai_agents.schemas import CritiqueRecommendation


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("off", 0),
        ("fast", 1),
        ("balanced", 2),
        ("thorough", 3),
        (RevisionMode.FAST, 1),
    ],
)
def test_mode_max_passes_mapping(mode, expected):
    assert max_passes_for_mode(mode) == expected
    assert REVISION_MODE_MAX_PASSES[str(mode.value if hasattr(mode, "value") else mode)] == expected


def test_resolve_max_passes_product_clamp():
    mode, passes = resolve_max_passes(revision_mode="thorough", max_revisions=8)
    assert mode == RevisionMode.THOROUGH
    assert passes == 3


def test_resolve_max_passes_named_mode_ignores_zero_max_revisions():
    """API default max_revisions=0 must not zero out Fast/Balanced/Thorough."""
    mode, passes = resolve_max_passes(revision_mode="fast", max_revisions=0)
    assert mode == RevisionMode.FAST
    assert passes == 1
    mode, passes = resolve_max_passes(revision_mode=RevisionMode.BALANCED, max_revisions=0)
    assert mode == RevisionMode.BALANCED
    assert passes == 2


def test_resolve_max_passes_named_mode_positive_clamp():
    mode, passes = resolve_max_passes(revision_mode="thorough", max_revisions=1)
    assert mode == RevisionMode.THOROUGH
    assert passes == 1


def test_resolve_max_passes_off_escape_hatch():
    mode, passes = resolve_max_passes(revision_mode="off", max_revisions=5)
    assert mode == RevisionMode.OFF
    assert passes == 5


def test_resolve_max_passes_raw_none_mode():
    mode, passes = resolve_max_passes(revision_mode=None, max_revisions=4)
    assert mode == RevisionMode.OFF
    assert passes == 4


def test_revision_pass_record_forbids_playable():
    with pytest.raises((ValidationError, ValueError)):
        RevisionPassRecordV1.model_validate(
            {
                "pass_index": 0,
                "candidate_fingerprint": "a" * 32,
                "tracks": [],
            }
        )


def test_revision_pass_record_accepts_minimal():
    rec = RevisionPassRecordV1(
        pass_index=0,
        candidate_fingerprint="b" * 32,
        validation_result=RevisionPassValidationResult(status="ok"),
    )
    dumped = rec.model_dump(mode="json")
    assert dumped["schema_version"] == "revision_pass_record.v1"
    assert "tracks" not in dumped


def test_revision_plan_optional_targeting_backward_compatible():
    plan = AgentRevisionPlanV1(
        critique_recommendation=CritiqueRecommendation.REVISE,
        revise_targets=["harmony"],
    )
    assert plan.preserve_outside_targets is True
    assert plan.affected_ranges == []
    plan2 = AgentRevisionPlanV1(
        critique_recommendation=CritiqueRecommendation.REVISE,
        pass_index=1,
        affected_ranges=[{"start_bar": 2, "end_bar": 4}],
        affected_tracks=["t1"],
        target_agent_ids=["harmony"],
    )
    assert plan2.pass_index == 1
    assert plan2.affected_ranges[0].start_bar == 2


def test_revision_loop_usage_extra_forbid():
    with pytest.raises(ValidationError):
        RevisionLoopUsage.model_validate({"latency_ms_total": 1, "dollars": 9.99})


def test_stop_reason_catalog_complete():
    expected = {
        "critic_approve",
        "hard_requirements_satisfied",
        "improvement_below_threshold",
        "max_passes_reached",
        "resource_budget_exhausted",
        "cancelled",
        "revise_exhausted",
        "validation_failed_kept_last_valid",
    }
    assert {r.value for r in RevisionStopReason} == expected
