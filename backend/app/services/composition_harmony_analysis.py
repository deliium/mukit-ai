"""Deterministic chord-span, harmonic-rhythm, and scale-degree analysis.

Builds harmonic frames from the analysis-context onset/offset sweep plus beat/bar
boundaries, scores a fixed triad/seventh/sus/diminished vocabulary, abstains on
weak coverage/margin, merges equivalent adjacent frames, and compares declared
``harmony`` metadata without creating playable notes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal, Sequence

from app.analysis_schemas import (
    ANALYSIS_ALGORITHM_VERSION,
    ANALYSIS_MAX_EVIDENCE_ITEMS,
    ANALYSIS_MAX_SPANS,
    AnalysisEvidence,
    AnalysisSourceLocator,
    ChordSpanResult,
    HarmonicRhythmMetrics,
    HarmonyAnalysisResult,
    InferenceMeta,
    ScaleDegreeAnalysisResult,
    ScaleDegreeBucket,
    TonalityAnalysisResult,
    make_derived_id,
    round_analysis_float,
)
from app.services.composition_analysis_context import CompositionAnalysisContext
from app.services.composition_logical_notes import CollapsedLogicalNote
from app.services.composition_timing import parse_time_signature
from app.services.composition_tonality import (
    ParsedKey,
    parse_chord_symbol,
    parse_key,
)


logger = logging.getLogger(__name__)

HARMONY_METHOD = "harmony.native.v1"

# Acceptance / merge thresholds (documented fixed policy).
MIN_DISTINCT_PCS_FOR_CHORD = 3
MIN_DISTINCT_PCS_FOR_DYAD_HINT = 2
MIN_COVERAGE = 0.66
MIN_SCORE_MARGIN = 0.06
MIN_WINNER_SCORE = 1.35
MIN_CONFIDENCE_FOR_ROMAN = 0.55
MIN_ABSOLUTE_SCORE_MARGIN = 0.35
# Absorb intervening abstain/silent frames shorter than this when neighbors match.
PASSING_ABSORB_RATIO = 0.5  # relative to ticks_per_quarter
# Declared-harmony comparison requires accepted inferred evidence in the bar.
MIN_DECLARED_COMPARE_CONFIDENCE = 0.45

DeclaredAgreement = Literal[
    "agreement",
    "partial_agreement",
    "conflict",
    "unparseable",
    "insufficient_evidence",
    "not_applicable",
]

_PC_TO_NAME = {
    0: "C",
    1: "C#",
    2: "D",
    3: "Eb",
    4: "E",
    5: "F",
    6: "F#",
    7: "G",
    8: "Ab",
    9: "A",
    10: "Bb",
    11: "B",
}

# Fixed chord vocabulary: intervals relative to root (pitch-class offsets).
# Ordered for deterministic tie-breaking after score.
_CHORD_VOCABULARY: tuple[tuple[str, tuple[int, ...]], ...] = (
    ("maj", (0, 4, 7)),
    ("min", (0, 3, 7)),
    ("dom7", (0, 4, 7, 10)),
    ("maj7", (0, 4, 7, 11)),
    ("min7", (0, 3, 7, 10)),
    ("dim", (0, 3, 6)),
    ("dim7", (0, 3, 6, 9)),
    ("min7b5", (0, 3, 6, 10)),
    ("aug", (0, 4, 8)),
    ("sus4", (0, 5, 7)),
    ("sus2", (0, 2, 7)),
)

_QUALITY_RANK = {quality: index for index, (quality, _) in enumerate(_CHORD_VOCABULARY)}

_MAJOR_SCALE = (0, 2, 4, 5, 7, 9, 11)
_MINOR_SCALE = (0, 2, 3, 5, 7, 8, 10)

# Roman numeral templates by mode and scale degree (1-7).
_ROMAN_MAJOR = ("I", "ii", "iii", "IV", "V", "vi", "vii°")
_ROMAN_MINOR = ("i", "ii°", "III", "iv", "V", "VI", "VII")
_FUNCTION_BY_DEGREE = {
    1: "tonic",
    2: "predominant",
    3: "tonic",
    4: "predominant",
    5: "dominant",
    6: "tonic",
    7: "dominant",
}


@dataclass(frozen=True)
class _ChordCandidate:
    root_pc: int
    quality: str
    tones: frozenset[int]
    score: float
    coverage: float
    missing_ratio: float
    extra_ratio: float


@dataclass
class _FrameResult:
    start_tick: int
    end_tick: int
    symbol: str | None
    root_pc: int | None
    quality: str | None
    bass_pc: int | None
    status: str
    confidence: float | None
    coverage: float | None
    score: float
    evidence_count: int
    mass_ticks: int


def analyze_harmony_from_context(
    context: CompositionAnalysisContext,
    tonality: TonalityAnalysisResult | None = None,
) -> HarmonyAnalysisResult:
    """Infer chord spans and harmonic rhythm from pitched note evidence.

    Declared ``composition.harmony`` is compared only; it never creates notes.
    """
    try:
        return _analyze_harmony_from_context(context, tonality)
    except Exception as exc:
        logger.error(
            "Harmony analyzer failure",
            extra={"error_type": type(exc).__name__},
        )
        raise


def analyze_scale_degrees_from_context(
    context: CompositionAnalysisContext,
    tonality: TonalityAnalysisResult | None = None,
) -> ScaleDegreeAnalysisResult:
    """Aggregate signed scale-degree distributions for scoped logical attacks."""
    try:
        return _analyze_scale_degrees_from_context(context, tonality)
    except Exception as exc:
        logger.error(
            "Scale-degree analyzer failure",
            extra={"error_type": type(exc).__name__},
        )
        raise


def _analyze_harmony_from_context(
    context: CompositionAnalysisContext,
    tonality: TonalityAnalysisResult | None,
) -> HarmonyAnalysisResult:
    scope = context.resolved_scope
    pitched = context.notes_overlapping_scope(drums=False)
    if not pitched:
        drums = context.notes_overlapping_scope(drums=True)
        status: str = "not_applicable" if drums else "insufficient_evidence"
        if drums:
            logger.warning(
                "Harmony analysis abstained",
                extra={"code": "percussion_only_scope", "scope_kind": scope.kind},
            )
        else:
            logger.warning(
                "Harmony analysis abstained",
                extra={"code": "insufficient_tonal_evidence", "scope_kind": scope.kind},
            )
        result = HarmonyAnalysisResult(
            spans=[],
            harmonic_rhythm=HarmonicRhythmMetrics(),
            declared_agreement="not_applicable"
            if not context.composition.harmony
            else "insufficient_evidence",
            inference=InferenceMeta(
                status=status,  # type: ignore[arg-type]
                confidence=None,
                evidence=AnalysisEvidence(count=len(drums) if drums else 0),
                method=HARMONY_METHOD,
                method_version=ANALYSIS_ALGORITHM_VERSION,
            ),
        )
        logger.info(
            "Harmony analysis complete",
            extra={"status": status, "span_count": 0, "harmonic_changes": 0},
        )
        return result

    boundary_ticks = _harmonic_boundary_ticks(context)
    frames = _score_frames(context, boundary_ticks, tonality)
    logger.debug(
        "Harmony frame scoring summary",
        extra={
            "frame_count": len(frames),
            "candidate_vocab_size": len(_CHORD_VOCABULARY) * 12,
            "boundary_count": len(boundary_ticks),
            "accepted_frames": sum(1 for frame in frames if frame.status == "ok"),
            "abstain_frames": sum(1 for frame in frames if frame.status != "ok"),
        },
    )

    merged = _merge_frames(frames, context.composition.ticks_per_quarter)
    logger.debug(
        "Harmony frame merge summary",
        extra={
            "input_frames": len(frames),
            "merged_spans": len(merged),
            "merge_reduction": max(0, len(frames) - len(merged)),
        },
    )

    spans = _frames_to_spans(context, merged, tonality)
    if len(spans) > ANALYSIS_MAX_SPANS:
        logger.warning(
            "Harmony spans truncated",
            extra={"code": "result_truncated", "span_count": len(spans)},
        )
        spans = spans[:ANALYSIS_MAX_SPANS]

    rhythm = _harmonic_rhythm_metrics(context, spans)
    agreement = _compare_declared_harmony(context, spans)
    if agreement == "conflict":
        logger.warning(
            "Declared harmony conflicts with inference",
            extra={"code": "declared_harmony_conflicts_with_inference"},
        )
    elif agreement == "unparseable":
        logger.warning(
            "Declared harmony unparseable",
            extra={"code": "declared_harmony_unparseable"},
        )

    accepted = [span for span in spans if span.inference.status == "ok" and span.symbol]
    overall_status = "ok" if accepted else "insufficient_evidence"
    if accepted and any(span.inference.status == "ambiguous" for span in spans):
        overall_status = "ambiguous"
    mean_confidence = None
    if accepted:
        confidences = [
            span.inference.confidence
            for span in accepted
            if span.inference.confidence is not None
        ]
        if confidences:
            mean_confidence = round_analysis_float(sum(confidences) / len(confidences))

    evidence_mass = sum(
        max(0, span.end_tick - span.start_tick) for span in accepted
    ) / float(context.composition.ticks_per_quarter)

    result = HarmonyAnalysisResult(
        spans=spans,
        harmonic_rhythm=rhythm,
        declared_agreement=agreement,
        inference=InferenceMeta(
            status=overall_status,  # type: ignore[arg-type]
            confidence=mean_confidence,
            evidence=AnalysisEvidence(
                count=len(accepted),
                mass=round_analysis_float(evidence_mass),
                coverage=round_analysis_float(
                    sum(span.inference.evidence.coverage or 0.0 for span in accepted)
                    / max(1, len(accepted))
                )
                if accepted
                else None,
            ),
            method=HARMONY_METHOD,
            method_version=ANALYSIS_ALGORITHM_VERSION,
        ),
    )
    logger.info(
        "Harmony analysis complete",
        extra={
            "status": overall_status,
            "span_count": len(spans),
            "accepted_span_count": len(accepted),
            "harmonic_changes": rhythm.unique_chord_count,
            "declared_agreement": agreement,
        },
    )
    return result


def _analyze_scale_degrees_from_context(
    context: CompositionAnalysisContext,
    tonality: TonalityAnalysisResult | None,
) -> ScaleDegreeAnalysisResult:
    attacks = context.attacks_in_scope(drums=False)
    if not attacks:
        status = "not_applicable" if context.notes_overlapping_scope(drums=True) else "insufficient_evidence"
        return ScaleDegreeAnalysisResult(
            buckets=[],
            inference=InferenceMeta(
                status=status,  # type: ignore[arg-type]
                method=HARMONY_METHOD,
                method_version=ANALYSIS_ALGORITHM_VERSION,
            ),
        )

    key_resolver = _KeyAtTick(context, tonality)
    # (degree, alteration) -> (count, mass_ticks)
    aggregates: dict[tuple[int, int], list[float]] = {}
    resolved = 0
    for track_idx, _note_idx, note in attacks:
        key = key_resolver.at_tick(note.start_tick)
        if key is None:
            continue
        degree, alteration = _scale_degree_for_pc(note.pitch_class, key)
        bucket_key = (degree, alteration)
        entry = aggregates.setdefault(bucket_key, [0.0, 0.0])
        entry[0] += 1.0
        entry[1] += float(context.clip_note_to_scope(note))
        resolved += 1

    if resolved == 0:
        return ScaleDegreeAnalysisResult(
            buckets=[],
            inference=InferenceMeta(
                status="insufficient_evidence",
                evidence=AnalysisEvidence(count=len(attacks)),
                method=HARMONY_METHOD,
                method_version=ANALYSIS_ALGORITHM_VERSION,
            ),
        )

    buckets = [
        ScaleDegreeBucket(
            degree=degree,
            alteration=alteration,
            count=int(count_mass[0]),
            mass=round_analysis_float(
                count_mass[1] / float(context.composition.ticks_per_quarter)
            ),
        )
        for (degree, alteration), count_mass in sorted(
            aggregates.items(),
            key=lambda item: (item[0][0], item[0][1], -item[1][0]),
        )
    ]
    # Bound bucket list similarly to evidence caps.
    if len(buckets) > ANALYSIS_MAX_EVIDENCE_ITEMS:
        buckets = buckets[:ANALYSIS_MAX_EVIDENCE_ITEMS]

    return ScaleDegreeAnalysisResult(
        buckets=buckets,
        inference=InferenceMeta(
            status="ok",
            confidence=round_analysis_float(resolved / max(1, len(attacks))),
            evidence=AnalysisEvidence(
                count=resolved,
                mass=round_analysis_float(
                    sum(bucket.mass or 0.0 for bucket in buckets)
                ),
            ),
            method=HARMONY_METHOD,
            method_version=ANALYSIS_ALGORITHM_VERSION,
        ),
    )


# ---------------------------------------------------------------------------
# Frame construction and scoring
# ---------------------------------------------------------------------------


def _harmonic_boundary_ticks(context: CompositionAnalysisContext) -> list[int]:
    scope = context.resolved_scope
    ticks: set[int] = {scope.start_tick, scope.end_tick}
    for point in context.sweep_points:
        if scope.start_tick <= point.tick <= scope.end_tick:
            # Skip drum-track sweep points.
            if context.tracks[point.track_index].is_drum:
                continue
            ticks.add(point.tick)

    timeline = context.timeline
    for bar in range(scope.start_bar, scope.end_bar_exclusive):
        if bar < 1 or bar > timeline.bar_count:
            continue
        bar_start = timeline.bar_start_tick(bar)
        bar_end = timeline.bar_end_tick(bar)
        if bar_end <= scope.start_tick or bar_start >= scope.end_tick:
            continue
        ticks.add(max(bar_start, scope.start_tick))
        ticks.add(min(bar_end, scope.end_tick))
        meter = timeline.active_time_signature(bar_start)
        try:
            numerator, _denominator = parse_time_signature(meter)
        except ValueError:
            numerator = 4
        bar_dur = bar_end - bar_start
        if numerator <= 0 or bar_dur <= 0:
            continue
        beat_len = max(1, bar_dur // numerator)
        cursor = bar_start + beat_len
        while cursor < bar_end and cursor < scope.end_tick:
            if cursor > scope.start_tick:
                ticks.add(cursor)
            cursor += beat_len

    return sorted(ticks)


def _score_frames(
    context: CompositionAnalysisContext,
    boundary_ticks: Sequence[int],
    tonality: TonalityAnalysisResult | None,
) -> list[_FrameResult]:
    if len(boundary_ticks) < 2:
        return []

    # Build onset/offset events for non-drum scoped notes (full midi for bass).
    events: list[tuple[int, int, CollapsedLogicalNote, int]] = []
    for track_idx, note_idx, note in context.notes_overlapping_scope(drums=False):
        onset = max(note.start_tick, context.resolved_scope.start_tick)
        offset = min(note.end_tick, context.resolved_scope.end_tick)
        if offset <= onset:
            continue
        events.append((onset, 1, note, track_idx))
        events.append((offset, -1, note, track_idx))
    # At a shared tick, offsets (-1) before onsets (+1) so frames are half-open.
    events.sort(key=lambda item: (item[0], item[1], item[3], item[2].midi_number))

    key_resolver = _KeyAtTick(context, tonality)
    active: dict[tuple[int, int, int, int], CollapsedLogicalNote] = {}
    event_i = 0
    frames: list[_FrameResult] = []
    previous_accepted: tuple[int, str] | None = None

    for left, right in zip(boundary_ticks, boundary_ticks[1:]):
        if right <= left:
            continue
        # Apply every event at tick <= left (offsets then onsets at left).
        while event_i < len(events) and events[event_i][0] <= left:
            _tick, delta, note, track_idx = events[event_i]
            key = (track_idx, note.start_tick, note.midi_number, note.duration_ticks)
            if delta > 0:
                active[key] = note
            else:
                active.pop(key, None)
            event_i += 1

        frame = _score_active_frame(
            start_tick=left,
            end_tick=right,
            active_notes=list(active.values()),
            local_key=key_resolver.at_tick(left),
            previous_accepted=previous_accepted,
        )
        frames.append(frame)
        if frame.status == "ok" and frame.root_pc is not None and frame.quality is not None:
            previous_accepted = (frame.root_pc, frame.quality)

    return frames


def _score_active_frame(
    *,
    start_tick: int,
    end_tick: int,
    active_notes: Sequence[CollapsedLogicalNote],
    local_key: ParsedKey | None,
    previous_accepted: tuple[int, str] | None,
) -> _FrameResult:
    duration = end_tick - start_tick
    if not active_notes:
        return _FrameResult(
            start_tick=start_tick,
            end_tick=end_tick,
            symbol=None,
            root_pc=None,
            quality=None,
            bass_pc=None,
            status="not_applicable",
            confidence=None,
            coverage=None,
            score=0.0,
            evidence_count=0,
            mass_ticks=duration,
        )

    pc_weights: dict[int, float] = {}
    bass_midi = min(note.midi_number for note in active_notes)
    bass_pc = bass_midi % 12
    for note in active_notes:
        pc_weights[note.pitch_class] = pc_weights.get(note.pitch_class, 0.0) + 1.0
        # Slight bass emphasis for the sounding lowest pitch class.
        if note.midi_number == bass_midi:
            pc_weights[note.pitch_class] += 0.5

    present = frozenset(pc_weights)
    if len(present) < MIN_DISTINCT_PCS_FOR_DYAD_HINT:
        return _FrameResult(
            start_tick=start_tick,
            end_tick=end_tick,
            symbol=None,
            root_pc=None,
            quality=None,
            bass_pc=bass_pc,
            status="insufficient_evidence",
            confidence=None,
            coverage=None,
            score=0.0,
            evidence_count=len(active_notes),
            mass_ticks=duration,
        )

    ranked = _rank_chord_candidates(
        present=present,
        pc_weights=pc_weights,
        bass_pc=bass_pc,
        local_key=local_key,
        previous_accepted=previous_accepted,
    )
    if not ranked:
        return _FrameResult(
            start_tick=start_tick,
            end_tick=end_tick,
            symbol=None,
            root_pc=None,
            quality=None,
            bass_pc=bass_pc,
            status="insufficient_evidence",
            confidence=None,
            coverage=None,
            score=0.0,
            evidence_count=len(active_notes),
            mass_ticks=duration,
        )

    winner = ranked[0]
    runner = ranked[1] if len(ranked) > 1 else None
    abs_margin = winner.score - runner.score if runner is not None else winner.score
    margin = (
        abs_margin / winner.score
        if runner is not None and winner.score > 0
        else 1.0
    )
    top_mass = winner.score + (runner.score if runner else 0.0)
    confidence = winner.score / top_mass if top_mass > 0 else 0.0

    # Ambiguous dyads: two pitch classes only.
    if len(present) < MIN_DISTINCT_PCS_FOR_CHORD:
        logger.debug(
            "Harmony frame abstained (dyad)",
            extra={"status": "ambiguous", "distinct_pcs": len(present)},
        )
        return _FrameResult(
            start_tick=start_tick,
            end_tick=end_tick,
            symbol=None,
            root_pc=None,
            quality=None,
            bass_pc=bass_pc,
            status="ambiguous",
            confidence=round_analysis_float(confidence),
            coverage=round_analysis_float(winner.coverage),
            score=winner.score,
            evidence_count=len(active_notes),
            mass_ticks=duration,
        )

    margin_ok = margin >= MIN_SCORE_MARGIN or abs_margin >= MIN_ABSOLUTE_SCORE_MARGIN
    if (
        winner.coverage < MIN_COVERAGE
        or winner.score < MIN_WINNER_SCORE
        or not margin_ok
    ):
        status = "ambiguous" if (not margin_ok) and winner.coverage >= MIN_COVERAGE else "insufficient_evidence"
        return _FrameResult(
            start_tick=start_tick,
            end_tick=end_tick,
            symbol=None,
            root_pc=None,
            quality=None,
            bass_pc=bass_pc,
            status=status,
            confidence=round_analysis_float(confidence),
            coverage=round_analysis_float(winner.coverage),
            score=winner.score,
            evidence_count=len(active_notes),
            mass_ticks=duration,
        )

    return _FrameResult(
        start_tick=start_tick,
        end_tick=end_tick,
        symbol=_format_chord_symbol(winner.root_pc, winner.quality),
        root_pc=winner.root_pc,
        quality=winner.quality,
        bass_pc=bass_pc,
        status="ok",
        confidence=round_analysis_float(confidence),
        coverage=round_analysis_float(winner.coverage),
        score=winner.score,
        evidence_count=len(active_notes),
        mass_ticks=duration,
    )


def _rank_chord_candidates(
    *,
    present: frozenset[int],
    pc_weights: dict[int, float],
    bass_pc: int,
    local_key: ParsedKey | None,
    previous_accepted: tuple[int, str] | None,
) -> list[_ChordCandidate]:
    total_weight = sum(pc_weights.values()) or 1.0
    candidates: list[_ChordCandidate] = []
    for root in range(12):
        for quality, intervals in _CHORD_VOCABULARY:
            tones = frozenset((root + interval) % 12 for interval in intervals)
            matched = present & tones
            missing = tones - present
            extra = present - tones
            coverage = len(matched) / float(len(tones))
            missing_ratio = len(missing) / float(len(tones))
            extra_ratio = len(extra) / float(max(1, len(present)))
            matched_weight = sum(pc_weights.get(pc, 0.0) for pc in matched) / total_weight

            score = (
                3.0 * coverage
                + 1.2 * matched_weight
                - 1.4 * missing_ratio
                - 1.35 * extra_ratio
            )
            # Prefer candidates that fully explain the sounding pitch classes.
            if extra_ratio == 0.0 and coverage >= 0.99:
                score += 0.85 + 0.12 * len(tones)
            # Prefer complete matches; soft-penalize missing fifth less than missing third/root.
            if root not in present:
                score -= 0.85
            else:
                score += 0.55
            third_pcs = {(root + 3) % 12, (root + 4) % 12}
            if quality in {"sus2", "sus4"}:
                third_pcs = {(root + 2) % 12, (root + 5) % 12}
            if not (present & third_pcs) and quality not in {"sus2", "sus4"}:
                # Missing both possible thirds is harsh for triads/sevenths.
                if quality not in {"dim", "aug"}:
                    score -= 0.35

            if bass_pc == root:
                score += 1.05
            elif bass_pc in tones:
                score += 0.3
            else:
                score -= 0.25

            if previous_accepted is not None and previous_accepted == (root, quality):
                score += 0.35

            if local_key is not None:
                score += _local_key_chord_bonus(root, quality, local_key)

            candidates.append(
                _ChordCandidate(
                    root_pc=root,
                    quality=quality,
                    tones=tones,
                    score=round_analysis_float(score),
                    coverage=coverage,
                    missing_ratio=missing_ratio,
                    extra_ratio=extra_ratio,
                )
            )

    candidates.sort(
        key=lambda item: (
            -item.score,
            item.root_pc,
            _QUALITY_RANK.get(item.quality, 99),
        )
    )
    return candidates


def _local_key_chord_bonus(root: int, quality: str, key: ParsedKey) -> float:
    degree = (root - key.tonic_pc) % 12
    scale = _MAJOR_SCALE if key.mode == "major" else _MINOR_SCALE
    diatonic_roots = {(key.tonic_pc + interval) % 12 for interval in scale}
    bonus = 0.15 if root in diatonic_roots else -0.05
    # Weak functional priors.
    if degree == 0 and quality in {"maj", "maj7", "min", "min7"}:
        bonus += 0.2
    elif degree == 7 and quality in {"maj", "dom7"}:
        bonus += 0.18
    elif degree == 5 and quality in {"maj", "maj7", "min", "min7"}:
        bonus += 0.1
    return bonus


def _format_chord_symbol(root_pc: int, quality: str) -> str:
    root = _PC_TO_NAME[root_pc % 12]
    if quality == "maj":
        return root
    if quality == "min":
        return f"{root}m"
    if quality == "dom7":
        return f"{root}7"
    if quality == "maj7":
        return f"{root}maj7"
    if quality == "min7":
        return f"{root}m7"
    if quality == "dim":
        return f"{root}dim"
    if quality == "dim7":
        return f"{root}dim7"
    if quality == "min7b5":
        return f"{root}m7b5"
    if quality == "aug":
        return f"{root}aug"
    if quality == "sus2":
        return f"{root}sus2"
    if quality == "sus4":
        return f"{root}sus4"
    return f"{root}{quality}"


# ---------------------------------------------------------------------------
# Merge / spans / rhythm / declared comparison
# ---------------------------------------------------------------------------


def _merge_frames(
    frames: Sequence[_FrameResult],
    ticks_per_quarter: int,
) -> list[_FrameResult]:
    if not frames:
        return []
    absorb_limit = max(1, int(ticks_per_quarter * PASSING_ABSORB_RATIO))
    merged: list[_FrameResult] = []

    index = 0
    while index < len(frames):
        current = frames[index]
        # Look ahead to absorb short abstain/silent frames between matching chords.
        cursor = index + 1
        end_tick = current.end_tick
        evidence_count = current.evidence_count
        mass_ticks = current.mass_ticks
        while cursor < len(frames):
            nxt = frames[cursor]
            if _frames_equivalent(current, nxt):
                end_tick = nxt.end_tick
                evidence_count = max(evidence_count, nxt.evidence_count)
                mass_ticks += nxt.mass_ticks
                cursor += 1
                continue
            # Absorb short non-accepted frames if the following accepted matches.
            gap_len = nxt.end_tick - nxt.start_tick
            if (
                current.status == "ok"
                and nxt.status != "ok"
                and gap_len <= absorb_limit
                and cursor + 1 < len(frames)
                and _frames_equivalent(current, frames[cursor + 1])
            ):
                end_tick = nxt.end_tick
                mass_ticks += nxt.mass_ticks
                cursor += 1
                continue
            break

        merged.append(
            _FrameResult(
                start_tick=current.start_tick,
                end_tick=end_tick,
                symbol=current.symbol,
                root_pc=current.root_pc,
                quality=current.quality,
                bass_pc=current.bass_pc,
                status=current.status,
                confidence=current.confidence,
                coverage=current.coverage,
                score=current.score,
                evidence_count=evidence_count,
                mass_ticks=mass_ticks,
            )
        )
        index = cursor if cursor > index else index + 1

    return merged


def _frames_equivalent(left: _FrameResult, right: _FrameResult) -> bool:
    if left.status == "ok" and right.status == "ok":
        return left.root_pc == right.root_pc and left.quality == right.quality
    if left.status != "ok" and right.status != "ok":
        # Collapse all non-accepted adjacent frames regardless of exact abstain code.
        return True
    return False


def _frames_to_spans(
    context: CompositionAnalysisContext,
    frames: Sequence[_FrameResult],
    tonality: TonalityAnalysisResult | None,
) -> list[ChordSpanResult]:
    key_resolver = _KeyAtTick(context, tonality)
    spans: list[ChordSpanResult] = []
    for index, frame in enumerate(frames):
        roman = None
        function = None
        if (
            frame.status == "ok"
            and frame.root_pc is not None
            and frame.quality is not None
            and frame.confidence is not None
            and frame.confidence >= MIN_CONFIDENCE_FOR_ROMAN
        ):
            local_key = key_resolver.accepted_at_tick(frame.start_tick)
            if local_key is not None:
                roman, function = _roman_and_function(frame.root_pc, frame.quality, local_key)

        spans.append(
            ChordSpanResult(
                id=make_derived_id(
                    "chord",
                    context.resolved_scope.kind,
                    index,
                    frame.start_tick,
                    frame.end_tick,
                ),
                start_tick=frame.start_tick,
                end_tick=frame.end_tick,
                symbol=frame.symbol,
                root_pc=frame.root_pc,
                quality=frame.quality,
                bass_pc=frame.bass_pc,
                roman=roman,
                function=function,
                inference=InferenceMeta(
                    status=frame.status,  # type: ignore[arg-type]
                    confidence=frame.confidence,
                    evidence=AnalysisEvidence(
                        count=frame.evidence_count,
                        mass=round_analysis_float(
                            frame.mass_ticks / float(context.composition.ticks_per_quarter)
                        ),
                        coverage=frame.coverage,
                        locators=[
                            AnalysisSourceLocator(
                                start_tick=frame.start_tick,
                                end_tick=frame.end_tick,
                                start_bar=context.timeline.bar_at_tick(frame.start_tick)
                                if frame.start_tick <= context.timeline.duration_ticks
                                else context.resolved_scope.start_bar,
                                end_bar=context.timeline.bar_at_tick(
                                    max(frame.start_tick, frame.end_tick - 1)
                                )
                                if frame.end_tick > 0
                                else context.resolved_scope.start_bar,
                            )
                        ][:ANALYSIS_MAX_EVIDENCE_ITEMS],
                    ),
                    method=HARMONY_METHOD,
                    method_version=ANALYSIS_ALGORITHM_VERSION,
                ),
            )
        )
    return spans


def _harmonic_rhythm_metrics(
    context: CompositionAnalysisContext,
    spans: Sequence[ChordSpanResult],
) -> HarmonicRhythmMetrics:
    accepted = [span for span in spans if span.inference.status == "ok" and span.symbol]
    if not accepted:
        return HarmonicRhythmMetrics(changes_per_bar=None, changes_per_quarter=None, unique_chord_count=0)

    changes = 0
    for left, right in zip(accepted, accepted[1:]):
        if left.symbol != right.symbol:
            changes += 1

    unique = len({(span.root_pc, span.quality) for span in accepted})
    scope = context.resolved_scope
    bar_count = max(1, scope.end_bar_exclusive - scope.start_bar)
    duration_ticks = max(1, scope.end_tick - scope.start_tick)
    quarters = duration_ticks / float(context.composition.ticks_per_quarter)
    return HarmonicRhythmMetrics(
        changes_per_bar=round_analysis_float(changes / float(bar_count)),
        changes_per_quarter=round_analysis_float(changes / quarters) if quarters > 0 else None,
        unique_chord_count=unique,
    )


def _compare_declared_harmony(
    context: CompositionAnalysisContext,
    spans: Sequence[ChordSpanResult],
) -> DeclaredAgreement:
    declared = context.composition.harmony
    if not declared:
        return "not_applicable"

    scope = context.resolved_scope
    relevant = []
    for item in declared:
        start_bar = context.timeline.bar_at_tick(int(item.start_tick))
        if scope.start_bar <= start_bar < scope.end_bar_exclusive:
            relevant.append((item, start_bar))
    if not relevant:
        return "not_applicable"

    unparseable = 0
    agreements = 0
    partials = 0
    conflicts = 0
    insufficient = 0

    for item, _start_bar in relevant:
        parsed = parse_chord_symbol(item.chord)
        if not parsed.parseable:
            unparseable += 1
            continue
        bar_start = int(item.start_tick)
        bar_end = bar_start + int(item.duration_ticks)
        overlapping = [
            span
            for span in spans
            if span.inference.status == "ok"
            and span.root_pc is not None
            and span.start_tick < bar_end
            and span.end_tick > bar_start
            and (span.inference.confidence or 0.0) >= MIN_DECLARED_COMPARE_CONFIDENCE
        ]
        if not overlapping:
            insufficient += 1
            continue

        # Duration-weighted majority inferred chord overlapping the declared span.
        best_span = max(
            overlapping,
            key=lambda span: (
                min(span.end_tick, bar_end) - max(span.start_tick, bar_start),
                span.inference.confidence or 0.0,
            ),
        )
        inferred_quality = _normalize_compare_quality(best_span.quality or "")
        declared_quality = _normalize_compare_quality(parsed.quality)
        if best_span.root_pc == parsed.root_pc and inferred_quality == declared_quality:
            agreements += 1
        elif best_span.root_pc == parsed.root_pc:
            partials += 1
        else:
            conflicts += 1

    if unparseable and not (agreements or partials or conflicts):
        return "unparseable"
    if conflicts and conflicts >= max(agreements, partials, 1):
        return "conflict"
    if agreements and not conflicts and not partials:
        return "agreement"
    if (agreements or partials) and not conflicts:
        return "partial_agreement"
    if conflicts:
        return "conflict"
    if unparseable:
        return "unparseable"
    if insufficient:
        return "insufficient_evidence"
    return "insufficient_evidence"


def _normalize_compare_quality(quality: str) -> str:
    text = quality.lower().strip()
    if text in {"", "maj", "major", "6", "9"}:
        return "maj"
    if text in {"m", "min", "minor"}:
        return "min"
    if text in {"7", "dom7"}:
        return "dom7"
    if text in {"maj7", "maj9"}:
        return "maj7"
    if text in {"m7", "min7", "m9", "min9"}:
        return "min7"
    if text in {"dim", "dim7"}:
        return "dim"
    if text in {"min7b5", "m7b5", "halfdim"}:
        return "min7b5"
    if text.startswith("sus"):
        return "sus"
    if text == "aug":
        return "aug"
    return text


# ---------------------------------------------------------------------------
# Scale degrees / Roman numerals / key lookup
# ---------------------------------------------------------------------------


class _KeyAtTick:
    def __init__(
        self,
        context: CompositionAnalysisContext,
        tonality: TonalityAnalysisResult | None,
    ) -> None:
        self._context = context
        self._tonality = tonality
        self._local: list[tuple[int, int, ParsedKey]] = []
        self._global: ParsedKey | None = None
        if tonality is not None:
            for span in tonality.local_spans:
                if span.inference.status != "ok" or not span.key:
                    continue
                parsed = parse_key(span.key)
                if parsed is None:
                    continue
                self._local.append((span.start_tick, span.end_tick, parsed))
            if (
                tonality.global_key is not None
                and tonality.global_key.inference.status == "ok"
                and tonality.global_key.key
            ):
                self._global = parse_key(tonality.global_key.key)
            if self._global is None and tonality.effective_key:
                self._global = parse_key(tonality.effective_key)
        if self._global is None:
            self._global = parse_key(context.composition.key)

    def at_tick(self, tick: int) -> ParsedKey | None:
        for start, end, key in self._local:
            if start <= tick < end:
                return key
        if self._global is not None:
            return self._global
        # Fall back to declared timeline key (metadata only).
        return parse_key(self._context.timeline.active_key(tick))

    def accepted_at_tick(self, tick: int) -> ParsedKey | None:
        """Key only when an accepted local or global inference exists."""
        for start, end, key in self._local:
            if start <= tick < end:
                return key
        if (
            self._tonality is not None
            and self._tonality.global_key is not None
            and self._tonality.global_key.inference.status == "ok"
            and self._global is not None
        ):
            return self._global
        return None


def _signed_pc_delta(relative: int, interval: int) -> int:
    raw = relative - interval
    if raw > 6:
        raw -= 12
    elif raw < -6:
        raw += 12
    return raw


def _scale_degree_for_pc(pc: int, key: ParsedKey) -> tuple[int, int]:
    scale = _MAJOR_SCALE if key.mode == "major" else _MINOR_SCALE
    relative = (pc - key.tonic_pc) % 12
    for degree_index, interval in enumerate(scale):
        if relative == interval:
            return degree_index + 1, 0

    # Nearest scale degree with signed alteration in [-2, 2].
    # Tie-break: smaller |alteration|, then lower degree index.
    best: tuple[int, int, int] | None = None  # (|alt|, degree_index, alt)
    for degree_index, interval in enumerate(scale):
        raw = _signed_pc_delta(relative, interval)
        if abs(raw) > 2:
            continue
        candidate = (abs(raw), degree_index, raw)
        if best is None or candidate < best:
            best = candidate
    if best is not None:
        return best[1] + 1, best[2]

    degree_index = min(
        range(7),
        key=lambda idx: (abs(_signed_pc_delta(relative, scale[idx])), idx),
    )
    alteration = max(-2, min(2, _signed_pc_delta(relative, scale[degree_index])))
    return degree_index + 1, alteration


def _roman_and_function(
    root_pc: int,
    quality: str,
    key: ParsedKey,
) -> tuple[str | None, str | None]:
    scale = _MAJOR_SCALE if key.mode == "major" else _MINOR_SCALE
    relative = (root_pc - key.tonic_pc) % 12
    degree_index = None
    for index, interval in enumerate(scale):
        if relative == interval:
            degree_index = index
            break
    if degree_index is None:
        return None, None
    template = _ROMAN_MAJOR if key.mode == "major" else _ROMAN_MINOR
    roman = template[degree_index]
    # Quality-sensitive adjustments for sevenths.
    if quality in {"dom7", "maj7", "min7", "dim7", "min7b5"}:
        if not roman.endswith("7") and "°" not in roman:
            roman = f"{roman}7"
        elif "°" in roman and quality in {"dim7", "min7b5"}:
            roman = roman.replace("°", "ø7" if quality == "min7b5" else "°7")
    function = _FUNCTION_BY_DEGREE.get(degree_index + 1)
    return roman, function
