"""Immutable harmony timeline range operations for composition.v2.

Harmony spans are metadata only — these operations never mutate note events.
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

from app.composition_schemas import CompositionV2, CompositionV2HarmonyItem, compile_bar_boundaries
from app.harmony_schemas import (
    HARMONY_TIMELINE_ERROR_CODES,
    HarmonyAddOperation,
    HarmonyMoveOperation,
    HarmonyRemoveOperation,
    HarmonyReplaceOperation,
    HarmonyResizeOperation,
    HarmonySpanInput,
    HarmonyTimelineEditResponse,
    HarmonyTimelineError,
    HarmonyTimelineOperation,
)
from app.services.composition_harmony_spans import bar_at_tick
from app.services.composition_timeline import compile_timeline


logger = logging.getLogger(__name__)


def _span_end(item: CompositionV2HarmonyItem | HarmonySpanInput | dict[str, Any]) -> int:
    if isinstance(item, dict):
        return int(item["start_tick"]) + int(item["duration_ticks"])
    return int(item.start_tick) + int(item.duration_ticks)


def _as_dict(item: CompositionV2HarmonyItem | HarmonySpanInput | dict[str, Any]) -> dict[str, Any]:
    if isinstance(item, dict):
        return {
            "start_tick": int(item["start_tick"]),
            "duration_ticks": int(item["duration_ticks"]),
            "chord": str(item["chord"]),
        }
    return {
        "start_tick": int(item.start_tick),
        "duration_ticks": int(item.duration_ticks),
        "chord": str(item.chord),
    }


def merge_adjacent_identical_spans(
    spans: Sequence[CompositionV2HarmonyItem | HarmonySpanInput | dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge adjacent spans that share identical chord spelling (not enharmonic equivalents)."""
    ordered = sorted((_as_dict(item) for item in spans), key=lambda item: item["start_tick"])
    if not ordered:
        return []
    merged: list[dict[str, Any]] = [dict(ordered[0])]
    for item in ordered[1:]:
        previous = merged[-1]
        previous_end = previous["start_tick"] + previous["duration_ticks"]
        if previous_end == item["start_tick"] and previous["chord"] == item["chord"]:
            previous["duration_ticks"] += item["duration_ticks"]
        else:
            merged.append(dict(item))
    return merged


def inferred_bar_label_for_span(
    composition: CompositionV2,
    span: CompositionV2HarmonyItem | dict[str, Any],
) -> int:
    """Derive a display bar label from the span start tick via the meter map."""
    timeline = compile_timeline(composition)
    start_tick = int(span.start_tick if hasattr(span, "start_tick") else span["start_tick"])
    return bar_at_tick(
        timeline.bar_boundaries,
        start_tick,
        duration_ticks=timeline.duration_ticks,
        bar_count=timeline.bar_count,
    )


def spans_overlapping_range(
    spans: Sequence[CompositionV2HarmonyItem | dict[str, Any]],
    *,
    start_tick: int,
    end_tick: int,
) -> list[CompositionV2HarmonyItem | dict[str, Any]]:
    overlapping: list[CompositionV2HarmonyItem | dict[str, Any]] = []
    for item in spans:
        item_start = int(item.start_tick if hasattr(item, "start_tick") else item["start_tick"])
        item_end = _span_end(item)
        if item_start < end_tick and item_end > start_tick:
            overlapping.append(item)
    return overlapping


def _validate_range(start_tick: int, duration_ticks: int, *, duration_limit: int) -> tuple[int, int]:
    if duration_ticks <= 0:
        raise HarmonyTimelineError(
            HARMONY_TIMELINE_ERROR_CODES["harmony_non_positive_duration"],
            code="harmony_non_positive_duration",
            context={"start_tick": start_tick, "duration_ticks": duration_ticks},
        )
    end_tick = start_tick + duration_ticks
    if start_tick < 0 or end_tick > duration_limit:
        raise HarmonyTimelineError(
            HARMONY_TIMELINE_ERROR_CODES["harmony_out_of_bounds"],
            code="harmony_out_of_bounds",
            context={
                "start_tick": start_tick,
                "duration_ticks": duration_ticks,
                "duration_ticks_limit": duration_limit,
            },
        )
    return start_tick, end_tick


