"""Recommendation policy for Music Evaluation Engine findings.

Subjective / stylistic observations never alone force revise. Hard-constraint
errors do. Technical warnings approve by default unless ``revise_on_technical``.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from app.ai_agents.schemas import CritiqueRecommendation
from app.critique_schemas import (
    CRITIQUE_FINDING_CODES,
    CritiqueFindingV1,
    CritiqueStratumCounts,
    count_strata,
)

logger = logging.getLogger(__name__)

# Re-export starter registry for callers / docs.
STABLE_FINDING_CODES = CRITIQUE_FINDING_CODES


def recommend_from_findings(
    findings: Sequence[CritiqueFindingV1],
    *,
    revise_on_technical: bool = False,
    evaluation_failed: bool = False,
) -> CritiqueRecommendation:
    """Pure policy: map findings (+ failure flag) → approve | revise.

    Locked rules:
    - evaluation_failed → revise
    - any hard_constraint + severity error → revise
    - technical-only → approve unless revise_on_technical
    - stylistic / subjective only → approve
    - empty + ok → approve
    """
    stratum_counts = count_strata(list(findings))
    hard_errors = sum(
        1
        for f in findings
        if f.stratum == "hard_constraint" and f.severity == "error"
    )
    technical_count = stratum_counts.technical

    if evaluation_failed:
        recommendation = CritiqueRecommendation.REVISE
    elif hard_errors > 0:
        recommendation = CritiqueRecommendation.REVISE
    elif revise_on_technical and technical_count > 0:
        recommendation = CritiqueRecommendation.REVISE
    else:
        recommendation = CritiqueRecommendation.APPROVE

    logger.info(
        "Critique recommendation policy",
        extra={
            "recommendation": recommendation.value,
            "hard_constraint": stratum_counts.hard_constraint,
            "technical": stratum_counts.technical,
            "stylistic": stratum_counts.stylistic,
            "subjective": stratum_counts.subjective,
            "revise_on_technical": revise_on_technical,
            "evaluation_failed": evaluation_failed,
        },
    )
    logger.debug(
        "Critique policy inputs",
        extra={
            "finding_count": len(findings),
            "hard_errors": hard_errors,
            "codes": [f.code for f in findings[:16]],
        },
    )
    return recommendation


def stratum_counts_for_log(findings: Sequence[CritiqueFindingV1]) -> CritiqueStratumCounts:
    return count_strata(list(findings))
