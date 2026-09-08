"""Duration-weighted vertical dissonance / tension components.

Uses a versioned interval-class dissonance table for always-available vertical
scores. Chromatic, non-chord-tone, functional-distance, and unresolved-tendency
components are added only when tonality and/or harmony evidence is available.
"""

from __future__ import annotations

import logging
from typing import Sequence

from app.analysis_schemas import (
    ANALYSIS_ALGORITHM_VERSION,
    AnalysisEvidence,
    ChordSpanResult,
    HarmonyAnalysisResult,
    InferenceMeta,
    TensionAnalysisResult,
    TensionComponents,
    TonalityAnalysisResult,
    round_analysis_float,
)
from app.services.composition_analysis_context import CompositionAnalysisContext
from app.services.composition_logical_notes import CollapsedLogicalNote
from app.services.composition_tonality import ParsedKey, parse_key


logger = logging.getLogger(__name__)

TENSION_METHOD = "tension.native.v1"
DISSONANCE_TABLE_VERSION = "interval_class.v1"

# Interval-class (0..6) dissonance weights. Documented fixed table.
# 0=unison/octave, 1=m2/M7, 2=M2/m7, 3=m3/M6, 4=M3/m6, 5=P4/P5, 6=tritone
_INTERVAL_CLASS_DISSONANCE: tuple[float, ...] = (
    0.0,   # 0
    1.0,   # 1 minor second family
    0.55,  # 2 major second family
    0.15,  # 3 minor third family
    0.1,   # 4 major third family
    0.05,  # 5 perfect fourth/fifth
    0.85,  # 6 tritone
)

_MAJOR_SCALE = (0, 2, 4, 5, 7, 9, 11)
_MINOR_SCALE = (0, 2, 3, 5, 7, 8, 10)

_FUNCTION_DISTANCE = {
    ("tonic", "tonic"): 0.0,
    ("tonic", "predominant"): 0.35,
    ("tonic", "dominant"): 0.55,
    ("predominant", "predominant"): 0.0,
    ("predominant", "dominant"): 0.25,
    ("predominant", "tonic"): 0.4,
    ("dominant", "dominant"): 0.0,
    ("dominant", "tonic"): 0.15,
    ("dominant", "predominant"): 0.45,
}


def analyze_tension_from_context(
    context: CompositionAnalysisContext,
    tonality: TonalityAnalysisResult | None = None,
    harmony: HarmonyAnalysisResult | None = None,
) -> TensionAnalysisResult:
    """Compute scoped tension components with explicit availability limitations."""
    try:
        return _analyze_tension_from_context(context, tonality, harmony)
    except Exception as exc:
        logger.error(
            "Tension analyzer failure",
            extra={"error_type": type(exc).__name__},
        )
        raise


def _analyze_tension_from_context(
    context: CompositionAnalysisContext,
    tonality: TonalityAnalysisResult | None,
    harmony: HarmonyAnalysisResult | None,
) -> TensionAnalysisResult:
    scope = context.resolved_scope
    pitched = context.notes_overlapping_scope(drums=False)
    limitations: list[str] = []

    if not pitched:
        drums = context.notes_overlapping_scope(drums=True)
        status = "not_applicable" if drums else "insufficient_evidence"
        if drums:
            limitations.append("percussion_only_scope")
            logger.warning(
                "Tension analysis abstained",
                extra={"code": "percussion_only_scope", "scope_kind": scope.kind},
            )
        logger.info(
            "Tension analysis complete",
            extra={"status": status, "limitation_count": len(limitations)},
        )
        return TensionAnalysisResult(
            components=TensionComponents(),
            limitations=limitations,
            inference=InferenceMeta(
                status=status,  # type: ignore[arg-type]
                confidence=None,
                evidence=AnalysisEvidence(count=0),
                method=TENSION_METHOD,
                method_version=ANALYSIS_ALGORITHM_VERSION,
            ),
        )

    vertical, mass_ticks, frame_count = _vertical_dissonance(pitched, scope.start_tick, scope.end_tick)
    logger.debug(
        "Tension vertical summary",
        extra={
            "frame_count": frame_count,
            "mass_ticks": mass_ticks,
            "table_version": DISSONANCE_TABLE_VERSION,
            "note_count": len(pitched),
        },
    )

    key = _resolve_key(tonality, context)
    chromatic = None
    if key is None:
        limitations.append("chromatic_mass_unavailable")
    else:
        chromatic = _chromatic_mass(pitched, scope.start_tick, scope.end_tick, key)

    nct = None
    functional = None
    unresolved = None
    accepted_spans = [
        span
        for span in (harmony.spans if harmony else [])
        if span.inference.status == "ok" and span.root_pc is not None
    ]
    if not accepted_spans:
        limitations.append("non_chord_tone_mass_unavailable")
        limitations.append("functional_distance_unavailable")
        limitations.append("unresolved_tendency_unavailable")
    else:
        nct = _non_chord_tone_mass(pitched, scope.start_tick, scope.end_tick, accepted_spans)
        functional = _functional_distance(accepted_spans)
        if key is None:
            limitations.append("unresolved_tendency_unavailable")
            unresolved = None
        else:
            unresolved = _unresolved_tendency(
                pitched,
                scope.start_tick,
                scope.end_tick,
                accepted_spans,
                key,
            )

    components = TensionComponents(
        vertical_dissonance=vertical,
        chromatic_mass=chromatic,
        non_chord_tone_mass=nct,
        functional_distance=functional,
        unresolved_tendency=unresolved,
    )

    available = sum(
        1
        for value in (
            components.vertical_dissonance,
            components.chromatic_mass,
            components.non_chord_tone_mass,
            components.functional_distance,
            components.unresolved_tendency,
        )
        if value is not None
    )
    confidence = round_analysis_float(available / 5.0)
    status = "ok" if available else "insufficient_evidence"

    logger.info(
        "Tension analysis complete",
        extra={
            "status": status,
            "available_components": available,
            "limitation_count": len(limitations),
        },
    )

    return TensionAnalysisResult(
        components=components,
        limitations=limitations,
        inference=InferenceMeta(
            status=status,  # type: ignore[arg-type]
            confidence=confidence,
            evidence=AnalysisEvidence(
                count=frame_count,
                mass=round_analysis_float(mass_ticks / float(context.composition.ticks_per_quarter)),
                coverage=round_analysis_float(min(1.0, mass_ticks / max(1, scope.end_tick - scope.start_tick))),
            ),
            method=TENSION_METHOD,
            method_version=ANALYSIS_ALGORITHM_VERSION,
        ),
    )


