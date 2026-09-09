"""Deterministic motif identity scoring and operation-specific verification gates."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal, Protocol, Sequence

from app.analysis_schemas import round_analysis_float
from app.composition_schemas import MotifRelationshipKind


logger = logging.getLogger(__name__)

MECHANICAL_OPERATIONS: frozenset[str] = frozenset(
    {
        "repeat",
        "transpose",
        "inversion",
        "augmentation",
        "diminution",
        "sequence",
    }
)
CREATIVE_OPERATIONS: frozenset[str] = frozenset(
    {
        "rhythmic_variation",
        "melodic_variation",
        "answer",
        "counterphrase",
    }
)

WARN_IDENTITY_BELOW_THRESHOLD = "motif_identity_below_threshold"
WARN_EXACT_TRANSFORM_FAILED = "motif_exact_transform_failed"


class _RelativeNoteLike(Protocol):
    relative_start_tick: int
    duration_ticks: int
    pitch_semitone_offset: int


@dataclass(frozen=True)
class MotifSimilarityComponents:
    rhythm_score: float
    contour_score: float
    interval_score: float
    alignment_score: float
    combined_score: float
    threshold: float
    exact_transform_verified: bool | None


@dataclass(frozen=True)
class MotifIdentityVerification:
    passed: bool
    components: MotifSimilarityComponents
    warning_codes: tuple[str, ...]


def identity_threshold(
    operation: MotifRelationshipKind,
    variation_strength: float | None,
) -> float:
    """Minimum combined identity score for creative operations (0..1)."""
    if operation in MECHANICAL_OPERATIONS:
        return 1.0
    strength = 0.5 if variation_strength is None else max(0.0, min(1.0, variation_strength))
    # Higher strength permits lower minimum identity while staying recognizable.
    threshold = round_analysis_float(0.95 - 0.40 * strength)
    logger.debug(
        "Computed motif identity threshold",
        extra={"operation": operation, "variation_strength": strength, "threshold": threshold},
    )
    return threshold


def _note_count_alignment(left: Sequence[_RelativeNoteLike], right: Sequence[_RelativeNoteLike]) -> float:
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    shorter = min(len(left), len(right))
    longer = max(len(left), len(right))
    return round_analysis_float(shorter / longer)


def _rhythm_score(left: Sequence[_RelativeNoteLike], right: Sequence[_RelativeNoteLike]) -> float:
    if not left or not right:
        return 0.0
    count = min(len(left), len(right))
    left_durs = [left[index].duration_ticks for index in range(count)]
    right_durs = [right[index].duration_ticks for index in range(count)]
    left_ioi = [left[index].relative_start_tick - left[index - 1].relative_start_tick for index in range(1, count)]
    right_ioi = [right[index].relative_start_tick - right[index - 1].relative_start_tick for index in range(1, count)]

    def _ratio_similarity(values_a: list[int], values_b: list[int]) -> float:
        if not values_a and not values_b:
            return 1.0
        if len(values_a) != len(values_b) or not values_a:
            return 0.0
        scores: list[float] = []
        for a, b in zip(values_a, values_b, strict=True):
            if a == 0 and b == 0:
                scores.append(1.0)
                continue
            denom = max(abs(a), abs(b), 1)
            scores.append(max(0.0, 1.0 - abs(a - b) / denom))
        return sum(scores) / len(scores)

    duration_score = _ratio_similarity(left_durs, right_durs)
    ioi_score = _ratio_similarity(left_ioi, right_ioi)
    return round_analysis_float(0.55 * duration_score + 0.45 * ioi_score)


def _intervals(notes: Sequence[_RelativeNoteLike]) -> tuple[int, ...]:
    return tuple(
        notes[index + 1].pitch_semitone_offset - notes[index].pitch_semitone_offset
        for index in range(len(notes) - 1)
    )


def _interval_score(left: Sequence[_RelativeNoteLike], right: Sequence[_RelativeNoteLike]) -> float:
    left_iv = _intervals(left)
    right_iv = _intervals(right)
    if not left_iv and not right_iv:
        return 1.0
    if len(left_iv) != len(right_iv) or not left_iv:
        return 0.0
    scores: list[float] = []
    for a, b in zip(left_iv, right_iv, strict=True):
        if a == b:
            scores.append(1.0)
            continue
        denom = max(abs(a), abs(b), 1)
        scores.append(max(0.0, 1.0 - abs(a - b) / denom))
    return round_analysis_float(sum(scores) / len(scores))


def _contour_score(left: Sequence[_RelativeNoteLike], right: Sequence[_RelativeNoteLike]) -> float:
    left_iv = _intervals(left)
    right_iv = _intervals(right)
    if not left_iv and not right_iv:
        return 1.0
    if len(left_iv) != len(right_iv) or not left_iv:
        return 0.0
    matches = sum(
        1
        for a, b in zip(left_iv, right_iv, strict=True)
        if (a > 0 and b > 0) or (a < 0 and b < 0) or (a == 0 and b == 0)
    )
    return round_analysis_float(matches / len(left_iv))


def _operation_weights(operation: MotifRelationshipKind) -> tuple[float, float, float, float]:
    if operation == "rhythmic_variation":
        return (0.50, 0.15, 0.15, 0.20)
    if operation == "melodic_variation":
        return (0.20, 0.25, 0.35, 0.20)
    if operation in {"answer", "counterphrase"}:
        return (0.25, 0.25, 0.25, 0.25)
    if operation in {"transpose", "sequence"}:
        return (0.30, 0.20, 0.30, 0.20)
    if operation in {"inversion"}:
        return (0.30, 0.20, 0.30, 0.20)
    if operation in {"augmentation", "diminution"}:
        return (0.45, 0.15, 0.20, 0.20)
    return (0.30, 0.20, 0.30, 0.20)


def compute_motif_similarity(
    source: Sequence[_RelativeNoteLike],
    target: Sequence[_RelativeNoteLike],
    *,
    operation: MotifRelationshipKind,
    variation_strength: float | None = None,
    exact_transform_verified: bool | None = None,
) -> MotifSimilarityComponents:
    """Compute weighted component scores and combined identity for a transform pair."""
    rhythm = _rhythm_score(source, target)
    contour = _contour_score(source, target)
    intervals = _interval_score(source, target)
    alignment = _note_count_alignment(source, target)
    w_rhythm, w_contour, w_interval, w_align = _operation_weights(operation)
    combined = round_analysis_float(
        w_rhythm * rhythm + w_contour * contour + w_interval * intervals + w_align * alignment
    )
    threshold = identity_threshold(operation, variation_strength)
    logger.debug(
        "Motif similarity computed",
        extra={
            "operation": operation,
            "source_note_count": len(source),
            "target_note_count": len(target),
            "rhythm_score": rhythm,
            "contour_score": contour,
            "interval_score": intervals,
            "alignment_score": alignment,
            "combined_score": combined,
            "threshold": threshold,
            "exact_transform_verified": exact_transform_verified,
        },
    )
    return MotifSimilarityComponents(
        rhythm_score=rhythm,
        contour_score=contour,
        interval_score=intervals,
        alignment_score=alignment,
        combined_score=combined,
        threshold=threshold,
        exact_transform_verified=exact_transform_verified,
    )


def verify_motif_identity(
    source: Sequence[_RelativeNoteLike],
    target: Sequence[_RelativeNoteLike],
    *,
    operation: MotifRelationshipKind,
    variation_strength: float | None = None,
    exact_transform_verified: bool | None = None,
) -> MotifIdentityVerification:
    """Apply operation-specific identity gates for creative and mechanical transforms."""
    components = compute_motif_similarity(
        source,
        target,
        operation=operation,
        variation_strength=variation_strength,
        exact_transform_verified=exact_transform_verified,
    )
    warnings: list[str] = []

    if operation in MECHANICAL_OPERATIONS:
        passed = exact_transform_verified is True
        if not passed:
            warnings.append(WARN_EXACT_TRANSFORM_FAILED)
            logger.warning(
                "Mechanical motif transform failed exact verification",
                extra={"operation": operation, "code": WARN_EXACT_TRANSFORM_FAILED},
            )
    else:
        passed = components.combined_score >= components.threshold

    if operation not in MECHANICAL_OPERATIONS and not passed:
        warnings.append(WARN_IDENTITY_BELOW_THRESHOLD)
        logger.warning(
            "Creative motif identity below threshold",
            extra={
                "operation": operation,
                "code": WARN_IDENTITY_BELOW_THRESHOLD,
                "combined_score": components.combined_score,
                "threshold": components.threshold,
            },
        )

    logger.debug(
        "Motif identity verification finished",
        extra={
            "operation": operation,
            "passed": passed,
            "warning_count": len(warnings),
        },
    )
    return MotifIdentityVerification(passed=passed, components=components, warning_codes=tuple(warnings))


def verify_exact_repeat(
    source: Sequence[_RelativeNoteLike],
    target: Sequence[_RelativeNoteLike],
) -> bool:
    if len(source) != len(target):
        return False
    for left, right in zip(source, target, strict=True):
        if (
            left.relative_start_tick != right.relative_start_tick
            or left.duration_ticks != right.duration_ticks
            or left.pitch_semitone_offset != right.pitch_semitone_offset
        ):
            return False
    return True


def verify_exact_transpose(
    source: Sequence[_RelativeNoteLike],
    target: Sequence[_RelativeNoteLike],
    *,
    semitones: int,
) -> bool:
    if len(source) != len(target):
        return False
    for left, right in zip(source, target, strict=True):
        if (
            left.relative_start_tick != right.relative_start_tick
            or left.duration_ticks != right.duration_ticks
            or right.pitch_semitone_offset != left.pitch_semitone_offset + semitones
        ):
            return False
    return True


def verify_exact_inversion(
    source: Sequence[_RelativeNoteLike],
    target: Sequence[_RelativeNoteLike],
    *,
    axis_semitone_offset: int,
) -> bool:
    if len(source) != len(target):
        return False
    for left, right in zip(source, target, strict=True):
        expected = axis_semitone_offset - (left.pitch_semitone_offset - axis_semitone_offset)
        if (
            left.relative_start_tick != right.relative_start_tick
            or left.duration_ticks != right.duration_ticks
            or right.pitch_semitone_offset != expected
        ):
            return False
    return True


def verify_exact_time_scale(
    source: Sequence[_RelativeNoteLike],
    target: Sequence[_RelativeNoteLike],
    *,
    numerator: int,
    denominator: int,
) -> bool:
    if len(source) != len(target) or not source:
        return False

    def _scale(value: int) -> int:
        return int(round(value * numerator / denominator))

    if target[0].relative_start_tick != 0:
        return False
    if source[0].relative_start_tick != 0:
        return False
    for index, (left, right) in enumerate(zip(source, target, strict=True)):
        if right.duration_ticks != _scale(left.duration_ticks):
            return False
        if index == 0:
            if right.relative_start_tick != 0:
                return False
            continue
        expected_start = _scale(left.relative_start_tick)
        if right.relative_start_tick != expected_start:
            return False
        if right.pitch_semitone_offset != left.pitch_semitone_offset:
            return False
    return True


def verify_exact_sequence_step(
    source: Sequence[_RelativeNoteLike],
    step_source: Sequence[_RelativeNoteLike],
    *,
    interval_semitones: int,
    step_ticks: int,
) -> bool:
    if len(source) != len(step_source):
        return False
    for left, right in zip(source, step_source, strict=True):
        if (
            right.relative_start_tick != left.relative_start_tick + step_ticks
            or right.duration_ticks != left.duration_ticks
            or right.pitch_semitone_offset != left.pitch_semitone_offset + interval_semitones
        ):
            return False
    return True


# --- Repetition-analysis token scoring (integer rhythm/interval tokens) --------

_REPETITION_WEIGHT_RHYTHM = 0.30
_REPETITION_WEIGHT_CONTOUR = 0.25
_REPETITION_WEIGHT_INTERVALS = 0.30
_REPETITION_WEIGHT_NOTE_COUNT = 0.15


def quantize_ioi(delta: int, ticks_per_quarter: int) -> int:
    """Map IOI to nearest sixteenth of a quarter (stable rhythm token)."""
    step = max(1, ticks_per_quarter // 4)
    return int(round(delta / float(step)))


def quantize_duration(duration: int, ticks_per_quarter: int) -> int:
    """Map duration to nearest sixteenth of a quarter."""
    return quantize_ioi(duration, ticks_per_quarter)


def _token_direction(interval: int) -> int:
    if interval > 0:
        return 1
    if interval < 0:
        return -1
    return 0


def _rhythm_token_match(left: Sequence[int], right: Sequence[int]) -> float:
    if not left and not right:
        return 1.0
    if len(left) != len(right) or not left:
        return 0.0
    matches = sum(1 for a, b in zip(left, right, strict=True) if a == b)
    return matches / len(left)


def _contour_token_match(left: Sequence[int], right: Sequence[int]) -> float:
    if len(left) != len(right) or not left:
        return 0.0 if left or right else 1.0
    left_dirs = [_token_direction(a) for a in left]
    right_dirs = [_token_direction(b) for b in right]
    matches = sum(1 for a, b in zip(left_dirs, right_dirs, strict=True) if a == b)
    return matches / len(left_dirs)


def _interval_token_match(left: Sequence[int], right: Sequence[int]) -> float:
    if len(left) != len(right) or not left:
        return 0.0 if left or right else 1.0
    if left == right:
        return 1.0
    matches = sum(1 for a, b in zip(left, right, strict=True) if abs(a) == abs(b))
    return matches / len(left)


def _note_count_token_alignment(left_count: int, right_count: int) -> float:
    if left_count <= 0 or right_count <= 0:
        return 0.0
    if left_count == right_count:
        return 1.0
    return min(left_count, right_count) / max(left_count, right_count)


def compute_identity_score(
    *,
    left_rhythm: Sequence[int],
    right_rhythm: Sequence[int],
    left_intervals: Sequence[int],
    right_intervals: Sequence[int],
    left_durations: Sequence[int],
    right_durations: Sequence[int],
    left_count: int,
    right_count: int,
) -> float:
    """Weighted rhythm + contour + intervals + note-count alignment for repetition."""
    rhythm = 0.5 * _rhythm_token_match(left_rhythm, right_rhythm) + 0.5 * _rhythm_token_match(
        left_durations, right_durations
    )
    contour = _contour_token_match(left_intervals, right_intervals)
    intervals = _interval_token_match(left_intervals, right_intervals)
    count = _note_count_token_alignment(left_count, right_count)
    score = (
        _REPETITION_WEIGHT_RHYTHM * rhythm
        + _REPETITION_WEIGHT_CONTOUR * contour
        + _REPETITION_WEIGHT_INTERVALS * intervals
        + _REPETITION_WEIGHT_NOTE_COUNT * count
    )
    return round_analysis_float(min(1.0, max(0.0, score)))


__all__ = [
    "CREATIVE_OPERATIONS",
    "MECHANICAL_OPERATIONS",
    "MotifIdentityVerification",
    "MotifSimilarityComponents",
    "WARN_EXACT_TRANSFORM_FAILED",
    "WARN_IDENTITY_BELOW_THRESHOLD",
    "compute_identity_score",
    "compute_motif_similarity",
    "identity_threshold",
    "quantize_duration",
    "quantize_ioi",
    "verify_exact_inversion",
    "verify_exact_repeat",
    "verify_exact_sequence_step",
    "verify_exact_time_scale",
    "verify_exact_transpose",
    "verify_motif_identity",
]
