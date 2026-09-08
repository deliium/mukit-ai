"""Deterministic rhythmic / textural density metrics over analysis context.

Union occupancy is clipped to [0, 1]. Polyphonic note load may exceed 1.0.
Crossing notes are clipped at scope and bar boundaries (never wholly assigned
to the attack bar). Variable-meter bars use compiled timeline boundaries.
"""

from __future__ import annotations

import logging
import statistics
from typing import Sequence

from app.analysis_schemas import (
    ANALYSIS_ALGORITHM_VERSION,
    AnalysisEvidence,
    DensityAnalysisResult,
    DensityMetrics,
    HarmonyAnalysisResult,
    InferenceMeta,
    round_analysis_float,
)
from app.services.composition_analysis_context import CompositionAnalysisContext
from app.services.composition_logical_notes import (
    CollapsedLogicalNote,
    attack_in_interval,
    clip_occupancy,
)


logger = logging.getLogger(__name__)

DENSITY_METHOD = "density.native.v1"


def analyze_density_from_context(
    context: CompositionAnalysisContext,
    harmony: HarmonyAnalysisResult | None = None,
) -> DensityAnalysisResult:
    """Compute scoped density metrics from logical notes and optional harmony."""
    try:
        return _analyze_density_from_context(context, harmony)
    except Exception as exc:
        logger.error(
            "Density analyzer failure",
            extra={"error_type": type(exc).__name__},
        )
        raise


def _analyze_density_from_context(
    context: CompositionAnalysisContext,
    harmony: HarmonyAnalysisResult | None,
) -> DensityAnalysisResult:
    scope = context.resolved_scope
    duration_ticks = max(0, scope.end_tick - scope.start_tick)
    overlapping = context.notes_overlapping_scope()
    attacks = context.attacks_in_scope()

    if duration_ticks <= 0:
        logger.warning(
            "Density analysis abstained",
            extra={"code": "empty_analysis_scope", "scope_kind": scope.kind},
        )
        return DensityAnalysisResult(
            metrics=DensityMetrics(),
            inference=InferenceMeta(
                status="not_applicable",
                confidence=None,
                evidence=AnalysisEvidence(count=0),
                method=DENSITY_METHOD,
                method_version=ANALYSIS_ALGORITHM_VERSION,
            ),
        )

    if not overlapping and not attacks:
        status = "insufficient_evidence"
        logger.info(
            "Density analysis complete",
            extra={"status": status, "attack_count": 0, "note_count": 0},
        )
        return DensityAnalysisResult(
            metrics=DensityMetrics(
                attacks_per_quarter=0.0,
                attacks_per_bar=0.0,
                median_inter_onset_ticks=None,
                active_time_union_ratio=0.0,
                note_load=0.0,
                mean_simultaneity=0.0,
                max_simultaneity=0,
                distinct_pitch_class_density=0.0,
                chord_changes=_chord_change_count(harmony),
                active_track_ratio=0.0,
                section_relative_density=_section_relative_density(context, 0.0),
            ),
            inference=InferenceMeta(
                status=status,
                confidence=0.0,
                evidence=AnalysisEvidence(count=0, mass=0.0, coverage=0.0),
                method=DENSITY_METHOD,
                method_version=ANALYSIS_ALGORITHM_VERSION,
            ),
        )

    tpq = float(context.composition.ticks_per_quarter)
    quarters = duration_ticks / tpq if tpq > 0 else 0.0
    bar_count = max(1, scope.end_bar_exclusive - scope.start_bar)
    attack_count = len(attacks)

    note_load_ticks = sum(context.clip_note_to_scope(note) for _, _, note in overlapping)
    union_ticks = _union_occupancy_in_scope(overlapping, scope.start_tick, scope.end_tick)
    mean_sim, max_sim = _simultaneity(overlapping, scope.start_tick, scope.end_tick)

    onset_ticks = sorted({note.start_tick for _, _, note in attacks})
    median_ioi = _median_ioi(onset_ticks)

    pitched = [(t, n, note) for t, n, note in overlapping if not context.tracks[t].is_drum]
    distinct_pcs = {note.pitch_class for _, _, note in pitched if context.clip_note_to_scope(note) > 0}
    pc_density = len(distinct_pcs) / 12.0

    active_track_ids = {context.tracks[t].track_id for t, _, _ in attacks}
    track_denom = _scoped_track_count(context)
    active_track_ratio = (len(active_track_ids) / track_denom) if track_denom else 0.0

    note_load = note_load_ticks / float(duration_ticks)
    union_ratio = min(1.0, union_ticks / float(duration_ticks))
    # Enforce invariant: only note_load may exceed 1.0.
    if union_ratio > 1.0:
        union_ratio = 1.0

    attacks_per_quarter = (attack_count / quarters) if quarters > 0 else None
    attacks_per_bar = attack_count / float(bar_count)

    metrics = DensityMetrics(
        attacks_per_quarter=attacks_per_quarter,
        attacks_per_bar=attacks_per_bar,
        median_inter_onset_ticks=median_ioi,
        active_time_union_ratio=union_ratio,
        note_load=note_load,
        mean_simultaneity=mean_sim,
        max_simultaneity=max_sim,
        distinct_pitch_class_density=pc_density,
        chord_changes=_chord_change_count(harmony),
        active_track_ratio=active_track_ratio,
        section_relative_density=_section_relative_density(context, note_load),
    )

    coverage = min(1.0, union_ratio)
    confidence = round_analysis_float(min(1.0, 0.35 + 0.65 * coverage)) if attack_count else 0.0

    logger.debug(
        "Density feature summary",
        extra={
            "attack_count": attack_count,
            "note_count": len(overlapping),
            "union_ticks": union_ticks,
            "note_load_ticks": note_load_ticks,
            "duration_ticks": duration_ticks,
            "max_simultaneity": max_sim,
            "distinct_pc_count": len(distinct_pcs),
            "active_track_count": len(active_track_ids),
            "bar_count": bar_count,
        },
    )
    logger.info(
        "Density analysis complete",
        extra={
            "status": "ok",
            "attack_count": attack_count,
            "max_simultaneity": max_sim,
            "chord_changes": metrics.chord_changes,
        },
    )

    return DensityAnalysisResult(
        metrics=metrics,
        inference=InferenceMeta(
            status="ok",
            confidence=confidence,
            evidence=AnalysisEvidence(
                count=attack_count,
                mass=round_analysis_float(note_load_ticks / tpq) if tpq > 0 else None,
                coverage=round_analysis_float(coverage),
            ),
            method=DENSITY_METHOD,
            method_version=ANALYSIS_ALGORITHM_VERSION,
        ),
    )


