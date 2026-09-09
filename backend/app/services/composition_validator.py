"""Deterministic musical integrity checks for Composition V1/V2."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal

from pydantic import ValidationError

from ..composition_schemas import (
    ATTACK_ARTICULATIONS,
    CompositionV1,
    CompositionV2,
    CompositionV1NoteEvent,
    CompositionV2NoteEvent,
    GATE_SHORTENING_ARTICULATIONS,
    _midi_pitch_number,
)
from .composition_planner import ValidationDiagnostic
from .composition_timing import bar_duration_ticks


logger = logging.getLogger(__name__)

CompositionLike = CompositionV1 | CompositionV2
NoteEventLike = CompositionV1NoteEvent | CompositionV2NoteEvent
ValidationProfile = Literal["generation", "canonical"]

DEFAULT_TICKS_PER_QUARTER = 480

ROLE_PITCH_RANGES: dict[str, tuple[int, int]] = {
    "melody": (_midi_pitch_number("C4"), _midi_pitch_number("C6")),
    "lead": (_midi_pitch_number("C4"), _midi_pitch_number("C6")),
    "countermelody": (_midi_pitch_number("C4"), _midi_pitch_number("C6")),
    "bass": (_midi_pitch_number("C1"), _midi_pitch_number("C4")),
    "harmony": (_midi_pitch_number("C2"), _midi_pitch_number("C7")),
    # Pads often double cello/viola color; allow ensemble lows.
    "pad": (_midi_pitch_number("C2"), _midi_pitch_number("C7")),
    "rhythm": (_midi_pitch_number("C2"), _midi_pitch_number("C6")),
    # Import-neutral / percussion: full MIDI pitch space.
    "other": (_midi_pitch_number("C-1"), _midi_pitch_number("G9")),
    "drums": (_midi_pitch_number("C-1"), _midi_pitch_number("G9")),
    "percussion": (_midi_pitch_number("C-1"), _midi_pitch_number("G9")),
}

INSTRUMENT_PITCH_RANGES: dict[str, tuple[int, int]] = {
    "bass": (_midi_pitch_number("C1"), _midi_pitch_number("C4")),
    # Generic "strings" covers ensemble writing including cello register.
    "strings": (_midi_pitch_number("C2"), _midi_pitch_number("C7")),
    "violin": (_midi_pitch_number("G3"), _midi_pitch_number("C7")),
    "cello": (_midi_pitch_number("C2"), _midi_pitch_number("C5")),
    "piano": (_midi_pitch_number("C2"), _midi_pitch_number("C7")),
}

ACCOMPANIMENT_ROLES = {"harmony", "pad", "rhythm", "countermelody"}
REQUIRED_MELODY_ROLES = {"melody", "lead"}
REQUIRED_BASS_ROLES = {"bass"}


@dataclass
class CompositionValidationResult:
    ok: bool
    errors: list[ValidationDiagnostic] = field(default_factory=list)
    warnings: list[ValidationDiagnostic] = field(default_factory=list)

    @property
    def diagnostics(self) -> list[ValidationDiagnostic]:
        return [*self.errors, *self.warnings]

    def error_codes(self) -> list[str]:
        return [item.code for item in self.errors]


def validate_composition_integrity(
    music: CompositionLike | dict[str, Any],
    *,
    requested_instruments: Iterable[str] | None = None,
    complexity: str = "moderate",
    profile: ValidationProfile = "generation",
) -> CompositionValidationResult:
    """Run schema plus musical integrity checks; return structured diagnostics.

    Profiles:
    - ``generation``: staged LLM ensemble density, harmony, and grid policies.
    - ``canonical``: structural/timeline integrity only (imports and post-import edits).

    Requested-instrument conformance is owned by generation constraint validation.
    ``requested_instruments`` is accepted for call-site compatibility but does not
    emit ``missing_requested_instrument`` here, avoiding duplicate diagnostics with
    ``constraint_missing_instrument_family`` during staged generation.
    """
    requested = list(requested_instruments or [])
    logger.debug(
        "Starting composition integrity validation",
        extra={
            "profile": profile,
            "requested_instruments": requested,
            "requested_instrument_check": "delegated_to_generation_constraints",
            "complexity": complexity,
            "input_type": type(music).__name__,
        },
    )
    if requested:
        logger.debug(
            "Skipping integrity requested-instrument matching; delegated to generation validator",
            extra={
                "requested_count": len(requested),
                "ownership": "generation_constraints",
            },
        )

    errors: list[ValidationDiagnostic] = []
    warnings: list[ValidationDiagnostic] = []

    try:
        if isinstance(music, (CompositionV1, CompositionV2)):
            composition: CompositionLike = music
        elif isinstance(music, dict) and music.get("schema_version") == "composition.v2":
            composition = CompositionV2.model_validate(music)
        else:
            composition = CompositionV1.model_validate(music)
    except ValidationError as exc:
        diagnostic = ValidationDiagnostic(
            code="schema_invalid",
            message=f"Composition failed schema validation: {exc.error_count()} issue(s)",
            severity="error",
            context={"error_count": exc.error_count()},
        )
        errors.append(diagnostic)
        logger.warning(
            "Composition schema validation failed",
            extra={"code": diagnostic.code, "error_count": exc.error_count()},
        )
        return CompositionValidationResult(ok=False, errors=errors, warnings=warnings)

    _check_boundaries(composition, errors, warnings)
    if profile == "generation":
        _check_tracks_and_density(composition, complexity, errors, warnings)
        _check_timing_grid(composition, errors, warnings)
        _check_harmony_usefulness(composition, errors, warnings)
        _check_bar_overflow(composition, errors, warnings)
    else:
        _check_canonical_tracks(composition, errors, warnings)
    _check_pitch_ranges(composition, errors, warnings)
    if isinstance(composition, CompositionV2):
        _check_v2_expression(composition, errors, warnings)
        _check_v2_motifs(composition, errors, warnings)

    result = CompositionValidationResult(ok=not errors, errors=errors, warnings=warnings)
    if result.ok:
        logger.info(
            "Composition integrity validation passed",
            extra={
                "profile": profile,
                "schema_version": composition.schema_version,
                "bar_count": composition.bar_count,
                "track_count": len(composition.tracks),
                "event_count": sum(len(track.events) for track in composition.tracks),
                "warning_codes": [item.code for item in warnings],
                **_expressive_feature_counts(composition),
            },
        )
    else:
        logger.warning(
            "Composition integrity validation failed",
            extra={
                "profile": profile,
                "schema_version": composition.schema_version,
                "error_codes": result.error_codes(),
                "warning_codes": [item.code for item in warnings],
                "bar_count": composition.bar_count,
                "track_count": len(composition.tracks),
                **_expressive_feature_counts(composition),
            },
        )
    logger.debug(
        "Composition integrity validation category summary",
        extra={
            "profile": profile,
            "error_count": len(errors),
            "warning_count": len(warnings),
            "codes": [item.code for item in result.diagnostics],
        },
    )
    return result


def _check_canonical_tracks(
    composition: CompositionLike,
    errors: list[ValidationDiagnostic],
    warnings: list[ValidationDiagnostic],
) -> None:
    """Structural track checks for imports / post-import edits (no ensemble policy)."""
    if not composition.tracks:
        errors.append(
            ValidationDiagnostic(
                code="missing_required_track",
                message="Composition requires at least one track",
                context={"present_roles": []},
            )
        )
        return
    total_events = sum(len(track.events) for track in composition.tracks)
    if total_events == 0:
        errors.append(
            ValidationDiagnostic(
                code="empty_required_track",
                message="Composition has no playable note events",
                context={"track_count": len(composition.tracks)},
            )
        )
    logger.debug(
        "Canonical track checks complete",
        extra={
            "track_count": len(composition.tracks),
            "event_count": total_events,
            "roles": sorted({track.role for track in composition.tracks}),
        },
    )

def _check_boundaries(
    composition: CompositionLike,
    errors: list[ValidationDiagnostic],
    warnings: list[ValidationDiagnostic],
) -> None:
    if isinstance(composition, CompositionV2):
        try:
            from .composition_timeline import compile_timeline

            compile_timeline(composition)
        except ValueError as exc:
            errors.append(
                ValidationDiagnostic(
                    code="event_out_of_range",
                    message="duration_ticks / meter map failed timeline compilation",
                    context={"reason": type(exc).__name__},
                )
            )
    else:
        expected = composition.bar_count * bar_duration_ticks(
            composition.time_signature, composition.ticks_per_quarter
        )
        if composition.duration_ticks != expected:
            errors.append(
                ValidationDiagnostic(
                    code="event_out_of_range",
                    message="duration_ticks does not match bar_count and meter",
                    context={"duration_ticks": composition.duration_ticks, "expected": expected},
                )
            )

    for track in composition.tracks:
        for event in track.events:
            end_tick = event.start_tick + event.duration_ticks
            if event.start_tick < 0 or end_tick > composition.duration_ticks:
                errors.append(
                    ValidationDiagnostic(
                        code="event_out_of_range",
                        message=f"Event on track {track.id} exceeds composition bounds",
                        context={
                            "track_id": track.id,
                            "start_tick": event.start_tick,
                            "duration_ticks": event.duration_ticks,
                            "duration_ticks_limit": composition.duration_ticks,
                        },
                    )
                )

def _check_tracks_and_density(
    composition: CompositionLike,
    complexity: str,
    errors: list[ValidationDiagnostic],
    warnings: list[ValidationDiagnostic],
) -> None:
    roles = {track.role for track in composition.tracks}
    if not roles & REQUIRED_MELODY_ROLES:
        errors.append(
            ValidationDiagnostic(
                code="missing_required_track",
                message="Composition is missing a required melody/lead track",
                context={"present_roles": sorted(roles)},
            )
        )
    if not roles & REQUIRED_BASS_ROLES:
        errors.append(
            ValidationDiagnostic(
                code="missing_required_track",
                message="Composition is missing a required bass track",
                context={"present_roles": sorted(roles)},
            )
        )
    if not roles & ACCOMPANIMENT_ROLES:
        errors.append(
            ValidationDiagnostic(
                code="missing_required_track",
                message="Composition is missing a required harmony/accompaniment track",
                context={"present_roles": sorted(roles)},
            )
        )

    min_events = _min_events_for_complexity(composition.bar_count, complexity)
    for track in composition.tracks:
        if track.is_drum:
            continue
        if track.role in REQUIRED_MELODY_ROLES | REQUIRED_BASS_ROLES | ACCOMPANIMENT_ROLES:
            track_min_events = _min_events_for_track(composition.bar_count, complexity, track.role)
            if not track.events:
                errors.append(
                    ValidationDiagnostic(
                        code="empty_required_track",
                        message=f"Required track {track.id} ({track.role}) has no note events",
                        context={"track_id": track.id, "role": track.role},
                    )
                )
            elif len(track.events) < track_min_events:
                errors.append(
                    ValidationDiagnostic(
                        code="empty_required_track",
                        message=(
                            f"Required track {track.id} ({track.role}) is too sparse "
                            f"({len(track.events)} events for {composition.bar_count} bars)"
                        ),
                        context={
                            "track_id": track.id,
                            "role": track.role,
                            "event_count": len(track.events),
                            "min_events": track_min_events,
                            "complexity_min_events": min_events,
                        },
                    )
                )

    total_events = sum(len(track.events) for track in composition.tracks)
    if total_events == 0 and composition.harmony:
        errors.append(
            ValidationDiagnostic(
                code="harmony_only",
                message="Harmony metadata cannot substitute for playable note events",
                context={"harmony_count": len(composition.harmony)},
            )
        )


def _check_pitch_ranges(
    composition: CompositionLike,
    errors: list[ValidationDiagnostic],
    warnings: list[ValidationDiagnostic],
) -> None:
    for track in composition.tracks:
        if track.is_drum:
            if track.channel != 10:
                warnings.append(
                    ValidationDiagnostic(
                        code="track_range_semantics",
                        message=f"Drum track {track.id} should use MIDI channel 10",
                        severity="warning",
                        context={"track_id": track.id, "channel": track.channel},
                    )
                )
            continue

        low, high = _pitch_range_for_track(track.role, track.instrument, track.staff)
        for event in track.events:
            try:
                midi = _midi_pitch_number(event.pitch)
            except ValueError:
                errors.append(
                    ValidationDiagnostic(
                        code="event_out_of_range",
                        message=f"Invalid pitch {event.pitch} on track {track.id}",
                        context={"track_id": track.id, "pitch": event.pitch},
                    )
                )
                continue
            if midi < low or midi > high:
                errors.append(
                    ValidationDiagnostic(
                        code="event_out_of_range",
                        message=(
                            f"Pitch {event.pitch} on track {track.id} outside practical "
                            f"range for role/instrument"
                        ),
                        context={
                            "track_id": track.id,
                            "role": track.role,
                            "instrument": track.instrument,
                            "pitch": event.pitch,
                            "midi": midi,
                            "allowed_low": low,
                            "allowed_high": high,
                        },
                    )
                )


def _check_bar_overflow(
    composition: CompositionLike,
    errors: list[ValidationDiagnostic],
    warnings: list[ValidationDiagnostic],
) -> None:
    bar_ticks = bar_duration_ticks(composition.time_signature, composition.ticks_per_quarter)
    for track in composition.tracks:
        if track.is_drum:
            continue
        occupancy: dict[int, int] = {}
        for event in track.events:
            bar_index = event.start_tick // bar_ticks
            local_start = event.start_tick % bar_ticks
            # Count occupied ticks within the starting bar only for overflow detection.
            occupied = min(event.duration_ticks, bar_ticks - local_start)
            occupancy[bar_index] = occupancy.get(bar_index, 0) + occupied
            if local_start + event.duration_ticks > bar_ticks and event.duration_ticks <= bar_ticks:
                # Note that starts in a bar and overflows that bar without being a multi-bar sustain.
                end_tick = event.start_tick + event.duration_ticks
                if end_tick <= composition.duration_ticks and (event.start_tick // bar_ticks) != (
                    (end_tick - 1) // bar_ticks
                ):
                    # Multi-bar sustain is allowed; only flag dense single-bar overflow packing.
                    pass
        for bar_index, occupied in occupancy.items():
            if occupied > bar_ticks * 4:
                # Extremely dense packing likely indicates overlapping overflow mistakes.
                errors.append(
                    ValidationDiagnostic(
                        code="bar_overflow",
                        message=f"Track {track.id} has impossible density in bar {bar_index + 1}",
                        context={
                            "track_id": track.id,
                            "bar": bar_index + 1,
                            "occupied_ticks": occupied,
                            "bar_ticks": bar_ticks,
                        },
                    )
                )
            elif occupied == 0:
                warnings.append(
                    ValidationDiagnostic(
                        code="sparse_bar",
                        message=f"Track {track.id} has an empty bar {bar_index + 1}",
                        severity="warning",
                        context={"track_id": track.id, "bar": bar_index + 1},
                    )
                )


def _check_timing_grid(
    composition: CompositionLike,
    errors: list[ValidationDiagnostic],
    warnings: list[ValidationDiagnostic],
) -> None:
    # Require alignment to a 16th-note grid for staged composer output.
    grid = max(1, composition.ticks_per_quarter // 4)
    for track in composition.tracks:
        for event in track.events:
            if event.start_tick % grid != 0 or event.duration_ticks % grid != 0:
                errors.append(
                    ValidationDiagnostic(
                        code="event_out_of_range",
                        message=f"Event on track {track.id} is not aligned to the rhythmic grid",
                        context={
                            "track_id": track.id,
                            "start_tick": event.start_tick,
                            "duration_ticks": event.duration_ticks,
                            "grid": grid,
                        },
                    )
                )




def _check_harmony_usefulness(
    composition: CompositionLike,
    errors: list[ValidationDiagnostic],
    warnings: list[ValidationDiagnostic],
) -> None:
    if not composition.harmony:
        warnings.append(
            ValidationDiagnostic(
                code="sparse_harmony",
                message="Harmony metadata is empty; chord symbols will be unavailable",
                severity="warning",
            )
        )
        return

    covered_bars = {item.bar for item in composition.harmony}
    section_starts = {section.start_bar for section in composition.sections}
    missing_starts = sorted(section_starts - covered_bars)
    if missing_starts:
        warnings.append(
            ValidationDiagnostic(
                code="sparse_harmony",
                message="Harmony metadata does not cover one or more section starts",
                severity="warning",
                context={"missing_section_start_bars": missing_starts},
            )
        )


def _pitch_range_for_track(role: str, instrument: str, staff: str | None) -> tuple[int, int]:
    instrument_l = instrument.lower()
    if "piano" in instrument_l and staff == "bass":
        return _midi_pitch_number("C2"), _midi_pitch_number("B3")
    if "piano" in instrument_l and staff == "treble":
        return _midi_pitch_number("C4"), _midi_pitch_number("C7")
    for token, bounds in INSTRUMENT_PITCH_RANGES.items():
        if token in instrument_l:
            return bounds
    return ROLE_PITCH_RANGES.get(role, (_midi_pitch_number("C2"), _midi_pitch_number("C7")))


def _min_events_for_complexity(bar_count: int, complexity: str) -> int:
    per_bar = {"simple": 0.5, "moderate": 1.0, "complex": 1.5}.get(complexity, 1.0)
    return max(1, int(bar_count * per_bar))


def _min_events_for_track(bar_count: int, complexity: str, role: str) -> int:
    """Bass often sustains whole/half notes; do not require melody-like density."""
    base = _min_events_for_complexity(bar_count, complexity)
    if role in REQUIRED_BASS_ROLES:
        return max(1, min(base, int(bar_count * 0.5)))
    return base


def _expressive_feature_counts(composition: CompositionLike) -> dict[str, int]:
    if not isinstance(composition, CompositionV2):
        return {
            "tempo_change_count": 0,
            "meter_change_count": 0,
            "key_change_count": 0,
            "marker_count": 0,
            "motif_count": 0,
            "motif_occurrence_count": 0,
            "articulation_note_count": 0,
            "tied_note_count": 0,
        }
    return {
        "tempo_change_count": len(composition.tempo_changes),
        "meter_change_count": len(composition.time_signature_changes),
        "key_change_count": len(composition.key_changes),
        "marker_count": len(composition.markers),
        "motif_count": len(composition.motifs),
        "motif_occurrence_count": sum(len(motif.occurrences) for motif in composition.motifs),
        "articulation_note_count": sum(
            1 for track in composition.tracks for event in track.events if event.articulations
        ),
        "tied_note_count": sum(
            1 for track in composition.tracks for event in track.events if event.tie is not None
        ),
    }


def _check_v2_expression(
    composition: CompositionV2,
    errors: list[ValidationDiagnostic],
    warnings: list[ValidationDiagnostic],
) -> None:
    """Emit structured diagnostics for V2 tie/articulation musical validity.

    Schema validation already rejects most invalid shapes; these checks provide
    stable integrity codes when expressive metadata is present.
    """
    del warnings  # reserved for future non-blocking expression advice
    for track in composition.tracks:
        for event in track.events:
            articulations = list(event.articulations or [])
            names = set(articulations)
            if len(articulations) != len(names):
                errors.append(
                    ValidationDiagnostic(
                        code="contradictory_articulation",
                        message=f"Duplicate articulations on track {track.id}",
                        context={"track_id": track.id, "articulations": articulations},
                    )
                )
            if "staccato" in names and "staccatissimo" in names:
                errors.append(
                    ValidationDiagnostic(
                        code="contradictory_articulation",
                        message="staccato and staccatissimo cannot combine",
                        context={"track_id": track.id, "articulations": articulations},
                    )
                )
            if ("staccato" in names or "staccatissimo" in names) and "tenuto" in names:
                errors.append(
                    ValidationDiagnostic(
                        code="contradictory_articulation",
                        message="short articulations cannot combine with tenuto",
                        context={"track_id": track.id, "articulations": articulations},
                    )
                )
            if "accent" in names and "marcato" in names:
                errors.append(
                    ValidationDiagnostic(
                        code="contradictory_articulation",
                        message="accent and marcato cannot combine",
                        context={"track_id": track.id, "articulations": articulations},
                    )
                )

        groups: dict[str, list[CompositionV2NoteEvent]] = {}
        for event in track.events:
            if event.tie is None:
                continue
            groups.setdefault(event.tie.group_id, []).append(event)

        for group_id, members in groups.items():
            ordered = sorted(members, key=lambda item: (item.start_tick, item.duration_ticks))
            types = [event.tie.type for event in ordered if event.tie is not None]
            if (
                types.count("start") != 1
                or types.count("stop") != 1
                or types[0] != "start"
                or types[-1] != "stop"
            ):
                errors.append(
                    ValidationDiagnostic(
                        code="invalid_tie_chain",
                        message=f"Tie group {group_id} on track {track.id} is musically invalid",
                        context={"track_id": track.id, "group_id": group_id, "types": types},
                    )
                )
                continue

            head = ordered[0]
            for event in ordered:
                if event.pitch != head.pitch or event.staff != head.staff or event.voice != head.voice:
                    errors.append(
                        ValidationDiagnostic(
                            code="invalid_tie_chain",
                            message=f"Tie group {group_id} members must share pitch/staff/voice",
                            context={"track_id": track.id, "group_id": group_id},
                        )
                    )
                    break
                if event is not head and event.articulations:
                    if any(name in GATE_SHORTENING_ARTICULATIONS for name in event.articulations):
                        errors.append(
                            ValidationDiagnostic(
                                code="contradictory_articulation",
                                message="Gate-shortening articulations are not allowed on non-head tied notes",
                                context={"track_id": track.id, "group_id": group_id},
                            )
                        )
                    if any(name in ATTACK_ARTICULATIONS for name in event.articulations):
                        errors.append(
                            ValidationDiagnostic(
                                code="contradictory_articulation",
                                message="Attack articulations are only allowed on the tie chain head",
                                context={"track_id": track.id, "group_id": group_id},
                            )
                        )
                if event is head and any(
                    name in GATE_SHORTENING_ARTICULATIONS for name in event.articulations
                ):
                    errors.append(
                        ValidationDiagnostic(
                            code="contradictory_articulation",
                            message="Gate-shortening articulations are not allowed in a tie chain",
                            context={"track_id": track.id, "group_id": group_id},
                        )
                    )

            for index in range(1, len(ordered)):
                previous = ordered[index - 1]
                current = ordered[index]
                previous_end = previous.start_tick + previous.duration_ticks
                if current.start_tick != previous_end:
                    errors.append(
                        ValidationDiagnostic(
                            code="invalid_tie_chain",
                            message=f"Tie group {group_id} members must be contiguous",
                            context={
                                "track_id": track.id,
                                "group_id": group_id,
                                "previous_end": previous_end,
                                "current_start": current.start_tick,
                            },
                        )
                    )
                    break


def _check_v2_motifs(
    composition: CompositionV2,
    errors: list[ValidationDiagnostic],
    warnings: list[ValidationDiagnostic],
) -> None:
    """Emit stable integrity codes for dangling or inconsistent motif references."""
    del warnings  # reserved for non-blocking motif advice
    if not composition.motifs:
        logger.debug(
            "Canonical motif integrity skipped (empty)",
            extra={"motif_count": 0},
        )
        return

    tracks_by_id = {track.id: track for track in composition.tracks}
    event_locations: dict[str, str] = {}
    for track in composition.tracks:
        for event in track.events:
            if event.id is None:
                continue
            event_locations[event.id] = track.id

    logger.debug(
        "Checking canonical motif integrity",
        extra={
            "motif_count": len(composition.motifs),
            "motif_occurrence_count": sum(len(motif.occurrences) for motif in composition.motifs),
        },
    )

    for motif in composition.motifs:
        originals = [occ for occ in motif.occurrences if occ.relationship == "original"]
        if len(originals) != 1:
            errors.append(
                ValidationDiagnostic(
                    code="motif_original_invalid",
                    message=f"Motif {motif.id} requires exactly one original occurrence",
                    severity="error",
                    context={"motif_id": motif.id, "original_count": len(originals)},
                )
            )
        for occurrence in motif.occurrences:
            track = tracks_by_id.get(occurrence.track_id)
            if track is None:
                errors.append(
                    ValidationDiagnostic(
                        code="motif_track_missing",
                        message=f"Motif occurrence {occurrence.id} references unknown track",
                        severity="error",
                        context={"motif_id": motif.id, "occurrence_id": occurrence.id},
                    )
                )
                continue
            if track.is_drum or track.role in {"drums", "percussion"}:
                errors.append(
                    ValidationDiagnostic(
                        code="motif_percussion_source",
                        message=f"Motif occurrence {occurrence.id} cannot use a percussion track",
                        severity="error",
                        context={
                            "motif_id": motif.id,
                            "occurrence_id": occurrence.id,
                            "track_id": track.id,
                        },
                    )
                )
            for event_id in occurrence.event_ids:
                owner = event_locations.get(event_id)
                if owner is None:
                    errors.append(
                        ValidationDiagnostic(
                            code="motif_event_unresolved",
                            message=f"Motif occurrence {occurrence.id} has dangling event reference",
                            severity="error",
                            context={
                                "motif_id": motif.id,
                                "occurrence_id": occurrence.id,
                                "event_id": event_id,
                            },
                        )
                    )
                elif owner != occurrence.track_id:
                    errors.append(
                        ValidationDiagnostic(
                            code="motif_event_track_mismatch",
                            message=f"Motif occurrence {occurrence.id} event belongs to another track",
                            severity="error",
                            context={
                                "motif_id": motif.id,
                                "occurrence_id": occurrence.id,
                                "event_id": event_id,
                                "track_id": occurrence.track_id,
                                "owner_track_id": owner,
                            },
                        )
                    )

    logger.info(
        "Canonical motif integrity check completed",
        extra={
            "motif_count": len(composition.motifs),
            "error_codes": [item.code for item in errors if item.code.startswith("motif_")],
        },
    )
