"""Pure stop-policy evaluation for the critique revision loop."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.ai_agents.revision_loop_schemas import (
    CritiqueScoreDigest,
    RevisionStopReason,
    UsageStatus,
)
from app.ai_agents.schemas import CritiqueRecommendation
from app.revision_loop_settings import RevisionLoopSettings, load_revision_loop_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StopEvaluationInput:
    """Inputs for ``evaluate_stop_conditions`` — scalars / digests only."""

    recommendation: CritiqueRecommendation | str | None
    score_digest: CritiqueScoreDigest | None
    prior_score_digest: CritiqueScoreDigest | None
    pass_index: int
    max_passes: int
    revise_count: int
    cancelled: bool = False
    budget_exhausted: bool = False
    validation_failed: bool = False
    completed_revise_pass: bool = False


def critique_score_digest_from_counts(
    *,
    hard_errors: int,
    technical_warnings: int,
    stylistic_count: int = 0,
    subjective_count: int = 0,
    settings: RevisionLoopSettings | None = None,
) -> CritiqueScoreDigest:
    cfg = settings or load_revision_loop_settings()
    hard = max(0, int(hard_errors))
    tech = max(0, int(technical_warnings))
    score = hard * cfg.hard_weight + tech * cfg.technical_weight
    return CritiqueScoreDigest(
        score=score,
        hard_errors=hard,
        technical_warnings=tech,
        stylistic_count=max(0, int(stylistic_count)),
        subjective_count=max(0, int(subjective_count)),
    )


def critique_score_digest_from_payload(
    critique_payload: dict[str, Any] | None,
    *,
    settings: RevisionLoopSettings | None = None,
) -> CritiqueScoreDigest:
    """Build digest from critique payload / stratum_counts (no full findings)."""
    cfg = settings or load_revision_loop_settings()
    if not isinstance(critique_payload, dict):
        return CritiqueScoreDigest(score=0)
    counts = critique_payload.get("stratum_counts")
    if not isinstance(counts, dict):
        counts = {}
    hard = int(counts.get("hard_constraint") or 0)
    tech = int(counts.get("technical") or 0)
    stylistic = int(counts.get("stylistic") or 0)
    subjective = int(counts.get("subjective") or 0)
    # Prefer severity=error hard findings when findings list present.
    findings = critique_payload.get("findings")
    if isinstance(findings, list):
        hard_errors = 0
        tech_warnings = 0
        for item in findings:
            if not isinstance(item, dict):
                continue
            stratum = str(item.get("stratum") or "")
            severity = str(item.get("severity") or "")
            if stratum == "hard_constraint" and severity == "error":
                hard_errors += 1
            elif stratum == "technical" and severity in {"warning", "error"}:
                tech_warnings += 1
        if hard_errors or tech_warnings:
            hard = hard_errors
            tech = tech_warnings
    return critique_score_digest_from_counts(
        hard_errors=hard,
        technical_warnings=tech,
        stylistic_count=stylistic,
        subjective_count=subjective,
        settings=cfg,
    )


def evaluate_stop_conditions(
    inp: StopEvaluationInput,
    *,
    settings: RevisionLoopSettings | None = None,
) -> RevisionStopReason | None:
    """Return a stop reason, or ``None`` to continue revising.

    Priority (first match wins):
    1. cancelled
    2. validation_failed → keep last valid
    3. resource budget
    4. critic approve
    5. hard requirements satisfied (when configured; stylistic-only remaining OK)
    6. improvement below threshold (after a completed revise pass)
    7. max passes / revise exhausted
    """
    cfg = settings or load_revision_loop_settings()
    rec = inp.recommendation
    if isinstance(rec, CritiqueRecommendation):
        rec_value = rec
    elif rec is None:
        rec_value = None
    else:
        try:
            rec_value = CritiqueRecommendation(str(rec).strip().lower())
        except ValueError:
            rec_value = None

    if inp.cancelled:
        reason = RevisionStopReason.CANCELLED
        _log_stop(reason, inp)
        return reason
    if inp.validation_failed:
        reason = RevisionStopReason.VALIDATION_FAILED_KEPT_LAST_VALID
        _log_stop(reason, inp)
        return reason
    if inp.budget_exhausted:
        reason = RevisionStopReason.RESOURCE_BUDGET_EXHAUSTED
        _log_stop(reason, inp)
        return reason
    if rec_value == CritiqueRecommendation.APPROVE:
        reason = RevisionStopReason.CRITIC_APPROVE
        _log_stop(reason, inp)
        return reason

    digest = inp.score_digest
    # hard_ok: Critic may still recommend revise for stylistic/subjective leftovers,
    # but product policy can stop once hard (+ technical when revise_on_technical) clear.
    if (
        cfg.stop_when_hard_ok
        and digest is not None
        and digest.hard_errors == 0
        and rec_value == CritiqueRecommendation.REVISE
        and (digest.technical_warnings == 0 or not cfg.revise_on_technical)
        # Only apply after at least one completed revise pass (pass_index >= 1),
        # so initial stylistic-driven test hooks can still schedule revises.
        and inp.pass_index >= 1
        and inp.completed_revise_pass
    ):
        reason = RevisionStopReason.HARD_REQUIREMENTS_SATISFIED
        _log_stop(reason, inp)
        return reason

    if (
        inp.completed_revise_pass
        and inp.prior_score_digest is not None
        and digest is not None
        # Do not stop for flat improvement while hard (or revise-driving technical) remain.
        and digest.hard_errors == 0
        and (digest.technical_warnings == 0 or not cfg.revise_on_technical)
    ):
        delta = inp.prior_score_digest.score - digest.score
        if delta < cfg.improvement_min_delta:
            reason = RevisionStopReason.IMPROVEMENT_BELOW_THRESHOLD
            _log_stop(reason, inp, score_before=inp.prior_score_digest.score, score_after=digest.score)
            return reason

    if rec_value == CritiqueRecommendation.REVISE:
        # No remaining revise budget for another pass.
        if inp.revise_count >= inp.max_passes:
            # Distinguish: completed allowed passes without approve vs exhausted mid-revise.
            if inp.pass_index >= inp.max_passes:
                reason = RevisionStopReason.MAX_PASSES_REACHED
            else:
                reason = RevisionStopReason.REVISE_EXHAUSTED
            _log_stop(reason, inp)
            return reason
        return None

    # Unknown / missing recommendation — stop safely.
    if inp.max_passes <= 0 or inp.revise_count >= inp.max_passes:
        reason = RevisionStopReason.MAX_PASSES_REACHED
        _log_stop(reason, inp)
        return reason
    return None


def _log_stop(
    reason: RevisionStopReason,
    inp: StopEvaluationInput,
    *,
    score_before: int | None = None,
    score_after: int | None = None,
) -> None:
    extra: dict[str, Any] = {
        "stop_reason": reason.value,
        "pass_index": inp.pass_index,
        "max_passes": inp.max_passes,
        "revise_count": inp.revise_count,
    }
    if score_before is not None:
        extra["score_before"] = score_before
    if score_after is not None:
        extra["score_after"] = score_after
    elif inp.score_digest is not None:
        extra["score_after"] = inp.score_digest.score
    logger.info("Revision loop stop condition", extra=extra)


def merge_usage_status(current: UsageStatus, incoming: UsageStatus) -> UsageStatus:
    if current == UsageStatus.UNAVAILABLE and incoming == UsageStatus.UNAVAILABLE:
        return UsageStatus.UNAVAILABLE
    if current == UsageStatus.AVAILABLE and incoming == UsageStatus.AVAILABLE:
        return UsageStatus.AVAILABLE
    if incoming == UsageStatus.UNAVAILABLE and current == UsageStatus.AVAILABLE:
        return UsageStatus.PARTIAL
    if incoming == UsageStatus.AVAILABLE and current == UsageStatus.UNAVAILABLE:
        return UsageStatus.PARTIAL
    return UsageStatus.PARTIAL