def _fragment_outside_range(
    spans: Sequence[CompositionV2HarmonyItem],
    *,
    start_tick: int,
    end_tick: int,
) -> list[dict[str, Any]]:
    """Keep left/right fragments of spans that only partially overlap the range."""
    kept: list[dict[str, Any]] = []
    for item in spans:
        item_start = item.start_tick
        item_end = item.start_tick + item.duration_ticks
        if item_end <= start_tick or item_start >= end_tick:
            kept.append(_as_dict(item))
            continue
        if item_start < start_tick:
            kept.append(
                {
                    "start_tick": item_start,
                    "duration_ticks": start_tick - item_start,
                    "chord": item.chord,
                }
            )
        if item_end > end_tick:
            kept.append(
                {
                    "start_tick": end_tick,
                    "duration_ticks": item_end - end_tick,
                    "chord": item.chord,
                }
            )
    return kept


def _commit_spans(composition: CompositionV2, spans: Sequence[dict[str, Any]]) -> CompositionV2:
    merged = merge_adjacent_identical_spans(spans)
    payload = composition.model_dump(mode="json")
    payload["harmony"] = merged
    # Re-validate the entire composition; notes/IDs/motifs remain untouched in payload.
    return CompositionV2.model_validate(payload)


def apply_add(composition: CompositionV2, operation: HarmonyAddOperation) -> CompositionV2:
    start_tick, end_tick = _validate_range(
        operation.span.start_tick,
        operation.span.duration_ticks,
        duration_limit=composition.duration_ticks,
    )
    overlapping = spans_overlapping_range(
        composition.harmony,
        start_tick=start_tick,
        end_tick=end_tick,
    )
    if overlapping:
        logger.warning(
            "Rejected overlapping harmony add",
            extra={"code": "harmony_overlap", "overlap_count": len(overlapping)},
        )
        raise HarmonyTimelineError(
            HARMONY_TIMELINE_ERROR_CODES["harmony_overlap"],
            code="harmony_overlap",
            context={"overlap_count": len(overlapping)},
        )
    spans = [_as_dict(item) for item in composition.harmony]
    spans.append(_as_dict(operation.span))
    return _commit_spans(composition, spans)


def apply_replace(composition: CompositionV2, operation: HarmonyReplaceOperation) -> CompositionV2:
    start_tick, end_tick = _validate_range(
        operation.start_tick,
        operation.duration_ticks,
        duration_limit=composition.duration_ticks,
    )
    # Explicit empty spans list means clear; omitting would be a different DTO.
    for span in operation.spans:
        span_start, span_end = _validate_range(
            span.start_tick,
            span.duration_ticks,
            duration_limit=composition.duration_ticks,
        )
        if span_start < start_tick or span_end > end_tick:
            raise HarmonyTimelineError(
                "Replacement spans must fit within the replace range",
                code="harmony_out_of_bounds",
                context={"start_tick": span_start, "end_tick": span_end},
            )
    kept = _fragment_outside_range(
        composition.harmony,
        start_tick=start_tick,
        end_tick=end_tick,
    )
    kept.extend(_as_dict(span) for span in operation.spans)
    return _commit_spans(composition, kept)


def apply_remove(composition: CompositionV2, operation: HarmonyRemoveOperation) -> CompositionV2:
    start_tick, end_tick = _validate_range(
        operation.start_tick,
        operation.duration_ticks,
        duration_limit=composition.duration_ticks,
    )
    kept = _fragment_outside_range(
        composition.harmony,
        start_tick=start_tick,
        end_tick=end_tick,
    )
    return _commit_spans(composition, kept)


def _find_span_by_start(
    composition: CompositionV2,
    source_start_tick: int,
) -> CompositionV2HarmonyItem:
    for item in composition.harmony:
        if item.start_tick == source_start_tick:
            return item
    raise HarmonyTimelineError(
        HARMONY_TIMELINE_ERROR_CODES["harmony_span_not_found"],
        code="harmony_span_not_found",
        context={"source_start_tick": source_start_tick},
    )


