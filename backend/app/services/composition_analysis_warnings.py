"""Stable non-blocking musical warning evaluation for composition analysis.

Automatic warnings cover data quality and conspicuous technical conditions only —
not stylistic taste (desired complexity, cadence type, consonance, or repetition).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from app.analysis_schemas import (
    ANALYSIS_MAX_EVIDENCE_ITEMS,
    ANALYSIS_MAX_WARNINGS,
    ANALYSIS_WARNING_CODES,
    AnalysisSourceLocator,
    AnalysisWarning,
    AnalysisWarningCategory,
    AnalysisWarningCode,
    AnalysisWarningSeverity,
    DensityAnalysisResult,
    HarmonyAnalysisResult,
    MelodyAnalysisResult,
    RepetitionAnalysisResult,
    RoleAnalysisResult,
    TonalityAnalysisResult,
    round_analysis_float,
)
from app.composition_schemas import midi_pitch_number
from app.services.composition_analysis_context import (
    AnalysisTrackView,
    CompositionAnalysisContext,
)
from app.services.composition_logical_notes import (
    CollapsedLogicalNote,
    attack_in_interval,
    note_overlaps_interval,
)
from app.services.composition_tonality import (
    CONTRADICTION_SCORE_MARGIN,
    MIN_COMPETITOR_ABS_SCORE,
    MIN_INFERENCE_MASS_QUARTERS,
    MIN_LOCAL_INFERENCE_MASS_QUARTERS,
    parse_key,
)
from app.services.instrument_identity import (
    is_drum_identity,
    normalize_instrument,
    normalize_instrument_identity,
)


logger = logging.getLogger(__name__)

# --- Objective default thresholds (not style preferences) ---------------------

# Dense overlap: conspicuously high concurrency / note load (chords/pads stay under).
DENSE_NOTE_LOAD_THRESHOLD = 8.0
DENSE_MAX_SIMULTANEITY_THRESHOLD = 12

# Exact duplicates within one track (same pitch/start/duration/staff/voice).
EXCESSIVE_DUPLICATE_MIN_COUNT = 2  # group size >= 2 => at least one duplicate pair

# Same-pitch temporal overlap that is not an exact duplicate.
SAME_PITCH_OVERLAP_MIN_PAIRS = 1
SAME_PITCH_OVERLAP_MIN_TICKS = 1

# Timing grid: 16th-note grid; warn when a large share of attacks are off-grid.
TIMING_GRID_DIVISOR = 4  # ticks_per_quarter // 4
TIMING_ANOMALY_MIN_OFF_GRID = 4
TIMING_ANOMALY_MIN_FRACTION = 0.35

# Severity sort rank (higher first).
_SEVERITY_RANK: dict[str, int] = {"error": 0, "warning": 1, "info": 2}


@dataclass(frozen=True)
class WarningSpec:
    """Registry entry for one stable analysis warning code."""

    code: AnalysisWarningCode
    severity: AnalysisWarningSeverity
    category: AnalysisWarningCategory
    message: str


# Closed registry: every ANALYSIS_WARNING_CODES key has policy metadata.
WARNING_REGISTRY: dict[str, WarningSpec] = {
    "note_outside_instrument_range": WarningSpec(
        "note_outside_instrument_range",
        "warning",
        "range",
        ANALYSIS_WARNING_CODES["note_outside_instrument_range"],
    ),
    "dense_overlapping_material": WarningSpec(
        "dense_overlapping_material",
        "warning",
        "density",
        ANALYSIS_WARNING_CODES["dense_overlapping_material"],
    ),
    "empty_analysis_scope": WarningSpec(
        "empty_analysis_scope",
        "info",
        "data_quality",
        ANALYSIS_WARNING_CODES["empty_analysis_scope"],
    ),
    "timing_grid_anomaly": WarningSpec(
        "timing_grid_anomaly",
        "warning",
        "timing",
        ANALYSIS_WARNING_CODES["timing_grid_anomaly"],
    ),
    "overlapping_same_pitch_timing": WarningSpec(
        "overlapping_same_pitch_timing",
        "warning",
        "timing",
        ANALYSIS_WARNING_CODES["overlapping_same_pitch_timing"],
    ),
    "declared_key_conflicts_with_inference": WarningSpec(
        "declared_key_conflicts_with_inference",
        "warning",
        "metadata_conflict",
        ANALYSIS_WARNING_CODES["declared_key_conflicts_with_inference"],
    ),
    "declared_key_change_conflicts_with_inference": WarningSpec(
        "declared_key_change_conflicts_with_inference",
        "warning",
        "metadata_conflict",
        ANALYSIS_WARNING_CODES["declared_key_change_conflicts_with_inference"],
    ),
    "declared_harmony_conflicts_with_inference": WarningSpec(
        "declared_harmony_conflicts_with_inference",
        "warning",
        "metadata_conflict",
        ANALYSIS_WARNING_CODES["declared_harmony_conflicts_with_inference"],
    ),
    "declared_harmony_unparseable": WarningSpec(
        "declared_harmony_unparseable",
        "warning",
        "metadata_conflict",
        ANALYSIS_WARNING_CODES["declared_harmony_unparseable"],
    ),
    "excessive_duplicate_notes": WarningSpec(
        "excessive_duplicate_notes",
        "warning",
        "data_quality",
        ANALYSIS_WARNING_CODES["excessive_duplicate_notes"],
    ),
    "result_truncated": WarningSpec(
        "result_truncated",
        "info",
        "limitation",
        ANALYSIS_WARNING_CODES["result_truncated"],
    ),
    "evidence_truncated": WarningSpec(
        "evidence_truncated",
        "info",
        "limitation",
        ANALYSIS_WARNING_CODES["evidence_truncated"],
    ),
    "melody_skyline_reduction": WarningSpec(
        "melody_skyline_reduction",
        "info",
        "limitation",
        ANALYSIS_WARNING_CODES["melody_skyline_reduction"],
    ),
    "motif_search_truncated": WarningSpec(
        "motif_search_truncated",
        "info",
        "limitation",
        ANALYSIS_WARNING_CODES["motif_search_truncated"],
    ),
    "relative_key_ambiguity": WarningSpec(
        "relative_key_ambiguity",
        "info",
        "ambiguity",
        ANALYSIS_WARNING_CODES["relative_key_ambiguity"],
    ),
    "insufficient_tonal_evidence": WarningSpec(
        "insufficient_tonal_evidence",
        "info",
        "ambiguity",
        ANALYSIS_WARNING_CODES["insufficient_tonal_evidence"],
    ),
    "unsupported_sustain_interpretation": WarningSpec(
        "unsupported_sustain_interpretation",
        "info",
        "limitation",
        ANALYSIS_WARNING_CODES["unsupported_sustain_interpretation"],
    ),
    "percussion_only_scope": WarningSpec(
        "percussion_only_scope",
        "info",
        "limitation",
        ANALYSIS_WARNING_CODES["percussion_only_scope"],
    ),
}

# Practical MIDI ranges keyed by normalized instrument identity (not substrings).
# Role fallback applies only when identity is unknown / unmapped.
_IDENTITY_PRACTICAL_RANGES: dict[str, tuple[int, int]] = {
    "bass": (midi_pitch_number("C1"), midi_pitch_number("C4")),
    "bassoon": (midi_pitch_number("Bb1"), midi_pitch_number("Eb5")),
    "piano": (midi_pitch_number("A0"), midi_pitch_number("C8")),
    "strings": (midi_pitch_number("C2"), midi_pitch_number("C7")),
    "violin": (midi_pitch_number("G3"), midi_pitch_number("C7")),
    "viola": (midi_pitch_number("C3"), midi_pitch_number("A5")),
    "cello": (midi_pitch_number("C2"), midi_pitch_number("C5")),
    "guitar": (midi_pitch_number("E2"), midi_pitch_number("E5")),
    "flute": (midi_pitch_number("C4"), midi_pitch_number("C7")),
    "oboe": (midi_pitch_number("Bb3"), midi_pitch_number("A6")),
    "clarinet": (midi_pitch_number("D3"), midi_pitch_number("Bb6")),
    "sax": (midi_pitch_number("Bb3"), midi_pitch_number("F6")),
    "trumpet": (midi_pitch_number("E3"), midi_pitch_number("C6")),
    "trombone": (midi_pitch_number("E2"), midi_pitch_number("Bb4")),
    "horn": (midi_pitch_number("F2"), midi_pitch_number("F5")),
    "brass": (midi_pitch_number("E2"), midi_pitch_number("C6")),
    "woodwind": (midi_pitch_number("C3"), midi_pitch_number("C7")),
    "organ": (midi_pitch_number("C2"), midi_pitch_number("C7")),
    "harp": (midi_pitch_number("C2"), midi_pitch_number("C7")),
    "choir": (midi_pitch_number("G2"), midi_pitch_number("C6")),
    "pad": (midi_pitch_number("C2"), midi_pitch_number("C7")),
    "synth": (midi_pitch_number("C1"), midi_pitch_number("C8")),
}

_ROLE_PRACTICAL_RANGES: dict[str, tuple[int, int]] = {
    "melody": (midi_pitch_number("C4"), midi_pitch_number("C6")),
    "lead": (midi_pitch_number("C4"), midi_pitch_number("C6")),
    "countermelody": (midi_pitch_number("C4"), midi_pitch_number("C6")),
    "bass": (midi_pitch_number("C1"), midi_pitch_number("C4")),
    "harmony": (midi_pitch_number("C2"), midi_pitch_number("C7")),
    "pad": (midi_pitch_number("C2"), midi_pitch_number("C7")),
    "rhythm": (midi_pitch_number("C2"), midi_pitch_number("C6")),
    "other": (midi_pitch_number("C-1"), midi_pitch_number("G9")),
}


@dataclass(frozen=True)
class WarningEvaluationInput:
    """Analyzer outputs consumed by warning evaluation (pure w.r.t. composition)."""

    context: CompositionAnalysisContext
    tonality: TonalityAnalysisResult
    harmony: HarmonyAnalysisResult
    melody: MelodyAnalysisResult
    density: DensityAnalysisResult
    roles: RoleAnalysisResult
    repetition: RepetitionAnalysisResult
    analyzer_warning_codes: tuple[str, ...] = ()


def evaluate_analysis_warnings(payload: WarningEvaluationInput) -> list[AnalysisWarning]:
    """Evaluate, deduplicate, and stably sort analysis warnings."""
    started_codes: list[str] = []
    warnings: list[AnalysisWarning] = []

    logger.debug(
        "Warning evaluation start",
        extra={
            "scope_kind": payload.context.resolved_scope.kind,
            "threshold_note_load": DENSE_NOTE_LOAD_THRESHOLD,
            "threshold_max_simultaneity": DENSE_MAX_SIMULTANEITY_THRESHOLD,
        },
    )

    warnings.extend(_warn_empty_scopes(payload.context))
    warnings.extend(_warn_instrument_ranges(payload.context))
    warnings.extend(_warn_dense_overlap(payload.context, payload.density))
    warnings.extend(_warn_duplicates(payload.context))
    warnings.extend(_warn_same_pitch_overlap(payload.context))
    warnings.extend(_warn_timing_grid(payload.context))
    warnings.extend(_warn_key_harmony_conflicts(payload.tonality, payload.harmony))
    warnings.extend(
        _warn_limitation_codes(
            payload.context,
            payload.tonality,
            payload.harmony,
            payload.melody,
            payload.roles,
            payload.repetition,
            payload.analyzer_warning_codes,
        )
    )

    aggregated = _aggregate_and_dedupe(warnings)
    ordered = sort_analysis_warnings(aggregated)
    if len(ordered) > ANALYSIS_MAX_WARNINGS:
        logger.warning(
            "Analysis warnings truncated",
            extra={"code": "result_truncated", "kept": ANALYSIS_MAX_WARNINGS},
        )
        ordered = ordered[: ANALYSIS_MAX_WARNINGS - 1]
        ordered.append(_make_warning("result_truncated", details={"kept": ANALYSIS_MAX_WARNINGS}))
        ordered = sort_analysis_warnings(ordered)

    started_codes = [item.code for item in ordered]
    logger.debug(
        "Warning evaluation complete",
        extra={
            "warning_count": len(ordered),
            "warning_codes": started_codes[:32],
        },
    )
    for code in sorted(set(started_codes)):
        logger.warning(
            "Analysis warning emitted",
            extra={"code": code, "count": started_codes.count(code)},
        )
    return ordered


def sort_analysis_warnings(warnings: Sequence[AnalysisWarning]) -> list[AnalysisWarning]:
    """Sort by severity, code, scope kind proxies, then location ticks/indexes."""

    def sort_key(item: AnalysisWarning) -> tuple[Any, ...]:
        loc = item.locator
        return (
            _SEVERITY_RANK.get(item.severity, 9),
            item.code,
            loc.section_index if loc is not None and loc.section_index is not None else 10**9,
            loc.track_index if loc is not None and loc.track_index is not None else 10**9,
            loc.start_tick if loc is not None and loc.start_tick is not None else 10**9,
            loc.end_tick if loc is not None and loc.end_tick is not None else 10**9,
            (loc.track_id or "") if loc is not None else "",
            (loc.section_id or "") if loc is not None else "",
        )

    return sorted(warnings, key=sort_key)


def practical_range_for_track(track: AnalysisTrackView) -> tuple[int, int] | None:
    """Return (low, high) MIDI inclusive for pitched tracks; None for drums/percussion."""
    identity = normalize_instrument_identity(track.instrument)
    if track.is_drum or is_drum_identity(identity):
        return None
    normalized = normalize_instrument(track.instrument)
    if normalized is not None and normalized.is_drum:
        return None
    if identity and identity in _IDENTITY_PRACTICAL_RANGES:
        return _IDENTITY_PRACTICAL_RANGES[identity]
    role = (track.role or "").strip().lower()
    if role in _ROLE_PRACTICAL_RANGES:
        return _ROLE_PRACTICAL_RANGES[role]
    return midi_pitch_number("C2"), midi_pitch_number("C7")


def _make_warning(
    code: AnalysisWarningCode,
    *,
    locator: AnalysisSourceLocator | None = None,
    details: dict[str, Any] | None = None,
    message: str | None = None,
) -> AnalysisWarning:
    spec = WARNING_REGISTRY[code]
    return AnalysisWarning(
        code=code,
        severity=spec.severity,
        category=spec.category,
        message=message or spec.message,
        locator=locator,
        details=details or {},
    )


def _aggregate_and_dedupe(warnings: Iterable[AnalysisWarning]) -> list[AnalysisWarning]:
    """Deduplicate by (code, locator signature); keep first (already deterministic)."""
    seen: set[tuple[Any, ...]] = set()
    out: list[AnalysisWarning] = []
    for warning in warnings:
        loc = warning.locator
        key = (
            warning.code,
            loc.track_id if loc else None,
            loc.track_index if loc else None,
            loc.section_id if loc else None,
            loc.section_index if loc else None,
            loc.start_tick if loc else None,
            loc.end_tick if loc else None,
            loc.start_bar if loc else None,
            # Bound detail fingerprint without freeform dumps.
            tuple(sorted((k, repr(v)[:80]) for k, v in sorted(warning.details.items())[:8])),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(warning)
    return out


def _warn_empty_scopes(context: CompositionAnalysisContext) -> list[AnalysisWarning]:
    warnings: list[AnalysisWarning] = []
    scope = context.resolved_scope
    attacks = context.attacks_in_scope()

    if scope.kind in {"section", "track"} and not attacks:
        warnings.append(
            _make_warning(
                "empty_analysis_scope",
                locator=AnalysisSourceLocator(
                    track_id=scope.track_id,
                    track_index=scope.track_index,
                    section_id=scope.section_id,
                    section_index=scope.section_index,
                    start_tick=scope.start_tick,
                    end_tick=scope.end_tick,
                    start_bar=scope.start_bar,
                    end_bar=max(scope.start_bar, scope.end_bar_exclusive - 1),
                ),
                details={"scope_kind": scope.kind, "attack_count": 0},
            )
        )
        logger.debug(
            "Empty selected scope",
            extra={"code": "empty_analysis_scope", "scope_kind": scope.kind},
        )

    if scope.kind == "composition":
        for index, section in enumerate(context.sections):
            start = section.start_tick
            end = section.start_tick + section.duration_ticks
            section_attacks = 0
            for track in context.tracks:
                for note in track.logical_notes:
                    if attack_in_interval(note, start, end):
                        section_attacks += 1
            if section_attacks == 0:
                warnings.append(
                    _make_warning(
                        "empty_analysis_scope",
                        locator=AnalysisSourceLocator(
                            section_id=section.id,
                            section_index=index,
                            start_tick=start,
                            end_tick=end,
                            start_bar=section.start_bar,
                            end_bar=section.start_bar + section.bar_count - 1,
                        ),
                        details={
                            "scope_kind": "authored_section",
                            "attack_count": 0,
                            "section_index": index,
                        },
                    )
                )
    return warnings


def _warn_instrument_ranges(context: CompositionAnalysisContext) -> list[AnalysisWarning]:
    warnings: list[AnalysisWarning] = []
    scope = context.resolved_scope
    for track in context.tracks:
        if scope.kind == "track" and track.track_id != scope.track_id:
            continue
        bounds = practical_range_for_track(track)
        if bounds is None:
            continue
        low, high = bounds
        identity = normalize_instrument_identity(track.instrument)
        outliers: list[dict[str, Any]] = []
        for note in track.logical_notes:
            if not note_overlaps_interval(note, scope.start_tick, scope.end_tick):
                continue
            if note.midi_number < low or note.midi_number > high:
                outliers.append(
                    {
                        "midi": note.midi_number,
                        "start_tick": note.start_tick,
                        "duration_ticks": note.duration_ticks,
                    }
                )
        if not outliers:
            continue
        evidence = outliers[:ANALYSIS_MAX_EVIDENCE_ITEMS]
        warnings.append(
            _make_warning(
                "note_outside_instrument_range",
                locator=AnalysisSourceLocator(
                    track_id=track.track_id,
                    track_index=track.track_index,
                    start_tick=evidence[0]["start_tick"],
                    end_tick=evidence[0]["start_tick"] + evidence[0]["duration_ticks"],
                ),
                details={
                    "identity": identity,
                    "role": track.role,
                    "allowed_low": low,
                    "allowed_high": high,
                    "outlier_count": len(outliers),
                    "evidence": evidence,
                },
            )
        )
        logger.debug(
            "Instrument range warning",
            extra={
                "code": "note_outside_instrument_range",
                "track_index": track.track_index,
                "outlier_count": len(outliers),
                "identity": identity,
            },
        )
    return warnings


def _warn_dense_overlap(
    context: CompositionAnalysisContext,
    density: DensityAnalysisResult,
) -> list[AnalysisWarning]:
    metrics = density.metrics
    note_load = metrics.note_load
    max_sim = metrics.max_simultaneity
    if note_load is None:
        return []

    load_hit = note_load >= DENSE_NOTE_LOAD_THRESHOLD
    sim_hit = max_sim >= DENSE_MAX_SIMULTANEITY_THRESHOLD
    if not load_hit and not sim_hit:
        logger.debug(
            "Dense-overlap thresholds not met",
            extra={
                "note_load": round_analysis_float(note_load),
                "max_simultaneity": max_sim,
                "threshold_note_load": DENSE_NOTE_LOAD_THRESHOLD,
                "threshold_max_simultaneity": DENSE_MAX_SIMULTANEITY_THRESHOLD,
            },
        )
        return []

    scope = context.resolved_scope
    return [
        _make_warning(
            "dense_overlapping_material",
            locator=AnalysisSourceLocator(
                track_id=scope.track_id,
                track_index=scope.track_index,
                section_id=scope.section_id,
                section_index=scope.section_index,
                start_tick=scope.start_tick,
                end_tick=scope.end_tick,
            ),
            details={
                "note_load_actual": round_analysis_float(note_load),
                "note_load_threshold": DENSE_NOTE_LOAD_THRESHOLD,
                "max_simultaneity_actual": max_sim,
                "max_simultaneity_threshold": DENSE_MAX_SIMULTANEITY_THRESHOLD,
            },
        )
    ]


def _duplicate_key(note: CollapsedLogicalNote) -> tuple[Any, ...]:
    return (
        note.pitch,
        note.start_tick,
        note.duration_ticks,
        note.staff,
        note.voice,
    )


def _warn_duplicates(context: CompositionAnalysisContext) -> list[AnalysisWarning]:
    warnings: list[AnalysisWarning] = []
    scope = context.resolved_scope
    for track in context.tracks:
        if scope.kind == "track" and track.track_id != scope.track_id:
            continue
        counts: dict[tuple[Any, ...], list[CollapsedLogicalNote]] = {}
        for note in track.logical_notes:
            if not attack_in_interval(note, scope.start_tick, scope.end_tick):
                continue
            key = _duplicate_key(note)
            counts.setdefault(key, []).append(note)
        excess_groups = [
            (key, group)
            for key, group in counts.items()
            if len(group) >= EXCESSIVE_DUPLICATE_MIN_COUNT
        ]
        if not excess_groups:
            continue
        excess_groups.sort(key=lambda item: (item[0][1], item[0][0], -len(item[1])))
        total_dupes = sum(len(group) - 1 for _, group in excess_groups)
        evidence = [
            {
                "pitch": key[0],
                "start_tick": key[1],
                "duration_ticks": key[2],
                "count": len(group),
            }
            for key, group in excess_groups[:ANALYSIS_MAX_EVIDENCE_ITEMS]
        ]
        first = excess_groups[0][1][0]
        warnings.append(
            _make_warning(
                "excessive_duplicate_notes",
                locator=AnalysisSourceLocator(
                    track_id=track.track_id,
                    track_index=track.track_index,
                    start_tick=first.start_tick,
                    end_tick=first.end_tick,
                ),
                details={
                    "duplicate_group_count": len(excess_groups),
                    "duplicate_extra_count": total_dupes,
                    "threshold": EXCESSIVE_DUPLICATE_MIN_COUNT,
                    "evidence": evidence,
                },
            )
        )
    return warnings


def _warn_same_pitch_overlap(context: CompositionAnalysisContext) -> list[AnalysisWarning]:
    warnings: list[AnalysisWarning] = []
    scope = context.resolved_scope
    for track in context.tracks:
        if scope.kind == "track" and track.track_id != scope.track_id:
            continue
        notes = [
            note
            for note in track.logical_notes
            if note_overlaps_interval(note, scope.start_tick, scope.end_tick)
        ]
        pairs = 0
        evidence: list[dict[str, Any]] = []
        ordered = sorted(notes, key=lambda n: (n.midi_number, n.start_tick, n.duration_ticks))
        for i, left in enumerate(ordered):
            for right in ordered[i + 1 :]:
                if right.midi_number != left.midi_number:
                    break
                if _duplicate_key(left) == _duplicate_key(right):
                    continue  # exact duplicates handled separately
                overlap = min(left.end_tick, right.end_tick) - max(left.start_tick, right.start_tick)
                if overlap < SAME_PITCH_OVERLAP_MIN_TICKS:
                    continue
                pairs += 1
                if len(evidence) < ANALYSIS_MAX_EVIDENCE_ITEMS:
                    evidence.append(
                        {
                            "midi": left.midi_number,
                            "overlap_ticks": overlap,
                            "left_start": left.start_tick,
                            "right_start": right.start_tick,
                        }
                    )
        if pairs < SAME_PITCH_OVERLAP_MIN_PAIRS:
            continue
        warnings.append(
            _make_warning(
                "overlapping_same_pitch_timing",
                locator=AnalysisSourceLocator(
                    track_id=track.track_id,
                    track_index=track.track_index,
                    start_tick=evidence[0]["left_start"] if evidence else scope.start_tick,
                ),
                details={
                    "pair_count": pairs,
                    "threshold": SAME_PITCH_OVERLAP_MIN_PAIRS,
                    "evidence": evidence,
                },
            )
        )
    return warnings


def _warn_timing_grid(context: CompositionAnalysisContext) -> list[AnalysisWarning]:
    tpq = context.composition.ticks_per_quarter
    grid = max(1, tpq // TIMING_GRID_DIVISOR)
    attacks = context.attacks_in_scope()
    if not attacks:
        return []
    off_grid = 0
    evidence: list[dict[str, Any]] = []
    for track_index, _note_index, note in attacks:
        start_off = note.start_tick % grid != 0
        dur_off = note.duration_ticks % grid != 0
        if not start_off and not dur_off:
            continue
        off_grid += 1
        if len(evidence) < ANALYSIS_MAX_EVIDENCE_ITEMS:
            evidence.append(
                {
                    "track_index": track_index,
                    "start_tick": note.start_tick,
                    "duration_ticks": note.duration_ticks,
                }
            )
    fraction = off_grid / float(len(attacks))
    if off_grid < TIMING_ANOMALY_MIN_OFF_GRID or fraction < TIMING_ANOMALY_MIN_FRACTION:
        logger.debug(
            "Timing grid anomaly thresholds not met",
            extra={
                "off_grid_count": off_grid,
                "attack_count": len(attacks),
                "fraction": round_analysis_float(fraction),
                "grid": grid,
            },
        )
        return []
    scope = context.resolved_scope
    return [
        _make_warning(
            "timing_grid_anomaly",
            locator=AnalysisSourceLocator(
                track_id=scope.track_id,
                track_index=scope.track_index,
                section_id=scope.section_id,
                section_index=scope.section_index,
                start_tick=scope.start_tick,
                end_tick=scope.end_tick,
            ),
            details={
                "grid_ticks": grid,
                "off_grid_count": off_grid,
                "attack_count": len(attacks),
                "off_grid_fraction": round_analysis_float(fraction),
                "min_off_grid": TIMING_ANOMALY_MIN_OFF_GRID,
                "min_fraction": TIMING_ANOMALY_MIN_FRACTION,
                "evidence": evidence,
            },
        )
    ]


def _warn_key_harmony_conflicts(
    tonality: TonalityAnalysisResult,
    harmony: HarmonyAnalysisResult,
) -> list[AnalysisWarning]:
    warnings: list[AnalysisWarning] = []

    global_span = tonality.global_key
    if (
        global_span
        and global_span.inference.status == "ok"
        and global_span.key
        and global_span.declared_key
        and global_span.candidates
    ):
        declared = parse_key(global_span.declared_key)
        inferred = parse_key(global_span.key)
        mass = global_span.inference.evidence.mass or 0.0
        if (
            declared
            and inferred
            and not (declared.tonic_pc == inferred.tonic_pc and declared.mode == inferred.mode)
            and mass >= MIN_INFERENCE_MASS_QUARTERS
        ):
            winner = global_span.candidates[0]
            declared_score = next(
                (
                    item.score
                    for item in global_span.candidates
                    if item.pitch_class == declared.tonic_pc and item.mode == declared.mode
                ),
                0.0,
            )
            if winner.score > 0:
                margin = (winner.score - declared_score) / winner.score
                if winner.score >= MIN_COMPETITOR_ABS_SCORE and margin >= CONTRADICTION_SCORE_MARGIN:
                    warnings.append(
                        _make_warning(
                            "declared_key_conflicts_with_inference",
                            locator=AnalysisSourceLocator(
                                start_tick=global_span.start_tick,
                                end_tick=global_span.end_tick,
                                start_bar=global_span.start_bar,
                                end_bar=max(
                                    global_span.start_bar,
                                    global_span.end_bar_exclusive - 1,
                                ),
                            ),
                            details={
                                "declared_key": global_span.declared_key,
                                "inferred_key": global_span.key,
                                "margin": round_analysis_float(margin),
                                "evidence_mass": round_analysis_float(mass),
                            },
                        )
                    )

    for span in tonality.local_spans:
        if span.inference.status != "ok" or not span.key or not span.declared_key:
            continue
        if not span.candidates:
            continue
        declared = parse_key(span.declared_key)
        inferred = parse_key(span.key)
        mass = span.inference.evidence.mass or 0.0
        if mass < MIN_LOCAL_INFERENCE_MASS_QUARTERS:
            continue
        if not declared or not inferred:
            continue
        if declared.tonic_pc == inferred.tonic_pc and declared.mode == inferred.mode:
            continue
        winner_score = span.candidates[0].score
        declared_score = next(
            (
                item.score
                for item in span.candidates
                if item.pitch_class == declared.tonic_pc and item.mode == declared.mode
            ),
            0.0,
        )
        if winner_score <= 0:
            continue
        margin = (winner_score - declared_score) / winner_score
        if winner_score >= MIN_COMPETITOR_ABS_SCORE and margin >= CONTRADICTION_SCORE_MARGIN:
            warnings.append(
                _make_warning(
                    "declared_key_change_conflicts_with_inference",
                    locator=AnalysisSourceLocator(
                        start_tick=span.start_tick,
                        end_tick=span.end_tick,
                        start_bar=span.start_bar,
                        end_bar=max(span.start_bar, span.end_bar_exclusive - 1),
                    ),
                    details={
                        "declared_key": span.declared_key,
                        "inferred_key": span.key,
                        "margin": round_analysis_float(margin),
                        "start_bar": span.start_bar,
                    },
                )
            )

    if harmony.declared_agreement == "conflict":
        warnings.append(_make_warning("declared_harmony_conflicts_with_inference"))
    elif harmony.declared_agreement == "unparseable":
        warnings.append(_make_warning("declared_harmony_unparseable"))

    return warnings


def _warn_limitation_codes(
    context: CompositionAnalysisContext,
    tonality: TonalityAnalysisResult,
    harmony: HarmonyAnalysisResult,
    melody: MelodyAnalysisResult,
    roles: RoleAnalysisResult,
    repetition: RepetitionAnalysisResult,
    analyzer_warning_codes: Sequence[str],
) -> list[AnalysisWarning]:
    codes: list[str] = list(context.warnings_codes)
    codes.extend(analyzer_warning_codes)

    pitched = context.notes_overlapping_scope(drums=False)
    drums = context.notes_overlapping_scope(drums=True)
    if not pitched and drums:
        codes.append("percussion_only_scope")
    if tonality.inference.status == "insufficient_evidence":
        codes.append("insufficient_tonal_evidence")
    if tonality.inference.status == "ambiguous":
        codes.append("relative_key_ambiguity")
    if any(profile.skyline_reduced for profile in melody.profiles):
        codes.append("melody_skyline_reduction")
    if repetition.inference.status == "truncated":
        codes.append("motif_search_truncated")
    for result in (harmony, melody, roles, repetition, tonality):
        if result.inference.status == "truncated":
            codes.append("result_truncated")
            break

    warnings: list[AnalysisWarning] = []
    seen: set[str] = set()
    for code in codes:
        if code not in WARNING_REGISTRY or code in seen:
            continue
        # Technical empty/range/conflict codes are emitted by dedicated detectors.
        if code in {
            "note_outside_instrument_range",
            "dense_overlapping_material",
            "empty_analysis_scope",
            "timing_grid_anomaly",
            "overlapping_same_pitch_timing",
            "declared_key_conflicts_with_inference",
            "declared_key_change_conflicts_with_inference",
            "declared_harmony_conflicts_with_inference",
            "declared_harmony_unparseable",
            "excessive_duplicate_notes",
        }:
            continue
        seen.add(code)
        warnings.append(_make_warning(code))  # type: ignore[arg-type]
    return warnings
