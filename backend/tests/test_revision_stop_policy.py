"""Unit tests for revision stop policy."""

from __future__ import annotations

from app.ai_agents.revision_loop_schemas import CritiqueScoreDigest, RevisionStopReason
from app.ai_agents.revision_stop_policy import (
    StopEvaluationInput,
    critique_score_digest_from_counts,
    critique_score_digest_from_payload,
    evaluate_stop_conditions,
)
from app.ai_agents.schemas import CritiqueRecommendation
from app.revision_loop_settings import RevisionLoopSettings


def _inp(**kwargs):
    base = dict(
        recommendation=CritiqueRecommendation.REVISE,
        score_digest=critique_score_digest_from_counts(hard_errors=1, technical_warnings=0),
        prior_score_digest=None,
        pass_index=0,
        max_passes=2,
        revise_count=0,
    )
    base.update(kwargs)
    return StopEvaluationInput(**base)


def test_stop_critic_approve():
    reason = evaluate_stop_conditions(
        _inp(recommendation=CritiqueRecommendation.APPROVE, score_digest=CritiqueScoreDigest(score=0))
    )
    assert reason == RevisionStopReason.CRITIC_APPROVE


def test_stop_cancelled():
    assert evaluate_stop_conditions(_inp(cancelled=True)) == RevisionStopReason.CANCELLED


def test_stop_budget():
    assert (
        evaluate_stop_conditions(_inp(budget_exhausted=True))
        == RevisionStopReason.RESOURCE_BUDGET_EXHAUSTED
    )


def test_stop_validation_failed():
    assert (
        evaluate_stop_conditions(_inp(validation_failed=True))
        == RevisionStopReason.VALIDATION_FAILED_KEPT_LAST_VALID
    )


def test_hard_ok_after_revise_pass_allows_stylistic_remaining():
    settings = RevisionLoopSettings(stop_when_hard_ok=True, revise_on_technical=False)
    digest = critique_score_digest_from_counts(
        hard_errors=0, technical_warnings=0, stylistic_count=3, subjective_count=1
    )
    reason = evaluate_stop_conditions(
        _inp(
            score_digest=digest,
            pass_index=1,
            revise_count=1,
            completed_revise_pass=True,
            prior_score_digest=critique_score_digest_from_counts(hard_errors=1, technical_warnings=0),
        ),
        settings=settings,
    )
    assert reason == RevisionStopReason.HARD_REQUIREMENTS_SATISFIED


def test_hard_ok_does_not_fire_on_pass_zero():
    settings = RevisionLoopSettings(stop_when_hard_ok=True)
    digest = critique_score_digest_from_counts(hard_errors=0, technical_warnings=0, stylistic_count=2)
    reason = evaluate_stop_conditions(
        _inp(score_digest=digest, pass_index=0, completed_revise_pass=False),
        settings=settings,
    )
    # Still revise with budget → continue
    assert reason is None


def test_improvement_below_threshold():
    settings = RevisionLoopSettings(improvement_min_delta=1, stop_when_hard_ok=False)
    # Flat score with hard already cleared (stylistic thrashing).
    prior = critique_score_digest_from_counts(hard_errors=0, technical_warnings=0, stylistic_count=2)
    reason = evaluate_stop_conditions(
        _inp(
            score_digest=prior,
            prior_score_digest=prior,
            pass_index=1,
            revise_count=1,
            completed_revise_pass=True,
        ),
        settings=settings,
    )
    assert reason == RevisionStopReason.IMPROVEMENT_BELOW_THRESHOLD


def test_improvement_does_not_stop_while_hard_remains():
    settings = RevisionLoopSettings(improvement_min_delta=1)
    prior = critique_score_digest_from_counts(hard_errors=1, technical_warnings=0)
    reason = evaluate_stop_conditions(
        _inp(
            score_digest=prior,
            prior_score_digest=prior,
            pass_index=1,
            revise_count=1,
            completed_revise_pass=True,
            max_passes=3,
        ),
        settings=settings,
    )
    assert reason is None


def test_max_passes_reached():
    reason = evaluate_stop_conditions(
        _inp(pass_index=2, max_passes=2, revise_count=2, score_digest=critique_score_digest_from_counts(hard_errors=2, technical_warnings=0))
    )
    assert reason in {
        RevisionStopReason.MAX_PASSES_REACHED,
        RevisionStopReason.REVISE_EXHAUSTED,
    }


def test_score_digest_from_payload_uses_findings():
    payload = {
        "stratum_counts": {"hard_constraint": 9, "technical": 9, "stylistic": 1, "subjective": 0},
        "findings": [
            {"stratum": "hard_constraint", "severity": "error", "code": "x"},
            {"stratum": "technical", "severity": "warning", "code": "y"},
            {"stratum": "stylistic", "severity": "info", "code": "z"},
        ],
    }
    digest = critique_score_digest_from_payload(payload)
    assert digest.hard_errors == 1
    assert digest.technical_warnings == 1
    assert digest.score == 100 + 10
