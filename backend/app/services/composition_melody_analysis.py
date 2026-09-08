"""Deterministic melodic range, contour, phrase, and cadence analysis.

Selects declared ``melody``/``lead`` tracks first, otherwise high-confidence
inferred candidates via a pluggable feature interface (Task 6 connects roles).
Monophonic lines are analyzed directly; polyphonic tracks use a documented
skyline reduction (highest sounding pitch at each attack) with explicit status.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal, Protocol, Sequence

from app.analysis_schemas import (
    ANALYSIS_ALGORITHM_VERSION,
    ANALYSIS_MAX_EVIDENCE_ITEMS,
    ANALYSIS_MAX_PHRASES,
    AnalysisEvidence,
    AnalysisSourceLocator,
    CadenceResult,
    HarmonyAnalysisResult,
    InferenceMeta,
    MelodicProfileResult,
    MelodyAnalysisResult,
    PhraseSpanResult,
    TonalityAnalysisResult,
    make_derived_id,
    round_analysis_float,
)
from app.services.composition_analysis_context import AnalysisTrackView, CompositionAnalysisContext
from app.services.composition_logical_notes import (
    CollapsedLogicalNote,
    clip_occupancy,
    note_overlaps_interval,
)
from app.services.composition_tonality import ParsedKey, parse_key


logger = logging.getLogger(__name__)

MELODY_METHOD = "melody.native.v1"

DECLARED_MELODY_ROLES = frozenset({"melody", "lead"})

# Candidate selection
MIN_INFERRED_CANDIDATE_SCORE = 0.55
MIN_INFERRED_CANDIDATE_MARGIN = 0.08
MAX_PROFILES = 8

# Contour thresholds (semitone / ratio policy)
STATIC_RANGE_SEMITONES = 2
STATIC_NET_ABS_SEMITONES = 1
ARCH_MIN_RISE_FALL = 3
DIRECTION_CHANGE_MIXED_RATIO = 0.45
MIN_NOTES_FOR_CONTOUR = 2

# Phrase boundary scoring
MIN_REST_TICKS_RATIO = 0.5  # relative to ticks_per_quarter
LONG_NOTE_RATIO = 2.0  # preceding duration vs median IOI
MIN_PHRASE_BOUNDARY_SCORE = 2.5
MIN_PHRASE_GAP_TICKS_RATIO = 1.0  # suppress closer competing boundaries
MIN_PHRASE_DURATION_TICKS_RATIO = 1.5
MAX_PHRASE_BOUNDARY_CANDIDATES = 128

# Cadence evidence
MIN_CADENCE_CHORD_CONFIDENCE = 0.45
MIN_CADENCE_KEY_CONFIDENCE = 0.45
MIN_CADENCE_COMBINED = 0.40

CadenceKind = Literal[
    "authentic_perfect",
    "authentic_imperfect",
    "half",
    "plagal",
    "deceptive",
    "unclassified",
]

ContourKind = Literal["ascending", "descending", "arch", "static", "mixed", "unknown"]

_PC_TO_SHARP = {
    0: "C",
    1: "C#",
    2: "D",
    3: "D#",
    4: "E",
    5: "F",
    6: "F#",
    7: "G",
    8: "G#",
    9: "A",
    10: "A#",
    11: "B",
}

_MAJOR_SCALE = (0, 2, 4, 5, 7, 9, 11)
_MINOR_SCALE = (0, 2, 3, 5, 7, 8, 10)


class MelodicCandidateFeatures(Protocol):
    """Pluggable melodic-candidate scoring for Task 6 role inference.

    Implementations supply deterministic per-track feature scores without
    requiring melody analysis to know how roles were inferred.
    """

    @property
    def track_id(self) -> str: ...

    @property
    def score(self) -> float: ...

    @property
    def confidence(self) -> float | None: ...


@dataclass(frozen=True)
class MelodicRoleHints:
    """Optional selection overrides / inferred candidates for melody analysis.

    ``candidate_track_ids`` forces those tracks (after declared melody/lead) when
    present. ``features`` supplies ranked inferred-role scores for Task 6; only
    high-confidence winners with sufficient margin are accepted.
    """

    candidate_track_ids: tuple[str, ...] = ()
    features: tuple[MelodicCandidateFeatures, ...] = ()


@dataclass(frozen=True)
class _SkylineNote:
    """One monophonic attack after optional skyline reduction."""

    pitch: str
    midi_number: int
    pitch_class: int
    start_tick: int
    duration_ticks: int
    end_tick: int
    velocity: int
    source_note: CollapsedLogicalNote


@dataclass
class _PhraseBoundaryCandidate:
    tick: int
    score: float
    reasons: tuple[str, ...]


def analyze_melody_from_context(
    context: CompositionAnalysisContext,
    tonality: TonalityAnalysisResult | None = None,
    harmony: HarmonyAnalysisResult | None = None,
    role_hints: MelodicRoleHints | Sequence[str] | None = None,
) -> MelodyAnalysisResult:
    """Analyze melodic profiles, phrases, and cadences for the scoped composition.

    Selection order:
    1. Declared ``melody`` / ``lead`` roles among scoped non-drum tracks.
    2. Explicit ``role_hints.candidate_track_ids`` (or a plain ID sequence).
    3. High-confidence inferred features from ``role_hints.features`` (Task 6).

    Polyphonic tracks are reduced with a deterministic skyline (highest MIDI at
    each attack onset among concurrently sounding notes on that track).
    """
    try:
        return _analyze_melody_from_context(context, tonality, harmony, role_hints)
    except Exception as exc:
        logger.error(
            "Melody analyzer failure",
            extra={"error_type": type(exc).__name__},
        )
        raise


def _normalize_role_hints(
    role_hints: MelodicRoleHints | Sequence[str] | None,
) -> MelodicRoleHints:
    if role_hints is None:
        return MelodicRoleHints()
    if isinstance(role_hints, MelodicRoleHints):
        return role_hints
    ids = tuple(str(item) for item in role_hints if item)
    return MelodicRoleHints(candidate_track_ids=ids)


def _analyze_melody_from_context(
    context: CompositionAnalysisContext,
    tonality: TonalityAnalysisResult | None,
    harmony: HarmonyAnalysisResult | None,
    role_hints: MelodicRoleHints | Sequence[str] | None,
) -> MelodyAnalysisResult:
    hints = _normalize_role_hints(role_hints)
    selected, selection_status, selection_confidence = _select_melody_tracks(context, hints)

    logger.debug(
        "Melody candidate selection",
        extra={
            "selected_count": len(selected),
            "selection_status": selection_status,
            "declared_melody_count": sum(
                1
                for track in context.tracks
                if not track.is_drum and track.role in DECLARED_MELODY_ROLES
            ),
            "hint_id_count": len(hints.candidate_track_ids),
            "feature_count": len(hints.features),
        },
    )

    if not selected:
        status = selection_status
        if status == "ok":
            status = "insufficient_evidence"
        logger.warning(
            "Melody analysis abstained",
            extra={"code": "insufficient_tonal_evidence", "status": status},
        )
        result = MelodyAnalysisResult(
            profiles=[],
            phrases=[],
            cadences=[],
            inference=InferenceMeta(
                status=status,  # type: ignore[arg-type]
                confidence=None,
                evidence=AnalysisEvidence(count=0),
                method=MELODY_METHOD,
                method_version=ANALYSIS_ALGORITHM_VERSION,
            ),
        )
        logger.info(
            "Melody analysis complete",
            extra={"profile_count": 0, "phrase_count": 0, "cadence_count": 0, "status": status},
        )
        return result

    profiles: list[MelodicProfileResult] = []
    phrases: list[PhraseSpanResult] = []
    cadences: list[CadenceResult] = []
    any_skyline = False
    primary_line: list[_SkylineNote] | None = None
    primary_track_id: str | None = None

    for track in selected:
        line, skyline_reduced = _build_melodic_line(context, track)
        if skyline_reduced:
            any_skyline = True
            logger.warning(
                "Melody skyline reduction applied",
                extra={
                    "code": "melody_skyline_reduction",
                    "track_index": track.track_index,
                    "line_attack_count": len(line),
                },
            )
        profile = _compute_profile(track, line, skyline_reduced)
        profiles.append(profile)
        if primary_line is None and line:
            primary_line = line
            primary_track_id = track.track_id

    if primary_line:
        phrases = _detect_phrases(context, primary_line, primary_track_id, harmony)
        cadences = _classify_cadences(
            context,
            primary_line,
            phrases,
            tonality,
            harmony,
            primary_track_id,
        )

    if len(phrases) > ANALYSIS_MAX_PHRASES:
        logger.warning(
            "Melody phrases truncated",
            extra={"code": "result_truncated", "phrase_count": len(phrases)},
        )
        phrases = phrases[:ANALYSIS_MAX_PHRASES]
        cadences = [c for c in cadences if any(p.end_tick == c.tick for p in phrases)]

    overall_status: str = selection_status
    if any_skyline and overall_status == "ok":
        # Reduction is a limitation, not a failure; keep ok with skyline flags on profiles.
        pass
    if selection_status == "ambiguous":
        overall_status = "ambiguous"
    elif not any(p.inference.status == "ok" for p in profiles):
        overall_status = "insufficient_evidence"

    confidences = [p.inference.confidence for p in profiles if p.inference.confidence is not None]
    if selection_confidence is not None:
        confidences.append(selection_confidence)
    overall_confidence = min(confidences) if confidences else None

    result = MelodyAnalysisResult(
        profiles=profiles[:MAX_PROFILES],
        phrases=phrases,
        cadences=cadences,
        inference=InferenceMeta(
            status=overall_status,  # type: ignore[arg-type]
            confidence=round_analysis_float(overall_confidence)
            if overall_confidence is not None
            else None,
            evidence=AnalysisEvidence(
                count=sum(p.inference.evidence.count for p in profiles),
                coverage=round_analysis_float(
                    sum((p.inference.evidence.coverage or 0.0) for p in profiles)
                    / max(1, len(profiles))
                )
                if profiles
                else None,
            ),
            method=MELODY_METHOD,
            method_version=ANALYSIS_ALGORITHM_VERSION,
        ),
    )
    logger.info(
        "Melody analysis complete",
        extra={
            "profile_count": len(result.profiles),
            "phrase_count": len(result.phrases),
            "cadence_count": len(result.cadences),
            "status": result.inference.status,
            "skyline_reduced": any_skyline,
        },
    )
    return result


def _select_melody_tracks(
    context: CompositionAnalysisContext,
    hints: MelodicRoleHints,
) -> tuple[list[AnalysisTrackView], str, float | None]:
    """Return ordered melodic tracks, status, and selection confidence."""
    scope = context.resolved_scope
    eligible = [
        track
        for track in context.tracks
        if not track.is_drum
        and (scope.track_id is None or track.track_id == scope.track_id)
    ]
    if not eligible:
        return [], "not_applicable", None

    # Track scope: always profile the selected track (even if role is not melody).
    if scope.kind == "track" and scope.track_id:
        for track in eligible:
            if track.track_id == scope.track_id:
                return [track], "ok", 1.0 if track.role in DECLARED_MELODY_ROLES else 0.7
        return [], "not_applicable", None

    declared = [track for track in eligible if track.role in DECLARED_MELODY_ROLES]
    if declared:
        # Prefer canonical track order; multiple declared melodies are all profiled.
        logger.debug(
            "Melody declared role selection",
            extra={"declared_count": len(declared)},
        )
        return declared, "ok", 1.0

    if hints.candidate_track_ids:
        by_id = {track.track_id: track for track in eligible}
        selected = [by_id[tid] for tid in hints.candidate_track_ids if tid in by_id]
        if selected:
            return selected, "ok", 0.85
        return [], "insufficient_evidence", None

    if hints.features:
        scored: list[tuple[float, float, int, AnalysisTrackView]] = []
        by_id = {track.track_id: track for track in eligible}
        for feature in hints.features:
            track = by_id.get(feature.track_id)
            if track is None:
                continue
            confidence = feature.confidence if feature.confidence is not None else feature.score
            scored.append((float(feature.score), float(confidence), track.track_index, track))
        scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
        if not scored:
            return [], "insufficient_evidence", None
        best_score, best_conf, _, best_track = scored[0]
        if best_score < MIN_INFERRED_CANDIDATE_SCORE:
            logger.warning(
                "Melody inferred candidate below threshold",
                extra={"code": "insufficient_tonal_evidence", "best_score": round(best_score, 4)},
            )
            return [], "insufficient_evidence", None
        if len(scored) > 1:
            second_score = scored[1][0]
            if best_score - second_score < MIN_INFERRED_CANDIDATE_MARGIN:
                logger.warning(
                    "Melody candidate ambiguity",
                    extra={
                        "code": "relative_key_ambiguity",
                        "winner_score": round(best_score, 4),
                        "runner_up_score": round(second_score, 4),
                    },
                )
                return [], "ambiguous", None
        return [best_track], "ok", min(1.0, max(0.0, best_conf))

    # No declared/inferred melody: abstain (do not guess from all pitched tracks).
    return [], "insufficient_evidence", None


def _build_melodic_line(
    context: CompositionAnalysisContext,
    track: AnalysisTrackView,
) -> tuple[list[_SkylineNote], bool]:
    """Build a monophonic attack sequence; skyline-reduce when polyphonic."""
    scope_start = context.resolved_scope.start_tick
    scope_end = context.resolved_scope.end_tick
    notes = [
        note
        for note in track.logical_notes
        if note_overlaps_interval(note, scope_start, scope_end)
        and clip_occupancy(note, scope_start, scope_end) > 0
    ]
    if not notes:
        return [], False

    # Attack-onset groups: polyphony if any overlapping occupancy at an attack.
    skyline_needed = _track_is_polyphonic(notes)
    if not skyline_needed:
        line = [
            _SkylineNote(
                pitch=note.pitch,
                midi_number=note.midi_number,
                pitch_class=note.pitch_class,
                start_tick=note.start_tick,
                duration_ticks=clip_occupancy(note, scope_start, scope_end),
                end_tick=min(note.end_tick, scope_end),
                velocity=note.velocity,
                source_note=note,
            )
            for note in sorted(notes, key=lambda item: (item.start_tick, -item.midi_number, item.pitch))
            if scope_start <= note.start_tick < scope_end
        ]
        return line, False

    # Skyline: at each unique attack onset in scope, take the highest MIDI among
    # notes sounding at that tick (start <= tick < end). Duration extends to the
    # next skyline attack or the chosen note's clipped end, whichever is sooner.
    onsets = sorted(
        {
            note.start_tick
            for note in notes
            if scope_start <= note.start_tick < scope_end
        }
    )
    skyline: list[_SkylineNote] = []
    for index, onset in enumerate(onsets):
        sounding = [
            note
            for note in notes
            if note.start_tick <= onset < note.end_tick
        ]
        if not sounding:
            continue
        chosen = max(sounding, key=lambda note: (note.midi_number, -note.start_tick, note.pitch))
        next_onset = onsets[index + 1] if index + 1 < len(onsets) else None
        clipped_end = min(chosen.end_tick, scope_end)
        if next_onset is not None:
            clipped_end = min(clipped_end, next_onset)
        duration = max(0, clipped_end - onset)
        if duration <= 0:
            continue
        skyline.append(
            _SkylineNote(
                pitch=chosen.pitch,
                midi_number=chosen.midi_number,
                pitch_class=chosen.pitch_class,
                start_tick=onset,
                duration_ticks=duration,
                end_tick=onset + duration,
                velocity=chosen.velocity,
                source_note=chosen,
            )
        )
    return skyline, True


def _track_is_polyphonic(notes: Sequence[CollapsedLogicalNote]) -> bool:
    """True when any two notes overlap in time (strict concurrent occupancy)."""
    ordered = sorted(notes, key=lambda note: (note.start_tick, note.end_tick, note.midi_number))
    active_end = -1
    for note in ordered:
        if note.start_tick < active_end:
            return True
        active_end = max(active_end, note.end_tick)
    return False


def _compute_profile(
    track: AnalysisTrackView,
    line: Sequence[_SkylineNote],
    skyline_reduced: bool,
) -> MelodicProfileResult:
    if len(line) < 1:
        return MelodicProfileResult(
            track_id=track.track_id,
            contour="unknown",
            skyline_reduced=skyline_reduced,
            inference=InferenceMeta(
                status="insufficient_evidence",
                confidence=None,
                evidence=AnalysisEvidence(count=0),
                method=MELODY_METHOD,
                method_version=ANALYSIS_ALGORITHM_VERSION,
            ),
        )

    midis = [note.midi_number for note in line]
    pitch_min_midi = min(midis)
    pitch_max_midi = max(midis)
    range_semitones = pitch_max_midi - pitch_min_midi

    duration_mass = sum(max(1, note.duration_ticks) for note in line)
    tessitura = sum(note.midi_number * max(1, note.duration_ticks) for note in line) / duration_mass

    intervals = [line[i + 1].midi_number - line[i].midi_number for i in range(len(line) - 1)]
    net_displacement = (line[-1].midi_number - line[0].midi_number) if len(line) >= 2 else 0
    direction_changes = _count_direction_changes(intervals)
    contour = _classify_contour(midis, intervals, net_displacement, direction_changes, range_semitones)

    mean_abs_interval = (
        sum(abs(delta) for delta in intervals) / len(intervals) if intervals else 0.0
    )

    logger.debug(
        "Melody contour summary",
        extra={
            "track_index": track.track_index,
            "attack_count": len(line),
            "range_semitones": range_semitones,
            "direction_changes": direction_changes,
            "contour": contour,
            "skyline_reduced": skyline_reduced,
            "mean_abs_interval": round(mean_abs_interval, 4),
        },
    )

    status: str = "ok"
    confidence = 0.95 if not skyline_reduced else 0.75
    if len(line) < MIN_NOTES_FOR_CONTOUR:
        status = "insufficient_evidence"
        confidence = 0.35
        contour = "unknown"

    locators = [
        AnalysisSourceLocator(
            track_id=track.track_id,
            track_index=track.track_index,
            start_tick=line[0].start_tick,
            end_tick=line[-1].end_tick,
            event_id=line[0].source_note.source_event_ids[0]
            if line[0].source_note.source_event_ids
            else None,
        )
    ][:ANALYSIS_MAX_EVIDENCE_ITEMS]

    return MelodicProfileResult(
        track_id=track.track_id,
        pitch_min=_midi_to_pitch(pitch_min_midi),
        pitch_max=_midi_to_pitch(pitch_max_midi),
        range_semitones=range_semitones,
        tessitura_midi=round_analysis_float(tessitura),
        contour=contour,
        net_displacement_semitones=net_displacement,
        direction_changes=direction_changes,
        skyline_reduced=skyline_reduced,
        inference=InferenceMeta(
            status=status,  # type: ignore[arg-type]
            confidence=round_analysis_float(confidence),
            evidence=AnalysisEvidence(
                count=len(line),
                mass=round_analysis_float(float(duration_mass)),
                coverage=1.0,
                locators=locators,
            ),
            method=MELODY_METHOD,
            method_version=ANALYSIS_ALGORITHM_VERSION,
        ),
    )


def _count_direction_changes(intervals: Sequence[int]) -> int:
    changes = 0
    previous_sign = 0
    for delta in intervals:
        if delta == 0:
            continue
        sign = 1 if delta > 0 else -1
        if previous_sign != 0 and sign != previous_sign:
            changes += 1
        previous_sign = sign
    return changes


def _classify_contour(
    midis: Sequence[int],
    intervals: Sequence[int],
    net_displacement: int,
    direction_changes: int,
    range_semitones: int,
) -> ContourKind:
    if len(midis) < MIN_NOTES_FOR_CONTOUR:
        return "unknown"
    if range_semitones <= STATIC_RANGE_SEMITONES and abs(net_displacement) <= STATIC_NET_ABS_SEMITONES:
        return "static"

    nonzero = [delta for delta in intervals if delta != 0]
    if not nonzero:
        return "static"

    up = sum(1 for delta in nonzero if delta > 0)
    down = sum(1 for delta in nonzero if delta < 0)
    n = len(nonzero)
    change_ratio = direction_changes / max(1, n - 1) if n > 1 else 0.0

    # Arch: rises then falls (or fall then rise as inverted arch → still arch-like).
    peak_index = max(range(len(midis)), key=lambda idx: (midis[idx], -idx))
    trough_index = min(range(len(midis)), key=lambda idx: (midis[idx], idx))
    if peak_index not in (0, len(midis) - 1):
        rise = midis[peak_index] - midis[0]
        fall = midis[peak_index] - midis[-1]
        if rise >= ARCH_MIN_RISE_FALL and fall >= ARCH_MIN_RISE_FALL:
            return "arch"
    if trough_index not in (0, len(midis) - 1):
        fall = midis[0] - midis[trough_index]
        rise = midis[-1] - midis[trough_index]
        if fall >= ARCH_MIN_RISE_FALL and rise >= ARCH_MIN_RISE_FALL and direction_changes >= 1:
            # Inverted arch still counts as arch for compressed classification.
            return "arch"

    if change_ratio >= DIRECTION_CHANGE_MIXED_RATIO and direction_changes >= 2:
        return "mixed"
    if net_displacement >= 2 and up >= down:
        return "ascending"
    if net_displacement <= -2 and down >= up:
        return "descending"
    if up > down * 1.5:
        return "ascending"
    if down > up * 1.5:
        return "descending"
    if direction_changes >= 2:
        return "mixed"
    if net_displacement > 0:
        return "ascending"
    if net_displacement < 0:
        return "descending"
    return "mixed"


def _detect_phrases(
    context: CompositionAnalysisContext,
    line: Sequence[_SkylineNote],
    track_id: str | None,
    harmony: HarmonyAnalysisResult | None,
) -> list[PhraseSpanResult]:
    if len(line) < 2:
        if len(line) == 1:
            return [
                PhraseSpanResult(
                    id=make_derived_id("phrase", track_id or "melody", 0, line[0].start_tick),
                    start_tick=line[0].start_tick,
                    end_tick=line[0].end_tick,
                    inference=InferenceMeta(
                        status="insufficient_evidence",
                        confidence=0.3,
                        evidence=AnalysisEvidence(count=1),
                        method=MELODY_METHOD,
                        method_version=ANALYSIS_ALGORITHM_VERSION,
                    ),
                )
            ]
        return []

    tpq = context.composition.ticks_per_quarter
    min_rest = max(1, int(tpq * MIN_REST_TICKS_RATIO))
    min_gap = max(1, int(tpq * MIN_PHRASE_GAP_TICKS_RATIO))
    min_phrase_dur = max(1, int(tpq * MIN_PHRASE_DURATION_TICKS_RATIO))

    iois = [line[i + 1].start_tick - line[i].start_tick for i in range(len(line) - 1)]
    median_ioi = sorted(iois)[len(iois) // 2] if iois else tpq

    section_starts = {section.start_tick for section in context.sections}
    candidates: list[_PhraseBoundaryCandidate] = []

    for index in range(len(line) - 1):
        current = line[index]
        nxt = line[index + 1]
        gap = nxt.start_tick - current.end_tick
        score = 0.0
        reasons: list[str] = []

        if gap >= min_rest:
            # Rest-based boundary between notes.
            score += 3.0 + min(2.0, gap / max(1.0, float(tpq)))
            reasons.append("rest")
        elif gap > 0:
            score += 0.5
            reasons.append("short_gap")

        if current.duration_ticks >= LONG_NOTE_RATIO * median_ioi:
            score += 1.5
            reasons.append("long_note")

        # Metric position: boundary preferred near bar starts (after the note).
        boundary_tick = current.end_tick if gap > 0 else nxt.start_tick
        try:
            bar = context.timeline.bar_at_tick(min(boundary_tick, context.timeline.duration_ticks))
            bar_start = context.timeline.bar_start_tick(bar)
            if abs(boundary_tick - bar_start) <= tpq // 8:
                score += 1.25
                reasons.append("bar_position")
            beat_offset = (boundary_tick - bar_start) % tpq
            if beat_offset <= tpq // 8:
                score += 0.5
                reasons.append("beat_position")
        except ValueError:
            pass

        # Contour break: large interval direction change around the join.
        if index > 0 and index + 1 < len(line):
            prev_iv = current.midi_number - line[index - 1].midi_number
            next_iv = nxt.midi_number - current.midi_number
            if prev_iv * next_iv < 0 and abs(prev_iv) + abs(next_iv) >= 5:
                score += 1.0
                reasons.append("contour_break")

        # Section adjacency is a weak hint only — never forced.
        if nxt.start_tick in section_starts or current.end_tick in section_starts:
            score += 0.75
            reasons.append("section_edge")

        # Harmonic resolution near the boundary (weak).
        if harmony is not None and _harmony_resolution_near(harmony, boundary_tick, tpq):
            score += 1.0
            reasons.append("harmonic_resolution")

        if score >= MIN_PHRASE_BOUNDARY_SCORE:
            candidates.append(
                _PhraseBoundaryCandidate(
                    tick=boundary_tick if gap >= 0 else nxt.start_tick,
                    score=score,
                    reasons=tuple(reasons),
                )
            )

    # Always allow start/end of the line as phrase extents (not as scored boundaries).
    candidates.sort(key=lambda item: (-item.score, item.tick))
    if len(candidates) > MAX_PHRASE_BOUNDARY_CANDIDATES:
        candidates = candidates[:MAX_PHRASE_BOUNDARY_CANDIDATES]

    accepted_ticks: list[int] = []
    for candidate in candidates:
        if any(abs(candidate.tick - existing) < min_gap for existing in accepted_ticks):
            continue
        accepted_ticks.append(candidate.tick)
    accepted_ticks.sort()

    logger.debug(
        "Melody phrase boundary scores",
        extra={
            "candidate_count": len(candidates),
            "accepted_count": len(accepted_ticks),
            "min_gap_ticks": min_gap,
        },
    )

    # Build phrase spans from line start → boundaries → line end.
    cuts = [line[0].start_tick, *accepted_ticks, line[-1].end_tick]
    # Deduplicate while preserving order.
    deduped: list[int] = []
    for tick in cuts:
        if not deduped or tick != deduped[-1]:
            deduped.append(tick)

    phrases: list[PhraseSpanResult] = []
    for index in range(len(deduped) - 1):
        start = deduped[index]
        end = deduped[index + 1]
        if end <= start:
            continue
        if end - start < min_phrase_dur and index not in (0, len(deduped) - 2):
            # Merge implausibly short inner phrases forward.
            continue
        notes_in = [note for note in line if start <= note.start_tick < end or (note.start_tick < end and note.end_tick > start)]
        conf = 0.55 + 0.1 * min(3, len(notes_in))
        phrases.append(
            PhraseSpanResult(
                id=make_derived_id("phrase", track_id or "melody", index, start),
                start_tick=start,
                end_tick=end,
                inference=InferenceMeta(
                    status="ok",
                    confidence=round_analysis_float(min(0.95, conf)),
                    evidence=AnalysisEvidence(
                        count=len(notes_in),
                        locators=[
                            AnalysisSourceLocator(
                                track_id=track_id,
                                start_tick=start,
                                end_tick=end,
                            )
                        ],
                    ),
                    method=MELODY_METHOD,
                    method_version=ANALYSIS_ALGORITHM_VERSION,
                ),
            )
        )

    # If suppression removed everything, treat the whole line as one phrase.
    if not phrases and line:
        phrases.append(
            PhraseSpanResult(
                id=make_derived_id("phrase", track_id or "melody", 0, line[0].start_tick),
                start_tick=line[0].start_tick,
                end_tick=line[-1].end_tick,
                inference=InferenceMeta(
                    status="ok",
                    confidence=0.5,
                    evidence=AnalysisEvidence(count=len(line)),
                    method=MELODY_METHOD,
                    method_version=ANALYSIS_ALGORITHM_VERSION,
                ),
            )
        )
    return phrases


def _harmony_resolution_near(
    harmony: HarmonyAnalysisResult,
    tick: int,
    tpq: int,
) -> bool:
    """True when an accepted tonic-function span begins near ``tick`` after a dominant."""
    accepted = [
        span
        for span in harmony.spans
        if span.inference.status == "ok" and span.function and span.symbol
    ]
    if len(accepted) < 2:
        return False
    window = max(tpq // 2, 1)
    for index in range(1, len(accepted)):
        prev = accepted[index - 1]
        cur = accepted[index]
        if abs(cur.start_tick - tick) > window:
            continue
        if prev.function == "dominant" and cur.function == "tonic":
            return True
        if prev.function == "predominant" and cur.function == "tonic":
            return True
    return False


def _classify_cadences(
    context: CompositionAnalysisContext,
    line: Sequence[_SkylineNote],
    phrases: Sequence[PhraseSpanResult],
    tonality: TonalityAnalysisResult | None,
    harmony: HarmonyAnalysisResult | None,
    track_id: str | None,
) -> list[CadenceResult]:
    if not phrases:
        return []

    results: list[CadenceResult] = []
    for phrase_index, phrase in enumerate(phrases):
        endpoint = _melodic_endpoint(line, phrase.end_tick)
        chord_pair = _chord_pair_at(harmony, phrase.end_tick) if harmony else None
        local_key, key_confidence = _local_key_at(tonality, phrase.end_tick, context)

        kind: CadenceKind = "unclassified"
        chord_conf = None
        if chord_pair is not None:
            prev_span, cur_span = chord_pair
            chord_conf = min(
                prev_span.inference.confidence or 0.0,
                cur_span.inference.confidence or 0.0,
            )
            kind = _cadence_kind_from_evidence(
                prev_span.function,
                cur_span.function,
                prev_span.roman,
                cur_span.roman,
                cur_span.bass_pc,
                cur_span.root_pc,
                endpoint,
                local_key,
            )

        evidence_confidences = [phrase.inference.confidence or 0.0]
        if key_confidence is not None:
            evidence_confidences.append(key_confidence)
        if chord_conf is not None:
            evidence_confidences.append(chord_conf)
        if endpoint is not None:
            evidence_confidences.append(0.7)
        else:
            evidence_confidences.append(0.2)

        combined = min(evidence_confidences) if evidence_confidences else 0.0
        status: str = "ok"
        if (
            harmony is None
            or chord_pair is None
            or (chord_conf is not None and chord_conf < MIN_CADENCE_CHORD_CONFIDENCE)
            or (key_confidence is not None and key_confidence < MIN_CADENCE_KEY_CONFIDENCE)
            or combined < MIN_CADENCE_COMBINED
            or local_key is None
        ):
            # Abstain / unclassified when evidence is weak.
            if kind != "unclassified" and combined >= MIN_CADENCE_COMBINED and local_key is not None:
                status = "ok"
            else:
                kind = "unclassified"
                status = "insufficient_evidence"
                combined = min(combined, 0.35)

        try:
            bar = context.timeline.bar_at_tick(
                min(phrase.end_tick, context.timeline.duration_ticks)
            )
        except ValueError:
            bar = None

        logger.debug(
            "Melody cadence evidence",
            extra={
                "phrase_index": phrase_index,
                "kind": kind,
                "status": status,
                "evidence_count": len(evidence_confidences),
                "has_harmony": harmony is not None,
                "has_key": local_key is not None,
            },
        )

        results.append(
            CadenceResult(
                id=make_derived_id("cadence", track_id or "melody", phrase_index, phrase.end_tick),
                kind=kind,
                tick=phrase.end_tick,
                bar=bar,
                inference=InferenceMeta(
                    status=status,  # type: ignore[arg-type]
                    confidence=round_analysis_float(combined),
                    evidence=AnalysisEvidence(
                        count=len(evidence_confidences),
                        locators=[
                            AnalysisSourceLocator(
                                track_id=track_id,
                                start_tick=max(0, phrase.end_tick - context.composition.ticks_per_quarter),
                                end_tick=phrase.end_tick,
                                start_bar=bar,
                                end_bar=bar,
                            )
                        ],
                    ),
                    method=MELODY_METHOD,
                    method_version=ANALYSIS_ALGORITHM_VERSION,
                ),
            )
        )
    return results


def _melodic_endpoint(
    line: Sequence[_SkylineNote],
    phrase_end_tick: int,
) -> _SkylineNote | None:
    candidates = [note for note in line if note.start_tick < phrase_end_tick]
    if not candidates:
        return None
    # Prefer the last attack that ends at/near the phrase end; else last attack before end.
    at_end = [note for note in candidates if note.end_tick >= phrase_end_tick - 1]
    if at_end:
        return max(at_end, key=lambda note: (note.start_tick, note.midi_number))
    return candidates[-1]


def _chord_pair_at(
    harmony: HarmonyAnalysisResult,
    tick: int,
) -> tuple | None:
    accepted = [
        span
        for span in harmony.spans
        if span.inference.status == "ok" and span.symbol and span.function
    ]
    if len(accepted) < 2:
        # Still try with symbols only.
        accepted = [
            span
            for span in harmony.spans
            if span.inference.status == "ok" and span.symbol
        ]
    if not accepted:
        return None

    # Find span covering or ending at tick, and its predecessor.
    current = None
    current_index = None
    for index, span in enumerate(accepted):
        if span.start_tick <= tick <= span.end_tick or span.end_tick == tick:
            current = span
            current_index = index
            break
    if current is None:
        # Nearest preceding span end.
        preceding = [span for span in accepted if span.end_tick <= tick]
        if not preceding:
            return None
        current = preceding[-1]
        current_index = accepted.index(current)
    if current_index is None or current_index == 0:
        return None
    return accepted[current_index - 1], current


def _local_key_at(
    tonality: TonalityAnalysisResult | None,
    tick: int,
    context: CompositionAnalysisContext,
) -> tuple[ParsedKey | None, float | None]:
    if tonality is None:
        declared = parse_key(context.composition.key)
        return declared, 0.4 if declared else None

    for span in tonality.local_spans:
        if span.start_tick <= tick < span.end_tick and span.inference.status == "ok" and span.key:
            parsed = parse_key(span.key)
            return parsed, span.inference.confidence

    if tonality.global_key and tonality.global_key.inference.status == "ok" and tonality.global_key.key:
        return parse_key(tonality.global_key.key), tonality.global_key.inference.confidence

    if tonality.effective_key:
        return parse_key(tonality.effective_key), tonality.inference.confidence

    return parse_key(context.composition.key), 0.35


def _cadence_kind_from_evidence(
    prev_function: str | None,
    cur_function: str | None,
    prev_roman: str | None,
    cur_roman: str | None,
    bass_pc: int | None,
    root_pc: int | None,
    endpoint: _SkylineNote | None,
    local_key: ParsedKey | None,
) -> CadenceKind:
    prev_deg = _roman_degree(prev_roman)
    cur_deg = _roman_degree(cur_roman)

    # Prefer roman degrees when available; fall back to functions.
    is_v_to_i = (prev_deg == 5 and cur_deg == 1) or (
        prev_function == "dominant" and cur_function == "tonic" and (cur_deg in (None, 1))
    )
    is_iv_to_i = (prev_deg == 4 and cur_deg == 1) or (
        prev_function == "predominant" and cur_function == "tonic" and prev_deg in (None, 4)
    )
    is_v_to_vi = prev_deg == 5 and cur_deg == 6
    is_half = cur_deg == 5 or (cur_function == "dominant" and not is_v_to_i)

    if is_v_to_i:
        perfect = _is_perfect_authentic(
            bass_pc=bass_pc,
            root_pc=root_pc,
            endpoint=endpoint,
            local_key=local_key,
        )
        return "authentic_perfect" if perfect else "authentic_imperfect"
    if is_iv_to_i:
        return "plagal"
    if is_v_to_vi:
        return "deceptive"
    if is_half:
        return "half"
    return "unclassified"


def _is_perfect_authentic(
    *,
    bass_pc: int | None,
    root_pc: int | None,
    endpoint: _SkylineNote | None,
    local_key: ParsedKey | None,
) -> bool:
    if local_key is None or root_pc is None or bass_pc is None or endpoint is None:
        return False
    # Root-position tonic: bass == root == tonic.
    if bass_pc != root_pc or root_pc != local_key.tonic_pc:
        return False
    # Melodic endpoint on tonic scale degree.
    degree, alteration = _scale_degree_for_pc(endpoint.pitch_class, local_key)
    return degree == 1 and alteration == 0


def _roman_degree(roman: str | None) -> int | None:
    if not roman:
        return None
    cleaned = (
        roman.replace("ø", "")
        .replace("°", "")
        .replace("7", "")
        .replace("b", "")
        .replace("#", "")
        .strip()
    )
    mapping = {
        "I": 1,
        "i": 1,
        "II": 2,
        "ii": 2,
        "III": 3,
        "iii": 3,
        "IV": 4,
        "iv": 4,
        "V": 5,
        "v": 5,
        "VI": 6,
        "vi": 6,
        "VII": 7,
        "vii": 7,
    }
    return mapping.get(cleaned)


def _scale_degree_for_pc(pc: int, key: ParsedKey) -> tuple[int, int]:
    scale = _MAJOR_SCALE if key.mode == "major" else _MINOR_SCALE
    relative = (pc - key.tonic_pc) % 12
    for degree_index, interval in enumerate(scale):
        if relative == interval:
            return degree_index + 1, 0
    best: tuple[int, int, int] | None = None
    for degree_index, interval in enumerate(scale):
        raw = relative - interval
        if raw > 6:
            raw -= 12
        elif raw < -6:
            raw += 12
        if abs(raw) > 2:
            continue
        candidate = (abs(raw), degree_index, raw)
        if best is None or candidate < best:
            best = candidate
    if best is not None:
        return best[1] + 1, best[2]
    return 1, 0


def _midi_to_pitch(midi: int) -> str:
    """Deterministic sharp-spelling scientific pitch from MIDI number."""
    pc = midi % 12
    octave = (midi // 12) - 1
    return f"{_PC_TO_SHARP[pc]}{octave}"


# Re-export for tests / Task 6 wiring.
__all__ = [
    "MELODY_METHOD",
    "MelodicCandidateFeatures",
    "MelodicRoleHints",
    "analyze_melody_from_context",
]
