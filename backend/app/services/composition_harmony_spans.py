"""Legacy `{bar, chord}` → explicit tick-span harmony normalization.

Harmony remains metadata only — never a playable note source.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Sequence

from app.composition_schemas import compile_bar_boundaries


logger = logging.getLogger(__name__)

HARMONY_SHAPE_LEGACY = "legacy_bar_points"
HARMONY_SHAPE_CANONICAL = "canonical_spans"
HARMONY_SHAPE_EMPTY = "empty"
HARMONY_SHAPE_MIXED = "mixed"
HARMONY_SHAPE_MALFORMED = "malformed"

WARNING_DUPLICATE_BAR_COLLAPSE = "harmony_duplicate_bar_collapsed"
WARNING_LEGACY_OUT_OF_RANGE = "harmony_legacy_bar_out_of_range"
WARNING_MIXED_HARMONY_SHAPE = "harmony_mixed_shape_rejected"
WARNING_MALFORMED_HARMONY = "harmony_malformed_rejected"


class HarmonySpanNormalizationError(ValueError):
    """Raised when legacy/canonical harmony cannot be normalized safely."""

    def __init__(self, message: str, *, code: str, context: dict[str, Any] | None = None):
        self.message = message
        self.code = code
        self.context = context or {}
        super().__init__(message)


def classify_harmony_item_shape(item: Any) -> str:
    """Classify a single harmony mapping as legacy, canonical, or malformed."""
    if not isinstance(item, Mapping):
        return HARMONY_SHAPE_MALFORMED
    has_bar = "bar" in item
    has_start = "start_tick" in item
    has_duration = "duration_ticks" in item
    has_chord = "chord" in item
    if has_bar and not has_start and not has_duration and has_chord:
        return HARMONY_SHAPE_LEGACY
    if has_start and has_duration and has_chord and not has_bar:
        return HARMONY_SHAPE_CANONICAL
    if has_bar and (has_start or has_duration):
        return HARMONY_SHAPE_MIXED
    return HARMONY_SHAPE_MALFORMED


def classify_harmony_list_shape(harmony: Sequence[Any] | None) -> str:
    if harmony is None:
        return HARMONY_SHAPE_EMPTY
    if not isinstance(harmony, Sequence) or isinstance(harmony, (str, bytes)):
        return HARMONY_SHAPE_MALFORMED
    if len(harmony) == 0:
        return HARMONY_SHAPE_EMPTY
    shapes = {classify_harmony_item_shape(item) for item in harmony}
    if shapes == {HARMONY_SHAPE_LEGACY}:
        return HARMONY_SHAPE_LEGACY
    if shapes == {HARMONY_SHAPE_CANONICAL}:
        return HARMONY_SHAPE_CANONICAL
    if HARMONY_SHAPE_MIXED in shapes or (
        HARMONY_SHAPE_LEGACY in shapes and HARMONY_SHAPE_CANONICAL in shapes
    ):
        return HARMONY_SHAPE_MIXED
    return HARMONY_SHAPE_MALFORMED


def bar_at_tick(boundaries: Sequence[int], tick: int, *, duration_ticks: int, bar_count: int) -> int:
    """Map a tick to a 1-based bar using inclusive bar starts / exclusive ends."""
    if tick < 0 or tick > duration_ticks:
        raise ValueError("tick is outside composition duration")
    if tick == duration_ticks:
        return bar_count
    for bar_index in range(bar_count):
        if boundaries[bar_index] <= tick < boundaries[bar_index + 1]:
            return bar_index + 1
    return bar_count


def project_spans_to_legacy_change_points(
    spans: Sequence[Mapping[str, Any] | Any],
    *,
    boundaries: Sequence[int],
    duration_ticks: int,
    bar_count: int,
) -> list[dict[str, Any]]:
    """Project canonical spans back onto `{bar, chord}` change points for fidelity checks."""
    projected: list[dict[str, Any]] = []
    for item in spans:
        if hasattr(item, "model_dump"):
            payload = item.model_dump(mode="json")
        else:
            payload = dict(item)
        start_tick = int(payload["start_tick"])
        chord = str(payload["chord"])
        bar = bar_at_tick(
            boundaries,
            start_tick,
            duration_ticks=duration_ticks,
            bar_count=bar_count,
        )
        if boundaries[bar - 1] != start_tick:
            # Sub-bar starts have no lossless legacy projection; keep ordered tick surrogate via bar.
            logger.debug(
                "Projected sub-bar harmony span to containing bar",
                extra={"bar": bar, "start_tick": start_tick, "code": "harmony_sub_bar_projection"},
            )
        projected.append({"bar": bar, "chord": chord})
    return projected


def legacy_harmony_points_to_spans(
    harmony: Sequence[Mapping[str, Any]],
    *,
    boundaries: Sequence[int],
    duration_ticks: int,
    bar_count: int,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Convert legacy bar change-points into sorted non-overlapping half-open spans.

    Rules:
    - Stable-sort by bar then original index; last declaration wins for duplicate bars.
    - Each point starts at its bar boundary and ends at the next distinct declaration
      or ``duration_ticks``.
    - No harmony is invented before the first declaration.
    - Out-of-range bars raise ``HarmonySpanNormalizationError``.
    """
    logger.debug(
        "Dispatching legacy harmony normalization",
        extra={
            "shape": HARMONY_SHAPE_LEGACY,
            "input_count": len(harmony),
            "bar_count": bar_count,
            "duration_ticks": duration_ticks,
        },
    )

    indexed: list[tuple[int, int, str]] = []
    for index, item in enumerate(harmony):
        bar = int(item["bar"])
        chord = str(item["chord"]).strip()
        if not chord:
            raise HarmonySpanNormalizationError(
                "Harmony chord must not be empty",
                code=WARNING_MALFORMED_HARMONY,
                context={"index": index},
            )
        if bar < 1 or bar > bar_count:
            logger.warning(
                "Rejected legacy harmony bar outside composition",
                extra={
                    "code": WARNING_LEGACY_OUT_OF_RANGE,
                    "bar": bar,
                    "bar_count": bar_count,
                },
            )
            raise HarmonySpanNormalizationError(
                f"Harmony bar {bar} is outside 1..{bar_count}",
                code=WARNING_LEGACY_OUT_OF_RANGE,
                context={"bar": bar, "bar_count": bar_count},
            )
        indexed.append((bar, index, chord))

    indexed.sort(key=lambda row: (row[0], row[1]))
    collapsed: list[tuple[int, str]] = []
    duplicate_collapsed = 0
    for bar, _index, chord in indexed:
        if collapsed and collapsed[-1][0] == bar:
            duplicate_collapsed += 1
            collapsed[-1] = (bar, chord)
        else:
            collapsed.append((bar, chord))

    if duplicate_collapsed:
        logger.warning(
            "Collapsed duplicate legacy harmony bars (last declaration wins)",
            extra={
                "code": WARNING_DUPLICATE_BAR_COLLAPSE,
                "duplicate_collapsed_count": duplicate_collapsed,
                "unique_bar_count": len(collapsed),
            },
        )

    spans: list[dict[str, Any]] = []
    for position, (bar, chord) in enumerate(collapsed):
        start_tick = boundaries[bar - 1]
        if position + 1 < len(collapsed):
            next_bar = collapsed[position + 1][0]
            end_tick = boundaries[next_bar - 1]
        else:
            end_tick = duration_ticks
        duration = end_tick - start_tick
        if duration <= 0:
            raise HarmonySpanNormalizationError(
                "Legacy harmony produced a non-positive duration span",
                code=WARNING_MALFORMED_HARMONY,
                context={"bar": bar, "start_tick": start_tick, "end_tick": end_tick},
            )
        spans.append(
            {
                "start_tick": start_tick,
                "duration_ticks": duration,
                "chord": chord,
            }
        )

    stats = {
        "input_count": len(harmony),
        "duplicate_collapsed_count": duplicate_collapsed,
        "span_count": len(spans),
        "leading_gap_ticks": spans[0]["start_tick"] if spans else 0,
        "final_span_end_tick": (
            spans[-1]["start_tick"] + spans[-1]["duration_ticks"] if spans else 0
        ),
    }
    logger.info(
        "Normalized legacy harmony points to explicit spans",
        extra={
            "schema_path": "legacy_bar_points",
            "input_count": stats["input_count"],
            "span_count": stats["span_count"],
            "duplicate_collapsed_count": stats["duplicate_collapsed_count"],
        },
    )
    logger.debug(
        "Legacy harmony normalization details",
        extra={
            "leading_gap_ticks": stats["leading_gap_ticks"],
            "final_span_end_tick": stats["final_span_end_tick"],
            "bar_count": bar_count,
        },
    )
    return spans, stats