def apply_move(composition: CompositionV2, operation: HarmonyMoveOperation) -> CompositionV2:
    source = _find_span_by_start(composition, operation.source_start_tick)
    new_start = operation.new_start_tick
    duration = source.duration_ticks
    _validate_range(new_start, duration, duration_limit=composition.duration_ticks)
    new_end = new_start + duration
    others = [item for item in composition.harmony if item.start_tick != source.start_tick]
    overlapping = spans_overlapping_range(others, start_tick=new_start, end_tick=new_end)
    if overlapping:
        logger.warning(
            "Rejected overlapping harmony move",
            extra={"code": "harmony_move_overlap", "overlap_count": len(overlapping)},
        )
        raise HarmonyTimelineError(
            HARMONY_TIMELINE_ERROR_CODES["harmony_move_overlap"],
            code="harmony_move_overlap",
            context={"overlap_count": len(overlapping)},
        )
    spans = [_as_dict(item) for item in others]
    spans.append(
        {
            "start_tick": new_start,
            "duration_ticks": duration,
            "chord": source.chord,
        }
    )
    return _commit_spans(composition, spans)


def apply_resize(composition: CompositionV2, operation: HarmonyResizeOperation) -> CompositionV2:
    source = _find_span_by_start(composition, operation.source_start_tick)
    source_end = source.start_tick + source.duration_ticks
    if operation.edge == "start":
        new_start = operation.new_tick
        new_end = source_end
    else:
        new_start = source.start_tick
        new_end = operation.new_tick
    if new_end <= new_start:
        raise HarmonyTimelineError(
            HARMONY_TIMELINE_ERROR_CODES["harmony_invalid_resize"],
            code="harmony_invalid_resize",
            context={"new_start": new_start, "new_end": new_end},
        )
    _validate_range(new_start, new_end - new_start, duration_limit=composition.duration_ticks)
    others = [item for item in composition.harmony if item.start_tick != source.start_tick]
    overlapping = spans_overlapping_range(others, start_tick=new_start, end_tick=new_end)
    if overlapping:
        raise HarmonyTimelineError(
            HARMONY_TIMELINE_ERROR_CODES["harmony_overlap"],
            code="harmony_overlap",
            context={"overlap_count": len(overlapping)},
        )
    spans = [_as_dict(item) for item in others]
    spans.append(
        {
            "start_tick": new_start,
            "duration_ticks": new_end - new_start,
            "chord": source.chord,
        }
    )
    return _commit_spans(composition, spans)


def apply_harmony_timeline_operation(
    composition: CompositionV2,
    operation: HarmonyTimelineOperation,
) -> HarmonyTimelineEditResponse:
    """Apply one immutable harmony operation; never mutates note events or source identity."""
    before_count = len(composition.harmony)
    logger.debug(
        "Harmony timeline operation start",
        extra={
            "operation": operation.operation,
            "span_count_before": before_count,
        },
    )

    source_id = id(composition)
    if isinstance(operation, HarmonyAddOperation):
        updated = apply_add(composition, operation)
    elif isinstance(operation, HarmonyReplaceOperation):
        updated = apply_replace(composition, operation)
    elif isinstance(operation, HarmonyRemoveOperation):
        updated = apply_remove(composition, operation)
    elif isinstance(operation, HarmonyMoveOperation):
        updated = apply_move(composition, operation)
    elif isinstance(operation, HarmonyResizeOperation):
        updated = apply_resize(composition, operation)
    else:
        raise HarmonyTimelineError(
            f"Unsupported harmony operation: {getattr(operation, 'operation', None)}",
            code="harmony_invalid_operation",
        )

    after_count = len(updated.harmony)
    before_dump = [item.model_dump(mode="json") for item in composition.harmony]
    after_dump = [item.model_dump(mode="json") for item in updated.harmony]
    changed = 1 if before_dump != after_dump else 0
    if id(composition) != source_id:
        logger.error("Harmony timeline unexpectedly replaced source object identity")
    logger.info(
        "Harmony timeline operation accepted",
        extra={
            "operation": operation.operation,
            "span_count_before": before_count,
            "span_count_after": after_count,
            "changed_span_count": changed,
        },
    )
    return HarmonyTimelineEditResponse(
        composition=updated,
        operation=operation.operation,
        span_count_before=before_count,
        span_count_after=after_count,
        changed_span_count=changed,
    )


def meter_boundaries_for(composition: CompositionV2) -> list[int]:
    return compile_bar_boundaries(
        time_signature=composition.time_signature,
        ticks_per_quarter=composition.ticks_per_quarter,
        bar_count=composition.bar_count,
        duration_ticks=composition.duration_ticks,
        time_signature_changes=composition.time_signature_changes,
    )
