"""Place one mechanical theme transformation onto a destination composition.

Relative cells stay in memory. This module does not write SQLite and does not
call motif apply, an LLM, or film-score preview.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass

from app.composition_schemas import (
    CompositionV2,
    CompositionV2MotifDefinition,
    CompositionV2MotifOccurrence,
    CompositionV2MotifTransformProvenance,
)
from app.musical_universe_schemas import (
    MECHANICAL_OPERATIONS,
    MUSICAL_UNIVERSE_ERROR_CODES,
    MechanicalOperation,
    MusicalUniverseError,
    MusicalUniverseThemeV1,
    UniverseTransformParameters,
    assert_operation_parameters,
)
from app.services.composition_motif_transform import (
    MotifTransformError,
    extract_relative_motif,
    transform_augmentation,
    transform_diminution,
    transform_inversion,
    transform_repeat,
    transform_sequence,
    transform_transpose,
)
from app.services.composition_motif_transform import build_motif_destination
from app.services.composition_timeline import compile_timeline

logger = logging.getLogger(__name__)

_TRANSFORM_CODES: dict[str, tuple[str, int]] = {
    "motif_destination_out_of_bounds": ("universe_destination_overflow", 422),
    "motif_overlap_rejected": ("universe_destination_overlap", 422),
    "motif_pitch_out_of_range": ("universe_pitch_out_of_range", 422),
    "motif_identity_failed": ("universe_identity_failed", 422),
    "motif_source_unresolved": ("universe_source_missing", 409),
    "motif_destination_unresolved": ("universe_track_missing", 422),
}


@dataclass(frozen=True)
class ThemeRealization:
    composition: CompositionV2
    motif_id: str
    occurrence_id: str
    created_event_count: int
    warning_codes: tuple[str, ...]


def realize_mechanical_theme(
    *,
    universe_id: str,
    theme: MusicalUniverseThemeV1,
    variant_id: str,
    operation: str,
    parameters: UniverseTransformParameters,
    source: CompositionV2,
    destination: CompositionV2,
    same_project: bool,
    destination_track_id: str,
    destination_start_bar: int,
) -> ThemeRealization:
    """Return a new destination composition. The inputs are not mutated."""
    logger.debug(
        "Realizing musical universe theme",
        extra={
            "universe_id": universe_id,
            "theme_id": theme.id,
            "operation": operation,
            "same_project": same_project,
            "destination_start_bar": destination_start_bar,
        },
    )
    if operation not in MECHANICAL_OPERATIONS:
        _refuse("universe_operation_unsupported", 422, operation=operation)
    assert_operation_parameters(operation, parameters)
    if destination_start_bar < 1 or destination_start_bar > destination.bar_count:
        _refuse("universe_destination_overflow", 422)
    track = next((item for item in destination.tracks if item.id == destination_track_id), None)
    if track is None:
        _refuse("universe_track_missing", 422, target_id=destination_track_id)
    if track.is_drum or track.role in {"drums", "percussion"}:
        _refuse("universe_destination_track_rejected", 422, target_id=destination_track_id)

    occurrence = _source_occurrence(source, theme)
    try:
        extracted = extract_relative_motif(
            source,
            track_id=occurrence.track_id,
            event_ids=occurrence.event_ids,
        )
        start_tick = compile_timeline(destination).bar_start_tick(destination_start_bar)
        spec = build_motif_destination(
            destination,
            track_id=destination_track_id,
            start_tick=start_tick,
            anchor_midi=extracted.anchor_midi,
            allow_overlap=False,
        )
        result = _dispatch(
            operation,  # type: ignore[arg-type]
            parameters,
            extracted.notes,
            composition=destination,
            destination=spec,
            id_seed=f"{universe_id}:{theme.id}:{variant_id}:{destination_track_id}:{start_tick}",
        )
    except MotifTransformError as exc:
        code, status = _TRANSFORM_CODES.get(exc.code, ("musical_universe_invalid", 422))
        _refuse(code, status)
    except MusicalUniverseError:
        raise

    if len(result.events) > 32:
        _refuse("universe_transform_too_long", 422, created_event_count=len(result.events))
    event_ids = [event.id for event in result.events if event.id]
    if len(event_ids) != len(result.events):
        _refuse("musical_universe_invalid", 422)

    if same_project:
        motif_id, occurrence_id, motifs = _append_same_project(
            destination,
            theme=theme,
            universe_id=universe_id,
            variant_id=variant_id,
            operation=operation,  # type: ignore[arg-type]
            parameters=parameters,
            event_ids=event_ids,
            track_id=destination_track_id,
        )
    else:
        motif_id, occurrence_id, motifs = _append_other_project(
            destination,
            theme=theme,
            universe_id=universe_id,
            variant_id=variant_id,
            event_ids=event_ids,
            track_id=destination_track_id,
        )

    tracks = []
    for item in destination.tracks:
        if item.id != destination_track_id:
            tracks.append(item)
            continue
        tracks.append(item.model_copy(update={"events": [*item.events, *result.events]}))
    composed = destination.model_copy(update={"tracks": tracks, "motifs": motifs})
    logger.debug(
        "Musical universe theme realized",
        extra={
            "universe_id": universe_id,
            "theme_id": theme.id,
            "operation": operation,
            "created_event_count": len(result.events),
            "motif_id": motif_id,
        },
    )
    return ThemeRealization(
        composition=composed,
        motif_id=motif_id,
        occurrence_id=occurrence_id,
        created_event_count=len(result.events),
        warning_codes=tuple(result.warning_codes)[:8],
    )


def _source_occurrence(source: CompositionV2, theme: MusicalUniverseThemeV1):
    motif = next((item for item in source.motifs if item.id == theme.source.motif_id), None)
    occurrence = None if motif is None else next(
        (item for item in motif.occurrences if item.id == theme.source.occurrence_id),
        None,
    )
    if motif is None or occurrence is None:
        _refuse("universe_source_missing", 409)
    if occurrence.relationship != "original":
        _refuse("universe_theme_source_not_original", 409)
    return occurrence


def _dispatch(operation: MechanicalOperation, parameters, notes, **kwargs):
    if operation == "repeat":
        return transform_repeat(notes, **kwargs)
    if operation == "transpose":
        return transform_transpose(notes, semitones=int(parameters.transpose_semitones), **kwargs)
    if operation == "inversion":
        return transform_inversion(notes, axis_pitch=parameters.inversion_axis_pitch, **kwargs)
    if operation == "augmentation":
        return transform_augmentation(
            notes,
            numerator=int(parameters.time_scale_numerator),
            denominator=int(parameters.time_scale_denominator),
            **kwargs,
        )
    if operation == "diminution":
        return transform_diminution(
            notes,
            numerator=int(parameters.time_scale_numerator),
            denominator=int(parameters.time_scale_denominator),
            **kwargs,
        )
    return transform_sequence(
        notes,
        steps=int(parameters.sequence_steps),
        interval_semitones=int(parameters.sequence_interval_semitones),
        step_ticks=int(parameters.sequence_step_ticks),
        **kwargs,
    )


def _append_same_project(
    destination: CompositionV2,
    *,
    theme: MusicalUniverseThemeV1,
    universe_id: str,
    variant_id: str,
    operation: MechanicalOperation,
    parameters: UniverseTransformParameters,
    event_ids: list[str],
    track_id: str,
) -> tuple[str, str, list[CompositionV2MotifDefinition]]:
    motif = next((item for item in destination.motifs if item.id == theme.source.motif_id), None)
    if motif is None:
        _refuse("universe_source_missing", 409)
    pins = (motif.musical_universe_id, motif.theme_id, motif.variant_id)
    if all(pin is None for pin in pins):
        updated_motif = motif.model_copy(
            update={
                "musical_universe_id": universe_id,
                "theme_id": theme.id,
                "variant_id": variant_id,
            }
        )
    elif pins[0] != universe_id or pins[1] != theme.id:
        _refuse("universe_motif_identity_conflict", 409)
    else:
        updated_motif = motif
    occurrence_id = _fresh("occ_", {item.id for item in updated_motif.occurrences})
    provenance = CompositionV2MotifTransformProvenance(
        operation=operation,
        source_occurrence_id=theme.source.occurrence_id,
        transpose_semitones=parameters.transpose_semitones,
        inversion_axis_pitch=parameters.inversion_axis_pitch,
        time_scale_numerator=parameters.time_scale_numerator,
        time_scale_denominator=parameters.time_scale_denominator,
        sequence_steps=parameters.sequence_steps,
        sequence_interval_semitones=parameters.sequence_interval_semitones,
        sequence_step_ticks=parameters.sequence_step_ticks,
    )
    occurrence = CompositionV2MotifOccurrence(
        id=occurrence_id,
        track_id=track_id,
        event_ids=event_ids,
        relationship=operation,
        transform=provenance,
    )
    replaced = updated_motif.model_copy(update={"occurrences": [*updated_motif.occurrences, occurrence]})
    motifs = [replaced if item.id == motif.id else item for item in destination.motifs]
    return motif.id, occurrence_id, motifs


def _append_other_project(
    destination: CompositionV2,
    *,
    theme: MusicalUniverseThemeV1,
    universe_id: str,
    variant_id: str,
    event_ids: list[str],
    track_id: str,
) -> tuple[str, str, list[CompositionV2MotifDefinition]]:
    motif_id = _fresh("motif_", {item.id for item in destination.motifs})
    occurrence_id = _fresh("occ_", set())
    label = _unique_label(theme.label, {item.label for item in destination.motifs})
    definition = CompositionV2MotifDefinition(
        id=motif_id,
        label=label,
        occurrences=[
            CompositionV2MotifOccurrence(
                id=occurrence_id,
                track_id=track_id,
                event_ids=event_ids,
                relationship="original",
                transform=None,
            )
        ],
        musical_universe_id=universe_id,
        theme_id=theme.id,
        variant_id=variant_id,
    )
    return motif_id, occurrence_id, [*destination.motifs, definition]


def _unique_label(label: str, existing: set[str]) -> str:
    if label not in existing:
        return label
    suffix = 2
    while f"{label} {suffix}" in existing:
        suffix += 1
    candidate = f"{label} {suffix}"
    return candidate[:120]


def _fresh(prefix: str, existing: set[str]) -> str:
    for _ in range(8):
        candidate = f"{prefix}{secrets.token_hex(4)}"
        if candidate not in existing:
            return candidate
    _refuse("musical_universe_invalid", 422)


def _refuse(code: str, status: int, **details: object) -> None:
    if code in {"universe_identity_failed", "universe_source_missing"}:
        logger.warning("Musical universe realization refused", extra={"code": code})
    raise MusicalUniverseError(
        code,
        MUSICAL_UNIVERSE_ERROR_CODES.get(code, "Musical universe reuse failed."),
        http_status=status,
        details={key: value for key, value in details.items() if isinstance(value, (str, int))},
    )
