"""Linear pairwise ranker for an explicit candidate ballot.

``CandidateRanker`` is the protocol. ``LinearPairwiseRanker`` is the only
implementation. This module imports schemas and the standard library. It does
not import SQLite, FastAPI, embeddings, or stores.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Protocol, Sequence

from app.preference_schemas import (
    PREFERENCE_FEATURE_DIMS,
    PREFERENCE_LEARNING_RATE,
    PREFERENCE_WEIGHT_LIMIT,
    PreferenceCandidateFeaturesV1,
    PreferenceChoiceV1,
    PreferenceRankerV1,
    PreferenceRankingV1,
)

logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    """Server timestamp for a ranker document. The pure update does not log it."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def log_preference_ranker_updated(*, pair_count: int, surface: str) -> None:
    """DEBUG the updated pair count. Callers pass the surface. Weights stay unlogged."""
    logger.debug(
        "preference ranker updated",
        extra={"pair_count": pair_count, "surface": surface},
    )


def _sigmoid(value: float) -> float:
    if value >= 0.0:
        return 1.0 / (1.0 + math.exp(-value))
    z = math.exp(value)
    return z / (1.0 + z)


def _clip(value: float) -> float:
    return max(-PREFERENCE_WEIGHT_LIMIT, min(PREFERENCE_WEIGHT_LIMIT, value))


def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    return sum(left[index] * right[index] for index in range(PREFERENCE_FEATURE_DIMS))


class CandidateRanker(Protocol):
    """Order a ballot and update weights from one explicit choice."""

    def rank(
        self,
        items: Sequence[PreferenceCandidateFeaturesV1],
        model: PreferenceRankerV1 | None,
    ) -> PreferenceRankingV1:
        """Return scores in preference order. Ties keep the earlier preview index."""

    def update(self, model: PreferenceRankerV1, choice: PreferenceChoiceV1) -> PreferenceRankerV1:
        """Return a new weight vector. The pure update does not log weights."""


class LinearPairwiseRanker:
    """One update per non-chosen sibling: ``w ← clip(w + lr * (1 - sigmoid(w·delta)) * delta)``."""

    def rank(
        self,
        items: Sequence[PreferenceCandidateFeaturesV1],
        model: PreferenceRankerV1 | None,
    ) -> PreferenceRankingV1:
        ordered = list(items)
        logger.debug(
            "preference ranker rank start",
            extra={
                "candidate_count": len(ordered),
                "pair_count": 0 if model is None else model.pair_count,
            },
        )
        if not ordered:
            raise ValueError("preference rank requires at least one candidate")
        if model is None or model.pair_count == 0:
            logger.debug(
                "preference ranker cold start",
                extra={"candidate_count": len(ordered), "ranking_applied": False},
            )
            return PreferenceRankingV1(
                ranking_applied=False,
                ordered_candidate_ids=[item.candidate_id for item in ordered],
                scores=[0.0] * len(ordered),
            )
        scored = [
            (_dot(model.weights, item.feature_vector), item)
            for item in ordered
        ]
        scored.sort(key=lambda pair: (-pair[0], pair[1].original_index))
        ranking = PreferenceRankingV1(
            ranking_applied=True,
            ordered_candidate_ids=[item.candidate_id for _score, item in scored],
            scores=[score for score, _item in scored],
        )
        logger.debug(
            "preference ranker rank complete",
            extra={"candidate_count": len(ordered), "ranking_applied": True},
        )
        return ranking

    def update(self, model: PreferenceRankerV1, choice: PreferenceChoiceV1) -> PreferenceRankerV1:
        chosen = next(item for item in choice.candidates if item.chosen)
        weights = [float(value) for value in model.weights]
        pairs = 0
        for other in choice.candidates:
            if other.chosen:
                continue
            delta = [
                chosen.feature_vector[index] - other.feature_vector[index]
                for index in range(PREFERENCE_FEATURE_DIMS)
            ]
            probability = _sigmoid(_dot(weights, delta))
            step = PREFERENCE_LEARNING_RATE * (1.0 - probability)
            weights = [_clip(weights[index] + step * delta[index]) for index in range(PREFERENCE_FEATURE_DIMS)]
            pairs += 1
        updated = PreferenceRankerV1(
            weights=weights,
            learning_rate=PREFERENCE_LEARNING_RATE,
            pair_count=model.pair_count + pairs,
            updated_at=utc_now_iso(),
        )
        return updated


def zero_ranker(*, updated_at: str | None = None) -> PreferenceRankerV1:
    """Weights start at zero. ``pair_count`` zero means a later rank keeps input order."""
    return PreferenceRankerV1(
        weights=[0.0] * PREFERENCE_FEATURE_DIMS,
        learning_rate=PREFERENCE_LEARNING_RATE,
        pair_count=0,
        updated_at=updated_at or utc_now_iso(),
    )