def _resolve_key(
    tonality: TonalityAnalysisResult | None,
    context: CompositionAnalysisContext,
) -> ParsedKey | None:
    label = None
    if tonality is not None:
        if tonality.effective_key:
            label = tonality.effective_key
        elif tonality.global_key and tonality.global_key.key:
            label = tonality.global_key.key
        elif tonality.declared_key:
            label = tonality.declared_key
    if not label:
        label = context.composition.key
    return parse_key(label) if label else None


def _vertical_dissonance(
    notes: Sequence[tuple[int, int, CollapsedLogicalNote]],
    start: int,
    end: int,
) -> tuple[float, int, int]:
    """Duration-weighted mean pairwise interval-class dissonance."""
    events: list[tuple[int, int, int]] = []  # tick, delta, pitch_class
    for _, _, note in notes:
        left = max(note.start_tick, start)
        right = min(note.end_tick, end)
        if right <= left:
            continue
        events.append((left, 1, note.pitch_class))
        events.append((right, -1, note.pitch_class))
    if not events:
        return 0.0, 0, 0

    events.sort(key=lambda item: (item[0], item[1], item[2]))
    active: dict[int, int] = {}
    cursor = start
    weighted = 0.0
    mass = 0
    frames = 0

    def _frame_score() -> float:
        pcs = [pc for pc, count in active.items() for _ in range(count)]
        if len(pcs) < 2:
            return 0.0
        total = 0.0
        pairs = 0
        for i in range(len(pcs)):
            for j in range(i + 1, len(pcs)):
                interval = abs(pcs[i] - pcs[j]) % 12
                ic = min(interval, 12 - interval)
                total += _INTERVAL_CLASS_DISSONANCE[ic]
                pairs += 1
        return total / float(pairs) if pairs else 0.0

    for tick, delta, pc in events:
        tick = max(start, min(end, tick))
        if tick > cursor:
            score = _frame_score()
            duration = tick - cursor
            weighted += score * duration
            if any(active.values()):
                mass += duration
                frames += 1
            cursor = tick
        if delta > 0:
            active[pc] = active.get(pc, 0) + 1
        else:
            count = active.get(pc, 0) - 1
            if count <= 0:
                active.pop(pc, None)
            else:
                active[pc] = count

    if end > cursor and any(active.values()):
        score = _frame_score()
        duration = end - cursor
        weighted += score * duration
        mass += duration
        frames += 1

    if mass <= 0:
        return 0.0, 0, frames
    return round_analysis_float(weighted / float(mass)), mass, frames


def _chromatic_mass(
    notes: Sequence[tuple[int, int, CollapsedLogicalNote]],
    start: int,
    end: int,
    key: ParsedKey,
) -> float:
    scale = set(_MAJOR_SCALE if key.mode == "major" else _MINOR_SCALE)
    tonic = key.tonic_pc
    chromatic_ticks = 0
    total_ticks = 0
    for _, _, note in notes:
        clipped = max(0, min(note.end_tick, end) - max(note.start_tick, start))
        if clipped <= 0:
            continue
        total_ticks += clipped
        degree_pc = (note.pitch_class - tonic) % 12
        if degree_pc not in scale:
            chromatic_ticks += clipped
    if total_ticks <= 0:
        return 0.0
    return round_analysis_float(chromatic_ticks / float(total_ticks))