def ensure_canonical_harmony_spans(
    harmony: Sequence[Any] | None,
    *,
    time_signature: str,
    ticks_per_quarter: int,
    bar_count: int,
    duration_ticks: int,
    time_signature_changes: Sequence[Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Normalize harmony input to canonical span dicts or raise on mixed/malformed shapes."""
    shape = classify_harmony_list_shape(harmony)
    logger.debug(
        "Harmony shape dispatch",
        extra={"shape": shape, "input_count": 0 if harmony is None else len(harmony)},
    )

    if shape == HARMONY_SHAPE_EMPTY:
        return [], {"shape": shape, "span_count": 0, "input_count": 0}

    if shape == HARMONY_SHAPE_MIXED:
        logger.warning(
            "Rejected mixed legacy/canonical harmony shapes",
            extra={"code": WARNING_MIXED_HARMONY_SHAPE},
        )
        raise HarmonySpanNormalizationError(
            "Harmony must not mix {bar, chord} points with explicit tick spans",
            code=WARNING_MIXED_HARMONY_SHAPE,
        )

    if shape == HARMONY_SHAPE_MALFORMED:
        logger.warning(
            "Rejected malformed harmony items",
            extra={"code": WARNING_MALFORMED_HARMONY},
        )
        raise HarmonySpanNormalizationError(
            "Harmony items must be legacy {bar, chord} or canonical tick spans",
            code=WARNING_MALFORMED_HARMONY,
        )

    boundaries = compile_bar_boundaries(
        time_signature=time_signature,
        ticks_per_quarter=ticks_per_quarter,
        bar_count=bar_count,
        duration_ticks=duration_ticks,
        time_signature_changes=list(time_signature_changes or ()),
    )

    if shape == HARMONY_SHAPE_CANONICAL:
        spans = [
            item.model_dump(mode="json") if hasattr(item, "model_dump") else dict(item)
            for item in harmony or ()
        ]
        return spans, {
            "shape": shape,
            "span_count": len(spans),
            "input_count": len(spans),
            "duplicate_collapsed_count": 0,
        }

    spans, stats = legacy_harmony_points_to_spans(
        [dict(item) for item in harmony or ()],
        boundaries=boundaries,
        duration_ticks=duration_ticks,
        bar_count=bar_count,
    )
    stats["shape"] = shape
    return spans, stats


def harmony_change_points_by_bar(
    spans: Sequence[Any],
    *,
    boundaries: Sequence[int],
    duration_ticks: int,
    bar_count: int,
) -> dict[int, str]:
    """Map each span start (or legacy bar point) to its containing bar."""
    by_bar: dict[int, str] = {}
    for item in spans:
        if hasattr(item, "start_tick"):
            start_tick = int(item.start_tick)
            chord = str(item.chord)
            bar = bar_at_tick(
                boundaries,
                start_tick,
                duration_ticks=duration_ticks,
                bar_count=bar_count,
            )
        elif hasattr(item, "bar"):
            bar = int(item.bar)
            chord = str(item.chord)
        elif isinstance(item, Mapping) and "start_tick" in item:
            start_tick = int(item["start_tick"])
            chord = str(item["chord"])
            bar = bar_at_tick(
                boundaries,
                start_tick,
                duration_ticks=duration_ticks,
                bar_count=bar_count,
            )
        elif isinstance(item, Mapping) and "bar" in item:
            bar = int(item["bar"])
            chord = str(item["chord"])
        else:
            continue
        by_bar[bar] = chord
    return by_bar


def spans_overlapping_bar_range(
    spans: Sequence[Any],
    *,
    start_bar: int,
    end_bar_inclusive: int,
    boundaries: Sequence[int],
) -> list[Any]:
    """Return spans overlapping inclusive bars using half-open tick ranges."""
    range_start = boundaries[start_bar - 1]
    range_end = boundaries[end_bar_inclusive]
    overlapping: list[Any] = []
    for item in spans:
        if hasattr(item, "start_tick"):
            start = int(item.start_tick)
            end = start + int(item.duration_ticks)
        else:
            start = int(item["start_tick"])
            end = start + int(item["duration_ticks"])
        if start < range_end and end > range_start:
            overlapping.append(item)
    return overlapping
