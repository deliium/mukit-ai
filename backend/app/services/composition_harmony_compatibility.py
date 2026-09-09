"""Declared-harmony vs realized-note compatibility for explicit V2 spans.

Produces a bounded report with stable finding codes. Chromatic function and
ordinary non-chord tones are informational or warnings — never hard failures
by themselves. Structural corruption and explicit preservation violations are
errors. Never persists analysis results.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass
from typing import Any, Sequence

from app.composition_schemas import CompositionV2, CompositionV2HarmonyItem, midi_pitch_number
from app.harmony_schemas import (
    MAX_COMPATIBILITY_FINDINGS,
    CompatibilityFinding,
    CompatibilityReport,
    CompatibilityStatus,
)
from app.services.composition_timeline import CompiledTimeline, compile_timeline
from app.services.composition_tonality import (
    ParsedChord,
    ParsedKey,
    parse_chord_symbol,
    parse_key,
)


logger = logging.getLogger(__name__)

MELODY_ROLES = frozenset({"melody", "lead"})
BASS_ROLES = frozenset({"bass"})
ACCOMP_ROLES = frozenset({"harmony", "pad", "rhythm"})
DRUM_ROLES = frozenset({"drums", "percussion"})

# Avoid notes relative to chord root (minor 2nd / major 7th color) under sustained pressure.
_AVOID_INTERVALS = frozenset({1, 11})
_STRONG_BEAT_CLASH_WEIGHT = 1.5
_SUSTAIN_AVOID_TICKS_RATIO = 0.45
_CADENCE_WINDOW_BARS = 1
_VOICE_LEADING_LEAP_SEMITONES = 7
_MAX_EVIDENCE_KEYS = 16


@dataclass(frozen=True)
class _NoteOcc:
    track_id: str
    role: str
    pitch: str
    midi: int
    pc: int
    start_tick: int
    end_tick: int
    duration_ticks: int


def analyze_harmony_compatibility(
    composition: CompositionV2,
    *,
    start_tick: int | None = None,
    end_tick: int | None = None,
    source_composition: CompositionV2 | None = None,
    authorized_target_ids: Sequence[str] | None = None,
    preserve_track_ids: Sequence[str] | None = None,
) -> CompatibilityReport:
    """Score declared spans against overlapping note evidence inside an optional range.

    ``source_composition`` enables preservation / unauthorized-change checks when
    comparing a candidate against its base.
    """
    timeline = compile_timeline(composition)
    range_start = 0 if start_tick is None else max(0, int(start_tick))
    range_end = timeline.duration_ticks if end_tick is None else min(int(end_tick), timeline.duration_ticks)
    if range_end <= range_start:
        raise ValueError("compatibility range must be positive")

    active_key_label = timeline.active_key(range_start)
    active_key = parse_key(active_key_label)
    findings: list[CompatibilityFinding] = []
    evidence_counts: Counter[str] = Counter()

    logger.debug(
        "Harmony compatibility start",
        extra={
            "start_tick": range_start,
            "end_tick": range_end,
            "active_key_present": bool(active_key),
            "harmony_span_count": len(composition.harmony),
        },
    )

    notes = _collect_notes(composition, range_start, range_end)
    evidence_counts["note_count"] = len(notes)
    evidence_counts["harmony_span_count"] = sum(
        1
        for span in composition.harmony
        if _overlap(span.start_tick, span.start_tick + span.duration_ticks, range_start, range_end) > 0
    )

    spans_in_range = [
        span
        for span in composition.harmony
        if _overlap(span.start_tick, span.start_tick + span.duration_ticks, range_start, range_end) > 0
    ]

    for span in spans_in_range:
        _analyze_span(
            composition=composition,
            timeline=timeline,
            span=span,
            notes=notes,
            active_key=active_key,
            range_start=range_start,
            range_end=range_end,
            findings=findings,
            evidence_counts=evidence_counts,
        )

    _analyze_cadence(
        timeline=timeline,
        spans=spans_in_range,
        active_key=active_key,
        range_end=range_end,
        findings=findings,
        evidence_counts=evidence_counts,
    )

    if active_key is not None:
        _analyze_tonal_center(
            spans=spans_in_range,
            notes=notes,
            active_key=active_key,
            findings=findings,
            evidence_counts=evidence_counts,
        )

    if source_composition is not None:
        _analyze_preservation(
            source=source_composition,
            candidate=composition,
            range_start=range_start,
            range_end=range_end,
            authorized_target_ids=set(authorized_target_ids or ()),
            preserve_track_ids=set(preserve_track_ids or ()),
            findings=findings,
            evidence_counts=evidence_counts,
        )

    findings = findings[:MAX_COMPATIBILITY_FINDINGS]
    status = _status_from_findings(findings)
    code_counts = Counter(item.code for item in findings)

    logger.info(
        "Harmony compatibility complete",
        extra={
            "status": status,
            "finding_count": len(findings),
            "finding_code_counts": dict(code_counts),
            "evidence_note_count": evidence_counts.get("note_count", 0),
            "evidence_span_count": evidence_counts.get("harmony_span_count", 0),
        },
    )
    if any(item.code in {"unsupported_chord_symbol", "competing_tonal_center"} for item in findings):
        logger.warning(
            "Harmony compatibility risk",
            extra={
                "codes": [
                    item.code
                    for item in findings
                    if item.code in {"unsupported_chord_symbol", "competing_tonal_center"}
                ][:8],
            },
        )

    return CompatibilityReport(
        status=status,
        findings=findings,
        finding_code_counts=dict(sorted(code_counts.items())),
        evidence_counts=_bounded_counts(evidence_counts),
        active_key=active_key_label,
    )


def _analyze_span(
    *,
    composition: CompositionV2,
    timeline: CompiledTimeline,
    span: CompositionV2HarmonyItem,
    notes: Sequence[_NoteOcc],
    active_key: ParsedKey | None,
    range_start: int,
    range_end: int,
    findings: list[CompatibilityFinding],
    evidence_counts: Counter[str],
) -> None:
    span_start = int(span.start_tick)
    span_end = span_start + int(span.duration_ticks)
    overlap_start = max(span_start, range_start)
    overlap_end = min(span_end, range_end)
    if overlap_end <= overlap_start:
        return

    parsed = parse_chord_symbol(span.chord)
    if not parsed.parseable:
        findings.append(
            CompatibilityFinding(
                code="unsupported_chord_symbol",
                severity="warning",
                message="Declared chord symbol could not be parsed for deterministic checks.",
                start_tick=span_start,
                end_tick=span_end,
                evidence={"parseable": False},
            )
        )
        evidence_counts["unsupported_symbol"] += 1
        logger.warning(
            "Harmony compatibility unsupported syntax",
            extra={"code": "unsupported_chord_symbol"},
        )
        return

    if parsed.has_unknown_syntax:
        findings.append(
            CompatibilityFinding(
                code="unsupported_chord_symbol",
                severity="info",
                message="Chord has unrecognized suffix tokens; root/quality still used for checks.",
                start_tick=span_start,
                end_tick=span_end,
                evidence={"has_unknown_suffix": True},
            )
        )
        evidence_counts["partial_symbol"] += 1

    if active_key is not None:
        _classify_function(
            parsed=parsed,
            active_key=active_key,
            span_start=span_start,
            span_end=span_end,
            findings=findings,
            evidence_counts=evidence_counts,
        )

    chord_pcs = set(parsed.pitch_classes) if parsed.pitch_classes else _fallback_triad_pcs(parsed)
    overlapping = [
        note
        for note in notes
        if note.start_tick < overlap_end and note.end_tick > overlap_start and note.role not in DRUM_ROLES
    ]
    if not overlapping:
        evidence_counts["span_without_notes"] += 1
        return

    realized_mass: Counter[int] = Counter()
    for note in overlapping:
        tick_overlap = min(note.end_tick, overlap_end) - max(note.start_tick, overlap_start)
        if tick_overlap > 0:
            realized_mass[note.pc] += tick_overlap

    evidence_counts["realized_pc_mass_ticks"] += sum(realized_mass.values())
    _declared_vs_realized(
        parsed=parsed,
        chord_pcs=chord_pcs,
        realized_mass=realized_mass,
        span_start=span_start,
        span_end=span_end,
        findings=findings,
        evidence_counts=evidence_counts,
    )

    melody_notes = [note for note in overlapping if note.role in MELODY_ROLES]
    bass_notes = [note for note in overlapping if note.role in BASS_ROLES]
    accomp_notes = [note for note in overlapping if note.role in ACCOMP_ROLES]

    _melody_evidence(
        timeline=timeline,
        melody_notes=melody_notes,
        chord_pcs=chord_pcs,
        root_pc=parsed.root_pc,
        overlap_start=overlap_start,
        overlap_end=overlap_end,
        span_start=span_start,
        span_end=span_end,
        findings=findings,
        evidence_counts=evidence_counts,
    )
    _bass_evidence(
        bass_notes=bass_notes,
        parsed=parsed,
        chord_pcs=chord_pcs,
        span_start=span_start,
        span_end=span_end,
        findings=findings,
        evidence_counts=evidence_counts,
    )
    _accompaniment_evidence(
        accomp_notes=accomp_notes,
        chord_pcs=chord_pcs,
        span_start=span_start,
        span_end=span_end,
        findings=findings,
        evidence_counts=evidence_counts,
    )
    _voice_leading_and_tension(
        composition=composition,
        span=span,
        accomp_notes=accomp_notes,
        chord_pcs=chord_pcs,
        findings=findings,
        evidence_counts=evidence_counts,
    )


def _declared_vs_realized(
    *,
    parsed: ParsedChord,
    chord_pcs: set[int],
    realized_mass: Counter[int],
    span_start: int,
    span_end: int,
    findings: list[CompatibilityFinding],
    evidence_counts: Counter[str],
) -> None:
    total = sum(realized_mass.values()) or 1
    support = sum(mass for pc, mass in realized_mass.items() if pc in chord_pcs)
    root_mass = realized_mass.get(parsed.root_pc, 0)
    ratio = support / float(total)
    root_ratio = root_mass / float(total)
    evidence = {
        "support_ratio": round(ratio, 3),
        "root_ratio": round(root_ratio, 3),
        "distinct_realized_pcs": len(realized_mass),
    }
    if ratio >= 0.55 and root_ratio >= 0.12:
        findings.append(
            CompatibilityFinding(
                code="declared_realized_agreement",
                severity="info",
                message="Realized pitch classes support the declared chord.",
                start_tick=span_start,
                end_tick=span_end,
                evidence=evidence,
            )
        )
        evidence_counts["declared_agreement"] += 1
    elif parsed.root_pc in realized_mass and ratio >= 0.3:
        findings.append(
            CompatibilityFinding(
                code="declared_realized_partial",
                severity="info",
                message="Declared root is present with partial chord-tone support.",
                start_tick=span_start,
                end_tick=span_end,
                evidence=evidence,
            )
        )
        evidence_counts["declared_partial"] += 1
    else:
        findings.append(
            CompatibilityFinding(
                code="declared_realized_conflict",
                severity="warning",
                message="Realized notes weakly match the declared chord tones.",
                start_tick=span_start,
                end_tick=span_end,
                evidence=evidence,
            )
        )
        evidence_counts["declared_conflict"] += 1


def _melody_evidence(
    *,
    timeline: CompiledTimeline,
    melody_notes: Sequence[_NoteOcc],
    chord_pcs: set[int],
    root_pc: int,
    overlap_start: int,
    overlap_end: int,
    span_start: int,
    span_end: int,
    findings: list[CompatibilityFinding],
    evidence_counts: Counter[str],
) -> None:
    if not melody_notes:
        return
    chord_tone_ticks = 0
    non_chord_ticks = 0
    strong_clash_ticks = 0
    avoid_ticks = 0
    for note in melody_notes:
        tick_overlap = min(note.end_tick, overlap_end) - max(note.start_tick, overlap_start)
        if tick_overlap <= 0:
            continue
        is_chord_tone = note.pc in chord_pcs
        if is_chord_tone:
            chord_tone_ticks += tick_overlap
            evidence_counts["melody_chord_tone_ticks"] += tick_overlap
        else:
            non_chord_ticks += tick_overlap
            evidence_counts["melody_non_chord_tone_ticks"] += tick_overlap
            if _is_strong_beat(timeline, note.start_tick):
                strong_clash_ticks += tick_overlap
            if ((note.pc - root_pc) % 12) in _AVOID_INTERVALS:
                avoid_ticks += tick_overlap

    if chord_tone_ticks:
        findings.append(
            CompatibilityFinding(
                code="melody_chord_tone",
                severity="info",
                message="Melody includes chord tones under the declared span.",
                start_tick=span_start,
                end_tick=span_end,
                evidence={"ticks": chord_tone_ticks},
            )
        )
    if non_chord_ticks:
        findings.append(
            CompatibilityFinding(
                code="melody_non_chord_tone",
                severity="info",
                message="Melody includes non-chord tones (allowed chromatic color).",
                start_tick=span_start,
                end_tick=span_end,
                evidence={"ticks": non_chord_ticks},
            )
        )
    if strong_clash_ticks > 0:
        findings.append(
            CompatibilityFinding(
                code="melody_strong_beat_clash",
                severity="warning",
                message="Non-chord melody tone lands on a strong beat.",
                start_tick=span_start,
                end_tick=span_end,
                evidence={"ticks": strong_clash_ticks},
            )
        )
    span_ticks = max(1, overlap_end - overlap_start)
    if avoid_ticks / float(span_ticks) >= _SUSTAIN_AVOID_TICKS_RATIO:
        findings.append(
            CompatibilityFinding(
                code="melody_avoid_note_pressure",
                severity="warning",
                message="Sustained avoid-note pressure against the declared chord.",
                start_tick=span_start,
                end_tick=span_end,
                evidence={"ticks": avoid_ticks, "span_ticks": span_ticks},
            )
        )
    # Resolution heuristic: later non-chord attack moves by step into a chord tone.
    ordered = sorted(melody_notes, key=lambda item: item.start_tick)
    for index, note in enumerate(ordered[:-1]):
        if note.pc in chord_pcs:
            continue
        nxt = ordered[index + 1]
        if nxt.pc in chord_pcs and abs(nxt.midi - note.midi) <= 2:
            findings.append(
                CompatibilityFinding(
                    code="melody_tension_resolution",
                    severity="info",
                    message="Non-chord melody tone resolves by step into a chord tone.",
                    start_tick=note.start_tick,
                    end_tick=nxt.end_tick,
                    track_id=note.track_id,
                    evidence={"interval_semitones": abs(nxt.midi - note.midi)},
                )
            )
            evidence_counts["melody_resolutions"] += 1
            break


def _bass_evidence(
    *,
    bass_notes: Sequence[_NoteOcc],
    parsed: ParsedChord,
    chord_pcs: set[int],
    span_start: int,
    span_end: int,
    findings: list[CompatibilityFinding],
    evidence_counts: Counter[str],
) -> None:
    if not bass_notes:
        return
    # Prefer lowest simultaneous pitch as bass support.
    lowest = min(bass_notes, key=lambda note: (note.midi, note.start_tick))
    expected_bass = parsed.bass_pc if parsed.bass_pc is not None else parsed.root_pc
    chord_intervals = {(pc - parsed.root_pc) % 12 for pc in chord_pcs}
    support_intervals = {0, 3, 4, 7, 10, 11}  # R/3/5/7 family
    if lowest.pc == expected_bass:
        code = "bass_inversion_support" if parsed.bass_pc is not None and parsed.bass_pc != parsed.root_pc else "bass_root_support"
        findings.append(
            CompatibilityFinding(
                code=code,
                severity="info",
                message="Bass supports the declared root or slash bass.",
                start_tick=span_start,
                end_tick=span_end,
                track_id=lowest.track_id,
                evidence={"bass_pc": lowest.pc, "expected_bass_pc": expected_bass},
            )
        )
        evidence_counts["bass_support"] += 1
    elif ((lowest.pc - parsed.root_pc) % 12) in support_intervals.intersection(chord_intervals | {0, 3, 4, 7, 10, 11}):
        findings.append(
            CompatibilityFinding(
                code="bass_inversion_support",
                severity="info",
                message="Bass realizes a chord-tone inversion.",
                start_tick=span_start,
                end_tick=span_end,
                track_id=lowest.track_id,
                evidence={"bass_pc": lowest.pc},
            )
        )
        evidence_counts["bass_support"] += 1
    else:
        findings.append(
            CompatibilityFinding(
                code="bass_weak_support",
                severity="warning",
                message="Bass pitch class does not clearly support the declared chord.",
                start_tick=span_start,
                end_tick=span_end,
                track_id=lowest.track_id,
                evidence={"bass_pc": lowest.pc, "expected_bass_pc": expected_bass},
            )
        )
        evidence_counts["bass_weak"] += 1


def _accompaniment_evidence(
    *,
    accomp_notes: Sequence[_NoteOcc],
    chord_pcs: set[int],
    span_start: int,
    span_end: int,
    findings: list[CompatibilityFinding],
    evidence_counts: Counter[str],
) -> None:
    if not accomp_notes:
        return
    total = 0
    support = 0
    for note in accomp_notes:
        total += note.duration_ticks
        if note.pc in chord_pcs:
            support += note.duration_ticks
    ratio = support / float(total or 1)
    findings.append(
        CompatibilityFinding(
            code="accompaniment_pc_support",
            severity="info" if ratio >= 0.45 else "warning",
            message="Accompaniment pitch-class support of the declared chord.",
            start_tick=span_start,
            end_tick=span_end,
            evidence={"support_ratio": round(ratio, 3), "note_count": len(accomp_notes)},
        )
    )
    evidence_counts["accomp_support_checks"] += 1
    density = len(accomp_notes) / float(max(1, span_end - span_start))
    findings.append(
        CompatibilityFinding(
            code="accompaniment_density_delta",
            severity="info",
            message="Accompaniment density under the declared span.",
            start_tick=span_start,
            end_tick=span_end,
            evidence={"attacks_per_tick": round(density, 6), "note_count": len(accomp_notes)},
        )
    )


def _voice_leading_and_tension(
    *,
    composition: CompositionV2,
    span: CompositionV2HarmonyItem,
    accomp_notes: Sequence[_NoteOcc],
    chord_pcs: set[int],
    findings: list[CompatibilityFinding],
    evidence_counts: Counter[str],
) -> None:
    span_start = int(span.start_tick)
    span_end = span_start + int(span.duration_ticks)
    # Mean absolute motion between successive accompaniment attacks.
    by_track: dict[str, list[_NoteOcc]] = {}
    for note in accomp_notes:
        by_track.setdefault(note.track_id, []).append(note)
    leap_count = 0
    for track_id, events in by_track.items():
        ordered = sorted(events, key=lambda item: item.start_tick)
        for left, right in zip(ordered, ordered[1:]):
            if abs(right.midi - left.midi) >= _VOICE_LEADING_LEAP_SEMITONES:
                leap_count += 1
                findings.append(
                    CompatibilityFinding(
                        code="voice_leading_leap",
                        severity="info",
                        message="Accompaniment voice-leading leap detected.",
                        start_tick=left.start_tick,
                        end_tick=right.end_tick,
                        track_id=track_id,
                        evidence={"semitones": abs(right.midi - left.midi)},
                    )
                )
    evidence_counts["voice_leading_leaps"] += leap_count

    # Simple tension proxy: share of non-chord-tone accompaniment mass.
    total = sum(note.duration_ticks for note in accomp_notes) or 1
    non_chord = sum(note.duration_ticks for note in accomp_notes if note.pc not in chord_pcs)
    tension = non_chord / float(total)
    findings.append(
        CompatibilityFinding(
            code="tension_delta",
            severity="info",
            message="Local accompaniment tension proxy from non-chord-tone mass.",
            start_tick=span_start,
            end_tick=span_end,
            evidence={"non_chord_ratio": round(tension, 3)},
        )
    )
    evidence_counts["tension_checks"] += 1
    _ = composition  # reserved for future cross-span deltas


def _analyze_cadence(
    *,
    timeline: CompiledTimeline,
    spans: Sequence[CompositionV2HarmonyItem],
    active_key: ParsedKey | None,
    range_end: int,
    findings: list[CompatibilityFinding],
    evidence_counts: Counter[str],
) -> None:
    if not spans or active_key is None:
        return
    window_start = max(0, range_end - timeline.bar_end_tick(1) + timeline.bar_start_tick(1))
    # Use last bar of selection when available.
    try:
        end_bar = timeline.bar_at_tick(max(0, range_end - 1))
        window_start = timeline.bar_start_tick(max(1, end_bar - _CADENCE_WINDOW_BARS + 1))
    except ValueError:
        pass

    trailing = [
        span
        for span in spans
        if span.start_tick + span.duration_ticks > window_start and span.start_tick < range_end
    ]
    if len(trailing) < 1:
        return
    last = max(trailing, key=lambda item: item.start_tick + item.duration_ticks)
    parsed_last = parse_chord_symbol(last.chord)
    if not parsed_last.parseable:
        return
    degree = (parsed_last.root_pc - active_key.tonic_pc) % 12
    is_tonic = degree == 0
    is_dominant = degree == 7 and parsed_last.quality in {"dom7", "maj", "maj7"}
    prev = None
    earlier = [span for span in trailing if span.start_tick < last.start_tick]
    if earlier:
        prev = max(earlier, key=lambda item: item.start_tick)
    authentic = False
    if prev is not None:
        parsed_prev = parse_chord_symbol(prev.chord)
        if parsed_prev.parseable:
            prev_degree = (parsed_prev.root_pc - active_key.tonic_pc) % 12
            authentic = prev_degree == 7 and is_tonic
    if authentic or (is_dominant and range_end >= last.start_tick + last.duration_ticks):
        findings.append(
            CompatibilityFinding(
                code="cadence_evidence",
                severity="info",
                message="Cadential evidence near the selection end.",
                start_tick=last.start_tick,
                end_tick=last.start_tick + last.duration_ticks,
                evidence={"authentic": authentic, "dominant_end": is_dominant, "tonic_end": is_tonic},
            )
        )
        evidence_counts["cadence_ok"] += 1
    elif is_tonic:
        findings.append(
            CompatibilityFinding(
                code="cadence_evidence",
                severity="info",
                message="Selection ends on tonic harmony.",
                start_tick=last.start_tick,
                end_tick=last.start_tick + last.duration_ticks,
                evidence={"tonic_end": True},
            )
        )
        evidence_counts["cadence_ok"] += 1
    else:
        findings.append(
            CompatibilityFinding(
                code="cadence_weak",
                severity="info",
                message="No clear cadence evidence at the selection end.",
                start_tick=last.start_tick,
                end_tick=last.start_tick + last.duration_ticks,
                evidence={"degree": degree},
            )
        )
        evidence_counts["cadence_weak"] += 1


def _analyze_tonal_center(
    *,
    spans: Sequence[CompositionV2HarmonyItem],
    notes: Sequence[_NoteOcc],
    active_key: ParsedKey,
    findings: list[CompatibilityFinding],
    evidence_counts: Counter[str],
) -> None:
    support = 0.0
    competing = 0.0
    for span in spans:
        parsed = parse_chord_symbol(span.chord)
        if not parsed.parseable:
            continue
        weight = float(span.duration_ticks)
        degree = (parsed.root_pc - active_key.tonic_pc) % 12
        if degree in {0, 5, 7}:
            support += weight
        elif degree in {1, 3, 6, 8, 10} and parsed.quality in {"dom7", "maj"}:
            # Secondary-dominant / chromatic function — support with chromatic allowance.
            support += weight * 0.7
            evidence_counts["chromatic_function_spans"] += 1
        else:
            competing += weight * 0.4
    pitched = [note for note in notes if note.role not in DRUM_ROLES]
    for note in pitched:
        degree = (note.pc - active_key.tonic_pc) % 12
        if degree in {0, 2, 3, 4, 5, 7, 8, 9, 10, 11}:
            support += note.duration_ticks * 0.15
        else:
            competing += note.duration_ticks * 0.2

    if support <= 0 and competing <= 0:
        return
    margin = (support - competing) / float(support + competing)
    if margin >= 0.15:
        findings.append(
            CompatibilityFinding(
                code="tonal_center_support",
                severity="info",
                message="Evidence supports the active tonal center.",
                evidence={"margin": round(margin, 3)},
            )
        )
        evidence_counts["tonal_support"] += 1
    elif competing > support * 1.25:
        findings.append(
            CompatibilityFinding(
                code="competing_tonal_center",
                severity="warning",
                message="Evidence suggests a competing tonal center.",
                evidence={"margin": round(margin, 3)},
            )
        )
        evidence_counts["tonal_compete"] += 1
        logger.warning(
            "Harmony compatibility competing center",
            extra={"code": "competing_tonal_center"},
        )
    else:
        findings.append(
            CompatibilityFinding(
                code="tonal_center_support",
                severity="info",
                message="Mixed tonal-center evidence without hard contradiction.",
                evidence={"margin": round(margin, 3)},
            )
        )


def _classify_function(
    *,
    parsed: ParsedChord,
    active_key: ParsedKey,
    span_start: int,
    span_end: int,
    findings: list[CompatibilityFinding],
    evidence_counts: Counter[str],
) -> None:
    degree = (parsed.root_pc - active_key.tonic_pc) % 12
    diatonic = _diatonic_pcs(active_key)
    if parsed.quality in {"dom7", "maj"} and degree in {2, 4, 9, 11, 1, 3, 6, 8, 10}:
        findings.append(
            CompatibilityFinding(
                code="secondary_dominant_function",
                severity="info",
                message="Chord reads as secondary-dominant / applied function.",
                start_tick=span_start,
                end_tick=span_end,
                evidence={"degree": degree, "quality": parsed.quality},
            )
        )
        evidence_counts["secondary_dominant"] += 1
    if parsed.root_pc not in diatonic and degree in {1, 3, 6, 8, 10}:
        findings.append(
            CompatibilityFinding(
                code="modal_mixture",
                severity="info",
                message="Chord suggests modal interchange / borrowed color.",
                start_tick=span_start,
                end_tick=span_end,
                evidence={"degree": degree},
            )
        )
        evidence_counts["modal_mixture"] += 1


def _analyze_preservation(
    *,
    source: CompositionV2,
    candidate: CompositionV2,
    range_start: int,
    range_end: int,
    authorized_target_ids: set[str],
    preserve_track_ids: set[str],
    findings: list[CompatibilityFinding],
    evidence_counts: Counter[str],
) -> None:
    source_tracks = {track.id: track for track in source.tracks}
    candidate_tracks = {track.id: track for track in candidate.tracks}
    if set(source_tracks) != set(candidate_tracks):
        findings.append(
            CompatibilityFinding(
                code="structural_corruption",
                severity="error",
                message="Track ID set changed between source and candidate.",
                evidence={
                    "source_track_count": len(source_tracks),
                    "candidate_track_count": len(candidate_tracks),
                },
            )
        )
        evidence_counts["structural_errors"] += 1
        return

    for track_id, source_track in source_tracks.items():
        candidate_track = candidate_tracks[track_id]
        source_events = [_event_fingerprint(event) for event in source_track.events]
        candidate_events = [_event_fingerprint(event) for event in candidate_track.events]
        if track_id in preserve_track_ids and source_events != candidate_events:
            findings.append(
                CompatibilityFinding(
                    code="preservation_violation",
                    severity="error",
                    message="Preserved track events were modified.",
                    track_id=track_id,
                    evidence={"source_event_count": len(source_events), "candidate_event_count": len(candidate_events)},
                )
            )
            evidence_counts["preservation_errors"] += 1
            continue

        # Outside-range events must be identical for every track.
        source_outside = [
            _event_fingerprint(event)
            for event in source_track.events
            if int(event.start_tick) + int(event.duration_ticks) <= range_start
            or int(event.start_tick) >= range_end
        ]
        candidate_outside = [
            _event_fingerprint(event)
            for event in candidate_track.events
            if int(event.start_tick) + int(event.duration_ticks) <= range_start
            or int(event.start_tick) >= range_end
        ]
        if source_outside != candidate_outside:
            findings.append(
                CompatibilityFinding(
                    code="preservation_violation",
                    severity="error",
                    message="Events outside the selection range were modified.",
                    track_id=track_id,
                )
            )
            evidence_counts["preservation_errors"] += 1

        if track_id not in authorized_target_ids and track_id not in preserve_track_ids:
            # Unauthorized audible changes inside the range.
            source_inside = [
                _event_fingerprint(event)
                for event in source_track.events
                if int(event.start_tick) < range_end
                and int(event.start_tick) + int(event.duration_ticks) > range_start
            ]
            candidate_inside = [
                _event_fingerprint(event)
                for event in candidate_track.events
                if int(event.start_tick) < range_end
                and int(event.start_tick) + int(event.duration_ticks) > range_start
            ]
            if source_inside != candidate_inside:
                findings.append(
                    CompatibilityFinding(
                        code="unauthorized_target_change",
                        severity="error",
                        message="Track changed without explicit target authorization.",
                        track_id=track_id,
                    )
                )
                evidence_counts["unauthorized_errors"] += 1

    # Structural metadata identity (IDs / conductor fields).
    if source.key != candidate.key or list(source.key_changes) != list(candidate.key_changes):
        # Allowed only when caller later marks modulation; default treat as error here
        # when comparing candidates unless authorized via empty preserve set + modulation flag.
        # Task 4 will pass preserve checks with modulation awareness; here flag as error.
        if not authorized_target_ids and source.key != candidate.key:
            findings.append(
                CompatibilityFinding(
                    code="structural_corruption",
                    severity="error",
                    message="Root key metadata changed without modulation authorization.",
                )
            )
            evidence_counts["structural_errors"] += 1


def _collect_notes(composition: CompositionV2, start_tick: int, end_tick: int) -> list[_NoteOcc]:
    notes: list[_NoteOcc] = []
    for track in composition.tracks:
        role = str(track.role or "other")
        for event in track.events:
            event_start = int(event.start_tick)
            event_end = event_start + int(event.duration_ticks)
            if event_start >= end_tick or event_end <= start_tick:
                continue
            try:
                midi = midi_pitch_number(event.pitch)
            except Exception:
                continue
            notes.append(
                _NoteOcc(
                    track_id=track.id,
                    role=role,
                    pitch=str(event.pitch),
                    midi=midi,
                    pc=midi % 12,
                    start_tick=event_start,
                    end_tick=event_end,
                    duration_ticks=int(event.duration_ticks),
                )
            )
    return notes


def _event_fingerprint(event: Any) -> tuple[Any, ...]:
    return (
        getattr(event, "type", "note"),
        getattr(event, "id", None),
        str(getattr(event, "pitch", "")),
        int(getattr(event, "start_tick", 0)),
        int(getattr(event, "duration_ticks", 0)),
        int(getattr(event, "velocity", 0) or 0),
        getattr(event, "tie_group", None),
        getattr(event, "tied_from_previous", False),
    )


def _overlap(a0: int, a1: int, b0: int, b1: int) -> int:
    return max(0, min(a1, b1) - max(a0, b0))


def _fallback_triad_pcs(parsed: ParsedChord) -> set[int]:
    if parsed.root_pc < 0:
        return set()
    if parsed.quality in {"min", "min7", "min7b5", "dim", "dim7"}:
        third = 3
    elif parsed.quality == "sus":
        third = 5
    else:
        third = 4
    fifth = 6 if parsed.quality in {"dim", "dim7", "min7b5"} else (8 if parsed.quality == "aug" else 7)
    pcs = {parsed.root_pc % 12, (parsed.root_pc + third) % 12, (parsed.root_pc + fifth) % 12}
    if parsed.bass_pc is not None:
        pcs.add(parsed.bass_pc % 12)
    return pcs


def _diatonic_pcs(key: ParsedKey) -> set[int]:
    scale = (0, 2, 4, 5, 7, 9, 11) if key.mode == "major" else (0, 2, 3, 5, 7, 8, 10)
    return {(key.tonic_pc + interval) % 12 for interval in scale}


def _is_strong_beat(timeline: CompiledTimeline, tick: int) -> bool:
    try:
        bar = timeline.bar_at_tick(tick)
        bar_start = timeline.bar_start_tick(bar)
        bar_end = timeline.bar_end_tick(bar)
    except ValueError:
        return False
    bar_ticks = max(1, bar_end - bar_start)
    pos = tick - bar_start
    # First quarter of the bar ≈ beat 1.
    return pos < bar_ticks * 0.12


def _status_from_findings(findings: Sequence[CompatibilityFinding]) -> CompatibilityStatus:
    if any(item.severity == "error" for item in findings):
        return "incompatible"
    if any(item.severity == "warning" for item in findings):
        return "compatible_with_warnings"
    return "compatible"


def _bounded_counts(counts: Counter[str]) -> dict[str, int]:
    items = sorted(counts.items(), key=lambda item: item[0])[:_MAX_EVIDENCE_KEYS]
    return {key: int(value) for key, value in items}