def _chord_pcs(span: ChordSpanResult) -> set[int]:
    if span.root_pc is None:
        return set()
    quality = span.quality or "maj"
    intervals = {
        "maj": (0, 4, 7),
        "min": (0, 3, 7),
        "dom7": (0, 4, 7, 10),
        "maj7": (0, 4, 7, 11),
        "min7": (0, 3, 7, 10),
        "dim": (0, 3, 6),
        "dim7": (0, 3, 6, 9),
        "min7b5": (0, 3, 6, 10),
        "aug": (0, 4, 8),
        "sus4": (0, 5, 7),
        "sus2": (0, 2, 7),
    }.get(quality, (0, 4, 7))
    return {(span.root_pc + interval) % 12 for interval in intervals}


def _non_chord_tone_mass(
    notes: Sequence[tuple[int, int, CollapsedLogicalNote]],
    start: int,
    end: int,
    spans: Sequence[ChordSpanResult],
) -> float:
    nct_ticks = 0
    total_ticks = 0
    for _, _, note in notes:
        left = max(note.start_tick, start)
        right = min(note.end_tick, end)
        if right <= left:
            continue
        # Allocate note duration across overlapping chord spans.
        remaining = right - left
        cursor = left
        for span in spans:
            if span.end_tick <= cursor or span.start_tick >= right:
                continue
            seg_left = max(cursor, span.start_tick)
            seg_right = min(right, span.end_tick)
            if seg_right <= seg_left:
                continue
            duration = seg_right - seg_left
            total_ticks += duration
            pcs = _chord_pcs(span)
            if note.pitch_class not in pcs:
                nct_ticks += duration
            remaining -= duration
            cursor = seg_right
        if remaining > 0:
            # No covering chord: count as NCT mass contribution.
            total_ticks += remaining
            nct_ticks += remaining
    if total_ticks <= 0:
        return 0.0
    return round_analysis_float(nct_ticks / float(total_ticks))


def _functional_distance(spans: Sequence[ChordSpanResult]) -> float:
    labeled = [span for span in spans if span.function]
    if len(labeled) < 2:
        return 0.0
    total = 0.0
    count = 0
    for left, right in zip(labeled, labeled[1:]):
        key = (left.function or "", right.function or "")
        rev = (right.function or "", left.function or "")
        if key in _FUNCTION_DISTANCE:
            total += _FUNCTION_DISTANCE[key]
        elif rev in _FUNCTION_DISTANCE:
            total += _FUNCTION_DISTANCE[rev]
        else:
            total += 0.3
        count += 1
    if count <= 0:
        return 0.0
    return round_analysis_float(total / float(count))


def _unresolved_tendency(
    notes: Sequence[tuple[int, int, CollapsedLogicalNote]],
    start: int,
    end: int,
    spans: Sequence[ChordSpanResult],
    key: ParsedKey,
) -> float:
    """Mass of dominant-function / leading-tone material not followed by tonic resolution."""
    leading = (key.tonic_pc + 11) % 12
    tonic_pc = key.tonic_pc
    tendency_ticks = 0
    unresolved_ticks = 0

    for index, span in enumerate(spans):
        is_dominant = (span.function == "dominant") or (
            span.root_pc is not None and span.root_pc == (key.tonic_pc + 7) % 12
        )
        if not is_dominant and span.root_pc is None:
            continue
        # Leading-tone presence in span window.
        window_notes = [
            note
            for _, _, note in notes
            if note.start_tick < span.end_tick
            and note.end_tick > span.start_tick
            and max(note.start_tick, start) < min(note.end_tick, end)
        ]
        lt_mass = 0
        for note in window_notes:
            clipped = max(
                0,
                min(note.end_tick, span.end_tick, end) - max(note.start_tick, span.start_tick, start),
            )
            if clipped <= 0:
                continue
            if note.pitch_class == leading or is_dominant:
                lt_mass += clipped
        if lt_mass <= 0 and not is_dominant:
            continue
        tendency_ticks += max(lt_mass, span.end_tick - span.start_tick if is_dominant else 0)
        resolved = False
        if index + 1 < len(spans):
            nxt = spans[index + 1]
            if nxt.function == "tonic" or nxt.root_pc == tonic_pc:
                resolved = True
        if not resolved:
            unresolved_ticks += max(lt_mass, span.end_tick - span.start_tick if is_dominant else 0)

    if tendency_ticks <= 0:
        return 0.0
    return round_analysis_float(min(1.0, unresolved_ticks / float(tendency_ticks)))
