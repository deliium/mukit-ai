"""Project a 16-d preference vector from symbolic.features.v1.

The projector reads the pre-L2 feature vector. It does not L2-normalize that
vector, persist an embedding, or call composition analysis.
"""

from __future__ import annotations

import logging

from app.composition_schemas import CompositionV2
from app.embeddings.features import (
    CONCURRENT_BINS,
    CONTOUR_DIMS,
    DENSITY_DIMS,
    DUR_BINS,
    INTERVAL_BINS,
    ONSET_BINS,
    PC_DIMS,
    RANGE_DIMS,
    extract_symbolic_features_v1,
)
from app.embeddings.schemas import EmbedScopeComposition
from app.preference_schemas import PREFERENCE_FEATURE_DIMS

logger = logging.getLogger(__name__)

_OFF_RANGE = PC_DIMS
_OFF_DUR = _OFF_RANGE + RANGE_DIMS
_OFF_ONSET = _OFF_DUR + DUR_BINS
_OFF_DENSITY = _OFF_ONSET + ONSET_BINS
_OFF_INTERVAL = _OFF_DENSITY + DENSITY_DIMS
_OFF_CONTOUR = _OFF_INTERVAL + INTERVAL_BINS
_OFF_CONCURRENT = _OFF_CONTOUR + CONTOUR_DIMS
_OFF_ROLE = _OFF_CONCURRENT + CONCURRENT_BINS
_OFF_TRACK = _OFF_ROLE + 5
_OFF_FORM = _OFF_TRACK + 1


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _absolute_interval_mean(interval: list[float]) -> float:
    """Mean absolute interval in semitones, divided by 12 so the value stays in [0, 1]."""
    total = sum(interval)
    if total <= 0.0:
        return 0.0
    weighted = 0.0
    for index, mass in enumerate(interval):
        weighted += abs(index - 12) * mass
    return (weighted / total) / 12.0


def project_preference_features(composition: CompositionV2, *, candidate_id: str) -> list[float]:
    """Return the 16 preference features for one candidate composition.

    A caller that cannot extract a vector should omit that candidate. This
    function lets extraction errors propagate.
    """
    logger.debug(
        "preference features project start",
        extra={"candidate_id": candidate_id, "dims": PREFERENCE_FEATURE_DIMS},
    )
    raw, _window = extract_symbolic_features_v1(composition, EmbedScopeComposition())
    interval = [float(raw[_OFF_INTERVAL + index]) for index in range(INTERVAL_BINS)]
    vector = [
        float(raw[_OFF_RANGE]),
        float(raw[_OFF_RANGE + 1]),
        float(raw[_OFF_RANGE + 2]),
        float(raw[_OFF_DENSITY]),
        float(raw[_OFF_CONTOUR]),
        float(raw[_OFF_CONTOUR + 1]),
        float(raw[_OFF_CONTOUR + 2]),
        _mean([float(raw[_OFF_DUR + index]) for index in range(DUR_BINS)]),
        _mean([float(raw[_OFF_ONSET + index]) for index in range(ONSET_BINS)]),
        _absolute_interval_mean(interval),
        float(raw[_OFF_ROLE]),
        float(raw[_OFF_ROLE + 1]),
        float(raw[_OFF_ROLE + 2]),
        _mean([float(raw[_OFF_CONCURRENT + index]) for index in range(CONCURRENT_BINS)]),
        float(raw[_OFF_TRACK]),
        float(raw[_OFF_FORM]),
    ]
    if len(vector) != PREFERENCE_FEATURE_DIMS:
        raise ValueError("preference feature projection length")
    logger.debug(
        "preference features projected",
        extra={"candidate_id": candidate_id, "dims": PREFERENCE_FEATURE_DIMS},
    )
    return vector
