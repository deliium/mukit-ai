"""Deterministic musical integrity checks for Composition V1."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable

from pydantic import ValidationError

from ..schemas import Composition, NoteEvent, _midi_pitch_number
from .composition_planner import ValidationDiagnostic
from .composition_timing import bar_duration_ticks


logger = logging.getLogger(__name__)

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
    music: Composition | dict[str, Any],
    *,
    requested_instruments: Iterable[str] | None = None,
    complexity: str = "moderate",
) -> CompositionValidationResult:
    """Run schema plus musical integrity checks; return structured diagnostics."""
    logger.debug(
        "Starting composition integrity validation",
        extra={
            "requested_instruments": list(requested_instruments or []),
            "complexity": complexity,
            "input_type": type(music).__name__,
        },
    )

    errors: list[ValidationDiagnostic] = []
    warnings: list[ValidationDiagnostic] = []

    try:
        composition = music if isinstance(music, Composition) else Composition.model_validate(music)
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
    _check_tracks_and_density(composition, complexity, errors, warnings)
    _check_pitch_ranges(composition, errors, warnings)
    _check_bar_overflow(composition, errors, warnings)
    _check_timing_grid(composition, errors, warnings)
    _check_requested_instruments(composition, list(requested_instruments or []), errors, warnings)
    _check_harmony_usefulness(composition, errors, warnings)

    result = CompositionValidationResult(ok=not errors, errors=errors, warnings=warnings)
    if result.ok:
        logger.info(
            "Composition integrity validation passed",
            extra={
                "bar_count": composition.bar_count,
                "track_count": len(composition.tracks),
                "event_count": sum(len(track.events) for track in composition.tracks),
                "warning_codes": [item.code for item in warnings],
            },
        )
    else:
        logger.warning(
            "Composition integrity validation failed",
            extra={
                "error_codes": result.error_codes(),
                "warning_codes": [item.code for item in warnings],
                "bar_count": composition.bar_count,
                "track_count": len(composition.tracks),
            },
        )
    logger.debug(
        "Composition integrity validation category summary",
        extra={
            "error_count": len(errors),
            "warning_count": len(warnings),
            "codes": [item.code for item in result.diagnostics],
        },
    )
    return result


def _check_boundaries(
    composition: Composition,
    errors: list[ValidationDiagnostic],
    warnings: list[ValidationDiagnostic],
) -> None:
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
    composition: Composition,
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
    composition: Composition,
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
    composition: Composition,
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
    composition: Composition,
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


def _check_requested_instruments(
    composition: Composition,
    requested_instruments: list[str],
    errors: list[ValidationDiagnostic],
    warnings: list[ValidationDiagnostic],
) -> None:
    if not requested_instruments:
        return

    present = " ".join(track.instrument.lower() for track in composition.tracks)
    missing_substantial: list[str] = []
    for instrument in requested_instruments:
        token = instrument.strip().lower()
        if not token:
            continue
        if any(part in token for part in ("drum", "perc")):
            continue
        if token not in present and not any(token in track.instrument.lower() for track in composition.tracks):
            # Treat strings/pad/guitar as substantial optional requests.
            if any(key in token for key in ("string", "pad", "guitar", "violin", "cello", "flute", "sax")):
                missing_substantial.append(instrument)
            elif token not in present:
                missing_substantial.append(instrument)

    for instrument in missing_substantial:
        # Missing optional color instruments warn; missing core piano/bass already covered by roles.
        if any(key in instrument.lower() for key in ("string", "pad", "guitar", "violin", "cello")):
            warnings.append(
                ValidationDiagnostic(
                    code="missing_requested_instrument",
                    message=f"Requested instrument '{instrument}' was not generated",
                    severity="warning",
                    context={"instrument": instrument},
                )
            )
        else:
            errors.append(
                ValidationDiagnostic(
                    code="missing_requested_instrument",
                    message=f"Substantial requested instrument '{instrument}' is missing",
                    context={"instrument": instrument},
                )
            )


def _check_harmony_usefulness(
    composition: Composition,
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
