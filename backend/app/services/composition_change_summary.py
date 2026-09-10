"""Derive bounded musical change scope between two composition snapshots."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.composition_schemas import CompositionV2
from app.project_history_schemas import AffectedBarRange, DeclaredScope
from app.services.composition_timeline import compile_timeline

logger = logging.getLogger(__name__)


class CompositionScopeError(ValueError):
    """Raised when actual changes escape a declared authorization scope."""

    def __init__(self, message: str, *, code: str = "scope_escape") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class CompositionChangeSummary:
    affected_ranges: tuple[AffectedBarRange, ...]
    affected_track_ids: tuple[str, ...]
    identical: bool
    source_track_count: int
    target_track_count: int
    source_event_count: int
    target_event_count: int


def _event_count(composition: CompositionV2 | None) -> int:
    if composition is None:
        return 0
    return sum(len(track.events) for track in composition.tracks)


def _track_map(composition: CompositionV2 | None) -> dict[str, Any]:
    if composition is None:
        return {}
    return {track.id: track for track in composition.tracks}


def _event_identity_key(event: Any) -> tuple[Any, ...]:
    articulations = tuple(getattr(event, "articulations", ()) or ())
    tie = getattr(event, "tie", None)
    tie_key = None
    if tie is not None:
        tie_key = (getattr(tie, "group_id", None), getattr(tie, "type", None))
    return (
        getattr(event, "pitch", None),
        int(getattr(event, "start_tick", 0)),
        int(getattr(event, "duration_ticks", 0)),
        int(getattr(event, "velocity", 0)),
        getattr(event, "staff", None),
        getattr(event, "voice", None),
        articulations,
        tie_key,
    )


def _bars_for_ticks(
    composition: CompositionV2,
    ticks: list[int],
) -> list[int]:
    if not ticks:
        return []
    try:
        timeline = compile_timeline(composition)
    except Exception:
        # Fallback for incomplete timelines: approximate with constant meter.
        tpq = max(1, int(composition.ticks_per_quarter))
        # 4/4 default if meter compile fails
        bar_ticks = tpq * 4
        bars = sorted({max(1, (tick // bar_ticks) + 1) for tick in ticks})
        return [bar for bar in bars if bar <= max(1, composition.bar_count)]
    bars: set[int] = set()
    for tick in ticks:
        clamped = min(max(0, tick), timeline.duration_ticks)
        try:
            bars.add(timeline.bar_at_tick(clamped))
        except ValueError:
            continue
    return sorted(bars)


def _merge_bars_to_ranges(bars: list[int]) -> tuple[AffectedBarRange, ...]:
    if not bars:
        return ()
    ranges: list[AffectedBarRange] = []
    start = prev = bars[0]
    for bar in bars[1:]:
        if bar == prev + 1:
            prev = bar
            continue
        ranges.append(AffectedBarRange(start_bar=start, end_bar=prev))
        start = prev = bar
    ranges.append(AffectedBarRange(start_bar=start, end_bar=prev))
    return tuple(ranges)


def summarize_composition_changes(
    source: CompositionV2 | None,
    target: CompositionV2 | None,
) -> CompositionChangeSummary:
    """Compare two snapshots and return bounded affected bars/tracks."""
    source_tracks = _track_map(source)
    target_tracks = _track_map(target)
    all_track_ids = sorted(set(source_tracks) | set(target_tracks))

    changed_tracks: list[str] = []
    changed_ticks: list[int] = []

    for track_id in all_track_ids:
        left = source_tracks.get(track_id)
        right = target_tracks.get(track_id)
        if left is None or right is None:
            changed_tracks.append(track_id)
            for track in (left, right):
                if track is None:
                    continue
                for event in track.events:
                    changed_ticks.append(int(event.start_tick))
                    changed_ticks.append(int(event.start_tick) + int(event.duration_ticks))
            continue

        identity_changed = (
            left.name != right.name
            or left.instrument != right.instrument
            or left.role != right.role
            or left.midi_program != right.midi_program
            or left.channel != right.channel
            or bool(left.is_drum) != bool(right.is_drum)
        )
        left_keys = sorted(_event_identity_key(event) for event in left.events)
        right_keys = sorted(_event_identity_key(event) for event in right.events)
        if identity_changed or left_keys != right_keys:
            changed_tracks.append(track_id)
            for event in list(left.events) + list(right.events):
                changed_ticks.append(int(event.start_tick))
                changed_ticks.append(int(event.start_tick) + int(event.duration_ticks))

    # Timeline / form metadata changes affect whole document scope.
    metadata_changed = False
    if (source is None) != (target is None):
        metadata_changed = True
    elif source is not None and target is not None:
        metadata_changed = (
            source.tempo != target.tempo
            or source.key != target.key
            or source.time_signature != target.time_signature
            or source.bar_count != target.bar_count
            or source.duration_ticks != target.duration_ticks
            or source.ticks_per_quarter != target.ticks_per_quarter
            or [item.model_dump(mode="json") for item in source.sections]
            != [item.model_dump(mode="json") for item in target.sections]
            or [item.model_dump(mode="json") for item in source.harmony]
            != [item.model_dump(mode="json") for item in target.harmony]
            or [item.model_dump(mode="json") for item in source.markers]
            != [item.model_dump(mode="json") for item in target.markers]
            or [item.model_dump(mode="json") for item in source.motifs]
            != [item.model_dump(mode="json") for item in target.motifs]
        )

    reference = target or source
    if metadata_changed and reference is not None:
        ranges = (AffectedBarRange(start_bar=1, end_bar=max(1, reference.bar_count)),)
        track_ids = tuple(all_track_ids)
    elif reference is None:
        ranges = ()
        track_ids = ()
    else:
        bars = _bars_for_ticks(reference, changed_ticks)
        ranges = _merge_bars_to_ranges(bars)
        track_ids = tuple(changed_tracks)

    identical = not changed_tracks and not metadata_changed
    summary = CompositionChangeSummary(
        affected_ranges=ranges,
        affected_track_ids=track_ids,
        identical=identical,
        source_track_count=len(source_tracks),
        target_track_count=len(target_tracks),
        source_event_count=_event_count(source),
        target_event_count=_event_count(target),
    )
    logger.debug(
        "Computed composition change summary",
        extra={
            "identical": summary.identical,
            "affected_range_count": len(summary.affected_ranges),
            "affected_track_count": len(summary.affected_track_ids),
            "source_event_count": summary.source_event_count,
            "target_event_count": summary.target_event_count,
        },
    )
    return summary


def enforce_declared_scope(
    summary: CompositionChangeSummary,
    declared: DeclaredScope | None,
) -> None:
    """Reject when actual changes escape the declared authorization scope."""
    if declared is None:
        return
    if summary.identical:
        return

    allowed_tracks = set(declared.track_ids)
    if allowed_tracks:
        escaped = [track_id for track_id in summary.affected_track_ids if track_id not in allowed_tracks]
        if escaped:
            logger.warning(
                "Actual track changes escaped declared scope",
                extra={
                    "code": "scope_escape_tracks",
                    "escaped_track_count": len(escaped),
                    "declared_track_count": len(allowed_tracks),
                },
            )
            raise CompositionScopeError(
                "Committed changes affect tracks outside the declared scope",
                code="scope_escape_tracks",
            )

    if declared.ranges:
        def bar_allowed(bar: int) -> bool:
            return any(item.start_bar <= bar <= item.end_bar for item in declared.ranges)

        for item in summary.affected_ranges:
            for bar in range(item.start_bar, item.end_bar + 1):
                if not bar_allowed(bar):
                    logger.warning(
                        "Actual bar changes escaped declared scope",
                        extra={
                            "code": "scope_escape_bars",
                            "affected_range_count": len(summary.affected_ranges),
                            "declared_range_count": len(declared.ranges),
                        },
                    )
                    raise CompositionScopeError(
                        "Committed changes affect bars outside the declared scope",
                        code="scope_escape_bars",
                    )
