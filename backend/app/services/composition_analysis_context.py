"""Request-scoped analysis context: timeline, logical notes, and temporal indexes."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Sequence

from app.analysis_schemas import (
    ANALYSIS_ALGORITHM_VERSION,
    CompositionAnalysisScope,
    ResolvedAnalysisScope,
    fingerprint_log_prefix,
    prepare_analysis_request,
)
from app.composition_schemas import CompositionV2, CompositionV2Section, CompositionV2Track
from app.services.composition_fingerprint import composition_source_fingerprint
from app.services.composition_logical_notes import (
    CollapsedLogicalNote,
    attack_in_interval,
    clip_occupancy,
    collapse_track_tie_chains,
    note_overlaps_interval,
)
from app.services.composition_timeline import CompiledTimeline, compile_timeline


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AnalysisTrackView:
    track_index: int
    track_id: str
    name: str
    instrument: str
    role: str
    is_drum: bool
    midi_program: int
    channel: int
    logical_notes: tuple[CollapsedLogicalNote, ...]


@dataclass(frozen=True)
class BarPitchHistogram:
    bar: int  # 1-based
    start_tick: int
    end_tick: int
    # duration-weighted pitch-class occupancy (non-drum only unless include_drums)
    pitch_class_mass: tuple[float, ...]  # length 12
    attack_count: int
    note_load_ticks: int
    union_occupancy_ticks: int


@dataclass(frozen=True)
class SweepPoint:
    tick: int
    # +1 onset / -1 offset; multiple events at same tick are separate points sorted onset first
    delta: int
    pitch_class: int
    track_index: int
    note_index: int


@dataclass
class CompositionAnalysisContext:
    """Compiled indexes for one analysis request. Pure w.r.t. input CompositionV2."""

    composition: CompositionV2
    timeline: CompiledTimeline
    resolved_scope: ResolvedAnalysisScope
    source_fingerprint: str
    tracks: tuple[AnalysisTrackView, ...]
    sections: tuple[CompositionV2Section, ...]
    # Per-bar histograms for the full composition (index 0 unused; use bar number)
    bar_histograms: tuple[BarPitchHistogram, ...]
    # Prefix sums of pitch-class mass by bar (length bar_count+1), each entry len 12
    bar_pc_prefix: tuple[tuple[float, ...], ...]
    sweep_points: tuple[SweepPoint, ...]
    scoped_logical_notes: tuple[tuple[int, int, CollapsedLogicalNote], ...]  # track_idx, note_idx, note
    tie_collapse_count: int
    build_elapsed_ms: float
    warnings_codes: tuple[str, ...] = field(default_factory=tuple)

    def notes_overlapping_scope(
        self,
        *,
        track_index: int | None = None,
        drums: bool | None = None,
    ) -> list[tuple[int, int, CollapsedLogicalNote]]:
        start = self.resolved_scope.start_tick
        end = self.resolved_scope.end_tick
        results: list[tuple[int, int, CollapsedLogicalNote]] = []
        for t_idx, n_idx, note in self.scoped_logical_notes:
            if track_index is not None and t_idx != track_index:
                continue
            if drums is False and self.tracks[t_idx].is_drum:
                continue
            if drums is True and not self.tracks[t_idx].is_drum:
                continue
            if note_overlaps_interval(note, start, end):
                results.append((t_idx, n_idx, note))
        return results

    def attacks_in_scope(
        self,
        *,
        track_index: int | None = None,
        drums: bool | None = None,
    ) -> list[tuple[int, int, CollapsedLogicalNote]]:
        start = self.resolved_scope.start_tick
        end = self.resolved_scope.end_tick
        results: list[tuple[int, int, CollapsedLogicalNote]] = []
        for t_idx, n_idx, note in self.scoped_logical_notes:
            if track_index is not None and t_idx != track_index:
                continue
            if drums is False and self.tracks[t_idx].is_drum:
                continue
            if drums is True and not self.tracks[t_idx].is_drum:
                continue
            if attack_in_interval(note, start, end):
                results.append((t_idx, n_idx, note))
        return results

    def pitch_class_mass_for_bars(self, start_bar: int, end_bar_exclusive: int) -> list[float]:
        """Duration-weighted non-drum PC mass across [start_bar, end_bar_exclusive)."""
        if start_bar < 1 or end_bar_exclusive > self.timeline.bar_count + 1:
            raise ValueError("bar range out of bounds")
        if end_bar_exclusive <= start_bar:
            return [0.0] * 12
        start_prefix = self.bar_pc_prefix[start_bar - 1]
        end_prefix = self.bar_pc_prefix[end_bar_exclusive - 1]
        return [end_prefix[pc] - start_prefix[pc] for pc in range(12)]

    def section_at_index(self, section_index: int) -> CompositionV2Section:
        return self.sections[section_index]

    def clip_note_to_scope(self, note: CollapsedLogicalNote) -> int:
        return clip_occupancy(
            note,
            self.resolved_scope.start_tick,
            self.resolved_scope.end_tick,
        )

    def clip_note_to_bar(self, note: CollapsedLogicalNote, bar: int) -> int:
        start = self.timeline.bar_start_tick(bar)
        end = self.timeline.bar_end_tick(bar)
        return clip_occupancy(note, start, end)


def build_analysis_context(
    composition: CompositionV2 | dict,
    scope: CompositionAnalysisScope | dict | None = None,
) -> CompositionAnalysisContext:
    """Build one request-scoped analysis context with timeline and temporal indexes."""
    started = time.perf_counter()
    validated, _parsed_scope, resolved = prepare_analysis_request(composition, scope)
    logger.debug(
        "Analysis context build start",
        extra={
            "algorithm_version": ANALYSIS_ALGORITHM_VERSION,
            "scope_kind": resolved.kind,
            "track_count": len(validated.tracks),
            "section_count": len(validated.sections),
            "bar_count": validated.bar_count,
        },
    )

    timeline = compile_timeline(validated)
    fingerprint = composition_source_fingerprint(validated)

    track_views: list[AnalysisTrackView] = []
    all_logical: list[tuple[int, int, CollapsedLogicalNote]] = []
    tie_collapse_count = 0
    warning_codes: list[str] = []

    for track_index, track in enumerate(validated.tracks):
        logical = collapse_track_tie_chains(track)
        tie_collapse_count += sum(1 for note in logical if note.tie_group_id is not None)
        if any(pedal.duration_ticks > 0 for pedal in track.sustain_pedals):
            if "unsupported_sustain_interpretation" not in warning_codes:
                warning_codes.append("unsupported_sustain_interpretation")
        view = AnalysisTrackView(
            track_index=track_index,
            track_id=track.id,
            name=track.name,
            instrument=track.instrument,
            role=track.role,
            is_drum=track.is_drum,
            midi_program=track.midi_program,
            channel=track.channel,
            logical_notes=tuple(logical),
        )
        track_views.append(view)
        for note_index, note in enumerate(logical):
            all_logical.append((track_index, note_index, note))

    # Scope filter for note-derived work: track scope keeps all timeline but clips notes to track.
    scoped_notes = _select_scoped_notes(track_views, resolved)

    bar_histograms, bar_pc_prefix = _build_bar_histograms(timeline, track_views)
    sweep_points = _build_sweep_points(track_views, resolved)

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    context = CompositionAnalysisContext(
        composition=validated,
        timeline=timeline,
        resolved_scope=resolved,
        source_fingerprint=fingerprint,
        tracks=tuple(track_views),
        sections=tuple(validated.sections),
        bar_histograms=tuple(bar_histograms),
        bar_pc_prefix=tuple(bar_pc_prefix),
        sweep_points=tuple(sweep_points),
        scoped_logical_notes=tuple(scoped_notes),
        tie_collapse_count=tie_collapse_count,
        build_elapsed_ms=round(elapsed_ms, 3),
        warnings_codes=tuple(warning_codes),
    )
    logger.info(
        "Analysis context build complete",
        extra={
            "algorithm_version": ANALYSIS_ALGORITHM_VERSION,
            "scope_kind": resolved.kind,
            "fingerprint_prefix": fingerprint_log_prefix(fingerprint),
            "track_count": len(track_views),
            "logical_note_count": len(all_logical),
            "scoped_note_count": len(scoped_notes),
            "sweep_point_count": len(sweep_points),
            "tie_collapse_count": tie_collapse_count,
            "elapsed_ms": context.build_elapsed_ms,
            "warning_codes": list(warning_codes),
        },
    )
    logger.debug(
        "Analysis context build details",
        extra={
            "scope_start_tick": resolved.start_tick,
            "scope_end_tick": resolved.end_tick,
            "bar_histogram_count": len(bar_histograms),
            "frame_index_size": len(sweep_points),
        },
    )
    return context


def _select_scoped_notes(
    tracks: Sequence[AnalysisTrackView],
    resolved: ResolvedAnalysisScope,
) -> list[tuple[int, int, CollapsedLogicalNote]]:
    selected: list[tuple[int, int, CollapsedLogicalNote]] = []
    for track in tracks:
        if resolved.kind == "track" and track.track_id != resolved.track_id:
            continue
        for note_index, note in enumerate(track.logical_notes):
            if note_overlaps_interval(note, resolved.start_tick, resolved.end_tick):
                selected.append((track.track_index, note_index, note))
    # Stable analytical order independent of authored event order.
    selected.sort(
        key=lambda item: (
            item[2].start_tick,
            item[0],
            item[2].pitch,
            item[2].duration_ticks,
            item[1],
        )
    )
    return selected


def _build_bar_histograms(
    timeline: CompiledTimeline,
    tracks: Sequence[AnalysisTrackView],
) -> tuple[list[BarPitchHistogram], list[tuple[float, ...]]]:
    histograms: list[BarPitchHistogram] = []
    # prefix[0] = zeros; prefix[b] = sum of bars 1..b
    prefix: list[list[float]] = [[0.0] * 12]

    for bar in range(1, timeline.bar_count + 1):
        start = timeline.bar_start_tick(bar)
        end = timeline.bar_end_tick(bar)
        mass = [0.0] * 12
        attack_count = 0
        note_load_ticks = 0
        # Sweep union occupancy within the bar for non-drum notes.
        events: list[tuple[int, int]] = []  # (tick, delta)
        for track in tracks:
            for note in track.logical_notes:
                clipped = clip_occupancy(note, start, end)
                if clipped <= 0:
                    continue
                note_load_ticks += clipped
                if attack_in_interval(note, start, end):
                    attack_count += 1
                if not track.is_drum:
                    mass[note.pitch_class] += float(clipped)
                    events.append((max(note.start_tick, start), 1))
                    events.append((min(note.end_tick, end), -1))

        union_ticks = _union_occupancy_ticks(events, start, end)
        histograms.append(
            BarPitchHistogram(
                bar=bar,
                start_tick=start,
                end_tick=end,
                pitch_class_mass=tuple(mass),
                attack_count=attack_count,
                note_load_ticks=note_load_ticks,
                union_occupancy_ticks=union_ticks,
            )
        )
        next_prefix = [prefix[-1][pc] + mass[pc] for pc in range(12)]
        prefix.append(next_prefix)

    return histograms, [tuple(row) for row in prefix]


def _union_occupancy_ticks(
    events: list[tuple[int, int]],
    start: int,
    end: int,
) -> int:
    if not events or end <= start:
        return 0
    events.sort(key=lambda item: (item[0], item[1]))
    active = 0
    cursor = start
    union = 0
    for tick, delta in events:
        tick = max(start, min(end, tick))
        if tick > cursor and active > 0:
            union += tick - cursor
        cursor = tick
        active += delta
    if end > cursor and active > 0:
        union += end - cursor
    return union


def _build_sweep_points(
    tracks: Sequence[AnalysisTrackView],
    resolved: ResolvedAnalysisScope,
) -> list[SweepPoint]:
    points: list[SweepPoint] = []
    for track in tracks:
        if resolved.kind == "track" and track.track_id != resolved.track_id:
            continue
        for note_index, note in enumerate(track.logical_notes):
            if not note_overlaps_interval(note, resolved.start_tick, resolved.end_tick):
                continue
            # Clip sounding interval to scope for frame construction.
            onset = max(note.start_tick, resolved.start_tick)
            offset = min(note.end_tick, resolved.end_tick)
            if offset <= onset:
                continue
            points.append(
                SweepPoint(
                    tick=onset,
                    delta=1,
                    pitch_class=note.pitch_class,
                    track_index=track.track_index,
                    note_index=note_index,
                )
            )
            points.append(
                SweepPoint(
                    tick=offset,
                    delta=-1,
                    pitch_class=note.pitch_class,
                    track_index=track.track_index,
                    note_index=note_index,
                )
            )
    # Onsets (+1) before offsets (-1) at the same tick.
    points.sort(key=lambda point: (point.tick, -point.delta, point.track_index, point.note_index))
    return points


def allocate_crossing_note_to_bars(
    note: CollapsedLogicalNote,
    timeline: CompiledTimeline,
) -> list[tuple[int, int]]:
    """Split note occupancy across bars; returns (bar, clipped_ticks) for each touched bar."""
    allocations: list[tuple[int, int]] = []
    if note.duration_ticks <= 0:
        return allocations
    start_bar = timeline.bar_at_tick(note.start_tick)
    # end tick is exclusive for occupancy; last sounding tick is end-1
    last_tick = max(note.start_tick, note.end_tick - 1)
    end_bar = timeline.bar_at_tick(min(last_tick, timeline.duration_ticks))
    for bar in range(start_bar, end_bar + 1):
        clipped = clip_occupancy(note, timeline.bar_start_tick(bar), timeline.bar_end_tick(bar))
        if clipped > 0:
            allocations.append((bar, clipped))
    return allocations