def _scoped_track_count(context: CompositionAnalysisContext) -> int:
    scope = context.resolved_scope
    if scope.kind == "track":
        return 1
    return len(context.tracks)


def _median_ioi(onset_ticks: Sequence[int]) -> float | None:
    if len(onset_ticks) < 2:
        return None
    deltas = [onset_ticks[i + 1] - onset_ticks[i] for i in range(len(onset_ticks) - 1)]
    return float(statistics.median(deltas))


def _chord_change_count(harmony: HarmonyAnalysisResult | None) -> int:
    if harmony is None:
        return 0
    accepted = [
        span
        for span in harmony.spans
        if span.inference.status == "ok" and span.symbol
    ]
    if len(accepted) < 2:
        return 0
    changes = 0
    for left, right in zip(accepted, accepted[1:]):
        if left.symbol != right.symbol:
            changes += 1
    return changes


def _section_relative_density(
    context: CompositionAnalysisContext,
    scope_note_load: float,
) -> float | None:
    """Compare scoped note load to mean per-section note load (composition-relative)."""
    sections = context.sections
    if not sections:
        return None

    loads: list[float] = []
    for section in sections:
        start = section.start_tick
        end = section.start_tick + section.duration_ticks
        duration = max(1, end - start)
        load_ticks = 0
        for track in context.tracks:
            for note in track.logical_notes:
                load_ticks += clip_occupancy(note, start, end)
        loads.append(load_ticks / float(duration))

    mean_load = statistics.fmean(loads) if loads else 0.0
    if mean_load <= 0.0:
        return 0.0 if scope_note_load <= 0.0 else None
    return scope_note_load / mean_load


def _union_occupancy_in_scope(
    notes: Sequence[tuple[int, int, CollapsedLogicalNote]],
    start: int,
    end: int,
) -> int:
    events: list[tuple[int, int]] = []
    for _, _, note in notes:
        left = max(note.start_tick, start)
        right = min(note.end_tick, end)
        if right <= left:
            continue
        events.append((left, 1))
        events.append((right, -1))
    return _sweep_union(events, start, end)


def _simultaneity(
    notes: Sequence[tuple[int, int, CollapsedLogicalNote]],
    start: int,
    end: int,
) -> tuple[float, int]:
    """Duration-weighted mean and max concurrent note count inside [start, end)."""
    events: list[tuple[int, int]] = []
    for _, _, note in notes:
        left = max(note.start_tick, start)
        right = min(note.end_tick, end)
        if right <= left:
            continue
        events.append((left, 1))
        events.append((right, -1))
    if not events or end <= start:
        return 0.0, 0

    events.sort(key=lambda item: (item[0], item[1]))
    active = 0
    cursor = start
    weighted = 0
    max_active = 0
    for tick, delta in events:
        tick = max(start, min(end, tick))
        if tick > cursor:
            weighted += active * (tick - cursor)
            max_active = max(max_active, active)
            cursor = tick
        active += delta
        max_active = max(max_active, active)
    if end > cursor:
        weighted += active * (end - cursor)
        max_active = max(max_active, active)

    duration = end - start
    mean = weighted / float(duration) if duration > 0 else 0.0
    return mean, max_active


def _sweep_union(events: list[tuple[int, int]], start: int, end: int) -> int:
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


def bar_density_snapshot(
    context: CompositionAnalysisContext,
    bar: int,
) -> dict[str, float | int]:
    """Bounded per-bar density helpers (attacks, union, note load) for tests/orchestrator."""
    hist = context.bar_histograms[bar - 1] if 1 <= bar <= len(context.bar_histograms) else None
    if hist is None:
        return {"attack_count": 0, "union_ticks": 0, "note_load_ticks": 0}
    # Prefer precomputed histogram; re-verify crossing clips via attacks_in_interval.
    start = hist.start_tick
    end = hist.end_tick
    duration = max(1, end - start)
    attack_count = sum(
        1
        for track in context.tracks
        for note in track.logical_notes
        if attack_in_interval(note, start, end)
    )
    return {
        "attack_count": attack_count,
        "union_ticks": hist.union_occupancy_ticks,
        "note_load_ticks": hist.note_load_ticks,
        "union_ratio": hist.union_occupancy_ticks / float(duration),
        "note_load": hist.note_load_ticks / float(duration),
    }
