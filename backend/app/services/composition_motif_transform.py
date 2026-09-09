"""Pure motif extraction, deterministic transforms, and identity verification."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, replace
from fractions import Fraction
from typing import Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.composition_schemas import (
    MOTIF_MAX_EVENT_REFS,
    MOTIF_MIN_EVENT_REFS,
    ArticulationName,
    CompositionV2,
    CompositionV2NoteEvent,
    CompositionV2NoteTie,
    CompositionV2Track,
    MotifRelationshipKind,
    _index_events_by_id,
    _tie_group_members,
    midi_pitch_number,
)
from app.services.composition_melody_analysis import _midi_to_pitch
from app.services.composition_motif_similarity import (
    MotifIdentityVerification,
    verify_exact_inversion,
    verify_exact_repeat,
    verify_exact_sequence_step,
    verify_exact_time_scale,
    verify_exact_transpose,
    verify_motif_identity,
)
from app.services.composition_validator import _pitch_range_for_track


logger = logging.getLogger(__name__)

WARN_QUANTIZED = "motif_quantized_to_grid"
WARN_PITCH_CLAMPED = "motif_pitch_range_rejected"

MAX_SEQUENCE_STEPS = 16
MAX_PROPOSAL_NOTES = MOTIF_MAX_EVENT_REFS


class MotifTransformError(ValueError):
    """Domain failure for motif extraction or transformation."""

    def __init__(self, message: str, *, code: str, context: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.context = context or {}


@dataclass(frozen=True)
class RelativeMotifNote:
    """Compact relative cell resolved from canonical events — not persisted."""

    relative_start_tick: int
    duration_ticks: int
    pitch_semitone_offset: int
    velocity: int
    staff: Literal["treble", "bass"] | None
    voice: int | None
    articulations: tuple[ArticulationName, ...]
    tie_segment_durations: tuple[int, ...] | None = None


@dataclass(frozen=True)
class ExtractedRelativeMotif:
    notes: tuple[RelativeMotifNote, ...]
    anchor_midi: int


@dataclass(frozen=True)
class MotifDestinationSpec:
    track: CompositionV2Track
    start_tick: int
    duration_limit: int
    anchor_midi: int
    existing_event_ids: frozenset[str]
    allow_overlap: bool = False


@dataclass(frozen=True)
class MotifTransformResult:
    events: tuple[CompositionV2NoteEvent, ...]
    verification: MotifIdentityVerification
    warning_codes: tuple[str, ...]
    span_ticks: int


class MotifVariationProposalBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note_count: int = Field(..., ge=MOTIF_MIN_EVENT_REFS, le=MOTIF_MAX_EVENT_REFS)


class RhythmicVariationProposal(MotifVariationProposalBase):
    """Bounded relative timing proposal — no absolute pitches."""

    onset_delta_ticks: list[int] = Field(..., min_length=MOTIF_MIN_EVENT_REFS, max_length=MOTIF_MAX_EVENT_REFS)
    duration_ticks: list[int] = Field(..., min_length=MOTIF_MIN_EVENT_REFS, max_length=MOTIF_MAX_EVENT_REFS)

    @model_validator(mode="after")
    def validate_lengths(self) -> RhythmicVariationProposal:
        if len(self.onset_delta_ticks) != self.note_count or len(self.duration_ticks) != self.note_count:
            raise ValueError("rhythmic proposal arrays must match note_count")
        if self.onset_delta_ticks[0] != 0:
            raise ValueError("first onset_delta_ticks entry must be 0")
        for duration in self.duration_ticks:
            if duration <= 0:
                raise ValueError("duration_ticks entries must be positive")
        return self


class MelodicVariationProposal(MotifVariationProposalBase):
    """Bounded relative pitch proposal — semitone offsets from source."""

    pitch_semitone_offsets: list[int] = Field(
        ...,
        min_length=MOTIF_MIN_EVENT_REFS,
        max_length=MOTIF_MAX_EVENT_REFS,
    )

    @field_validator("pitch_semitone_offsets")
    @classmethod
    def validate_offset_bounds(cls, value: list[int]) -> list[int]:
        for offset in value:
            if offset < -48 or offset > 48:
                raise ValueError("pitch_semitone_offsets must stay within -48..48")
        return value

    @model_validator(mode="after")
    def validate_lengths(self) -> MelodicVariationProposal:
        if len(self.pitch_semitone_offsets) != self.note_count:
            raise ValueError("pitch_semitone_offsets must match note_count")
        return self


class AnswerCounterphraseProposal(MotifVariationProposalBase):
    """Combined melodic + optional rhythmic proposal for answer/counterphrase."""

    pitch_semitone_offsets: list[int] = Field(
        ...,
        min_length=MOTIF_MIN_EVENT_REFS,
        max_length=MOTIF_MAX_EVENT_REFS,
    )
    onset_delta_ticks: list[int] | None = None
    duration_ticks: list[int] | None = None

    @model_validator(mode="after")
    def validate_lengths(self) -> AnswerCounterphraseProposal:
        if len(self.pitch_semitone_offsets) != self.note_count:
            raise ValueError("pitch_semitone_offsets must match note_count")
        if self.onset_delta_ticks is not None:
            if len(self.onset_delta_ticks) != self.note_count:
                raise ValueError("onset_delta_ticks must match note_count")
            if self.onset_delta_ticks[0] != 0:
                raise ValueError("first onset_delta_ticks entry must be 0")
        if self.duration_ticks is not None:
            if len(self.duration_ticks) != self.note_count:
                raise ValueError("duration_ticks must match note_count")
            if any(duration <= 0 for duration in self.duration_ticks):
                raise ValueError("duration_ticks entries must be positive")
        return self


def build_motif_destination(
    composition: CompositionV2,
    *,
    track_id: str,
    start_tick: int,
    anchor_midi: int,
    allow_overlap: bool = False,
    exclude_event_ids: Sequence[str] = (),
) -> MotifDestinationSpec:
    track = next((item for item in composition.tracks if item.id == track_id), None)
    if track is None:
        raise MotifTransformError(
            "Destination track not found",
            code="motif_destination_unresolved",
            context={"track_id": track_id},
        )
    return MotifDestinationSpec(
        track=track,
        start_tick=start_tick,
        duration_limit=composition.duration_ticks,
        anchor_midi=anchor_midi,
        existing_event_ids=frozenset(exclude_event_ids),
        allow_overlap=allow_overlap,
    )


def extract_relative_motif(
    composition: CompositionV2,
    *,
    track_id: str,
    event_ids: Sequence[str],
) -> ExtractedRelativeMotif:
    """Resolve canonical event references into compact relative cells."""
    if len(event_ids) < MOTIF_MIN_EVENT_REFS or len(event_ids) > MOTIF_MAX_EVENT_REFS:
        raise MotifTransformError(
            "Motif source event count out of bounds",
            code="motif_source_event_count",
            context={"event_count": len(event_ids)},
        )

    track = next((item for item in composition.tracks if item.id == track_id), None)
    if track is None:
        raise MotifTransformError(
            "Motif source track not found",
            code="motif_source_unresolved",
            context={"track_id": track_id},
        )
    if track.is_drum or track.role in {"drums", "percussion"}:
        raise MotifTransformError(
            "Motif source must be a pitched non-percussion track",
            code="motif_source_invalid_track",
            context={"track_id": track_id, "role": track.role},
        )

    indexed = _index_events_by_id(composition)
    resolved: list[CompositionV2NoteEvent] = []
    for event_id in event_ids:
        located = indexed.get(event_id)
        if located is None:
            raise MotifTransformError(
                "Motif source references unresolved event id",
                code="motif_source_unresolved",
                context={"event_id": event_id},
            )
        event_track, event = located
        if event_track.id != track_id:
            raise MotifTransformError(
                "Motif source event is on the wrong track",
                code="motif_source_unresolved",
                context={"event_id": event_id, "track_id": track_id},
            )
        resolved.append(event)

    ordered = sorted(resolved, key=lambda event: (event.start_tick, event.duration_ticks, event.id or ""))
    ordered_ids = [event.id for event in ordered if event.id is not None]
    if list(event_ids) != ordered_ids:
        raise MotifTransformError(
            "Motif source event_ids must be chronological",
            code="motif_source_event_order",
        )

    referenced = set(event_ids)
    for event in ordered:
        if event.tie is None:
            continue
        members = _tie_group_members(track, event)
        member_ids = [member.id for member in members]
        if any(member_id is None for member_id in member_ids):
            raise MotifTransformError(
                "Motif source tie chain has missing event ids",
                code="motif_source_incomplete_tie",
            )
        if not set(member_ids).issubset(referenced):
            raise MotifTransformError(
                "Motif source must include complete tie chains",
                code="motif_source_incomplete_tie",
            )

    cells = _collapse_to_relative_cells(track, ordered)
    anchor_midi = midi_pitch_number(ordered[0].pitch)
    notes: list[RelativeMotifNote] = []
    anchor_start = ordered[0].start_tick
    for cell in cells:
        notes.append(
            RelativeMotifNote(
                relative_start_tick=cell["start"] - anchor_start,
                duration_ticks=cell["duration"],
                pitch_semitone_offset=midi_pitch_number(cell["pitch"]) - anchor_midi,
                velocity=cell["velocity"],
                staff=cell["staff"],
                voice=cell["voice"],
                articulations=cell["articulations"],
                tie_segment_durations=cell["tie_segment_durations"],
            )
        )

    logger.debug(
        "Extracted relative motif",
        extra={
            "track_id": track_id,
            "source_event_count": len(event_ids),
            "logical_note_count": len(notes),
        },
    )
    return ExtractedRelativeMotif(notes=tuple(notes), anchor_midi=anchor_midi)


def transform_repeat(
    source: Sequence[RelativeMotifNote],
    *,
    composition: CompositionV2,
    destination: MotifDestinationSpec,
    id_seed: str,
) -> MotifTransformResult:
    return _materialize_transform(
        source,
        transformed=source,
        composition=composition,
        destination=destination,
        operation="repeat",
        id_seed=id_seed,
        variation_strength=None,
        exact_verified=verify_exact_repeat(source, source),
    )


def transform_transpose(
    source: Sequence[RelativeMotifNote],
    *,
    semitones: int,
    composition: CompositionV2,
    destination: MotifDestinationSpec,
    id_seed: str,
) -> MotifTransformResult:
    if semitones < -48 or semitones > 48:
        raise MotifTransformError(
            "Transpose semitones out of bounds",
            code="motif_invalid_parameters",
            context={"transpose_semitones": semitones},
        )
    transformed = tuple(
        replace(note, pitch_semitone_offset=note.pitch_semitone_offset + semitones) for note in source
    )
    return _materialize_transform(
        source,
        transformed=transformed,
        composition=composition,
        destination=destination,
        operation="transpose",
        id_seed=id_seed,
        variation_strength=None,
        exact_verified=verify_exact_transpose(source, transformed, semitones=semitones),
    )


def transform_inversion(
    source: Sequence[RelativeMotifNote],
    *,
    axis_pitch: str | None,
    composition: CompositionV2,
    destination: MotifDestinationSpec,
    id_seed: str,
) -> MotifTransformResult:
    if not source:
        raise MotifTransformError("Motif source is empty", code="motif_source_empty")
    if axis_pitch is None:
        axis_semitone = source[0].pitch_semitone_offset
    else:
        axis_semitone = midi_pitch_number(axis_pitch) - destination.anchor_midi
    transformed = tuple(
        replace(
            note,
            pitch_semitone_offset=axis_semitone - (note.pitch_semitone_offset - axis_semitone),
        )
        for note in source
    )
    return _materialize_transform(
        source,
        transformed=transformed,
        composition=composition,
        destination=destination,
        operation="inversion",
        id_seed=id_seed,
        variation_strength=None,
        exact_verified=verify_exact_inversion(source, transformed, axis_semitone_offset=axis_semitone),
    )


def transform_augmentation(
    source: Sequence[RelativeMotifNote],
    *,
    numerator: int,
    denominator: int,
    composition: CompositionV2,
    destination: MotifDestinationSpec,
    id_seed: str,
) -> MotifTransformResult:
    return _transform_time_scale(
        source,
        numerator=numerator,
        denominator=denominator,
        composition=composition,
        destination=destination,
        operation="augmentation",
        id_seed=id_seed,
    )


def transform_diminution(
    source: Sequence[RelativeMotifNote],
    *,
    numerator: int,
    denominator: int,
    composition: CompositionV2,
    destination: MotifDestinationSpec,
    id_seed: str,
) -> MotifTransformResult:
    return _transform_time_scale(
        source,
        numerator=numerator,
        denominator=denominator,
        composition=composition,
        destination=destination,
        operation="diminution",
        id_seed=id_seed,
    )


def transform_sequence(
    source: Sequence[RelativeMotifNote],
    *,
    steps: int,
    interval_semitones: int,
    step_ticks: int,
    composition: CompositionV2,
    destination: MotifDestinationSpec,
    id_seed: str,
) -> MotifTransformResult:
    if steps < 1 or steps > MAX_SEQUENCE_STEPS:
        raise MotifTransformError(
            "Sequence steps out of bounds",
            code="motif_invalid_parameters",
            context={"sequence_steps": steps},
        )
    if step_ticks <= 0:
        raise MotifTransformError(
            "Sequence step_ticks must be positive",
            code="motif_invalid_parameters",
            context={"sequence_step_ticks": step_ticks},
        )

    expanded: list[RelativeMotifNote] = []
    span = source[-1].relative_start_tick + source[-1].duration_ticks if source else 0
    for step_index in range(steps):
        tick_shift = step_index * (span + step_ticks)
        pitch_shift = step_index * interval_semitones
        for note in source:
            expanded.append(
                replace(
                    note,
                    relative_start_tick=note.relative_start_tick + tick_shift,
                    pitch_semitone_offset=note.pitch_semitone_offset + pitch_shift,
                )
            )
    transformed = tuple(expanded)
    exact = True
    if steps >= 2:
        step_slice = transformed[len(source) : len(source) * 2]
        exact = verify_exact_sequence_step(
            source,
            step_slice,
            interval_semitones=interval_semitones,
            step_ticks=span + step_ticks,
        )
    else:
        exact = verify_exact_repeat(source, transformed)

    return _materialize_transform(
        source,
        transformed=transformed,
        composition=composition,
        destination=destination,
        operation="sequence",
        id_seed=id_seed,
        variation_strength=None,
        exact_verified=exact,
    )


def realize_rhythmic_variation(
    source: Sequence[RelativeMotifNote],
    proposal: RhythmicVariationProposal,
    *,
    composition: CompositionV2,
    destination: MotifDestinationSpec,
    id_seed: str,
    variation_strength: float,
) -> MotifTransformResult:
    _validate_proposal_length(source, proposal.note_count)
    transformed = tuple(
        replace(
            source[index],
            relative_start_tick=proposal.onset_delta_ticks[index],
            duration_ticks=proposal.duration_ticks[index],
        )
        for index in range(len(source))
    )
    return _materialize_transform(
        source,
        transformed=transformed,
        composition=composition,
        destination=destination,
        operation="rhythmic_variation",
        id_seed=id_seed,
        variation_strength=variation_strength,
        exact_verified=None,
    )


def realize_melodic_variation(
    source: Sequence[RelativeMotifNote],
    proposal: MelodicVariationProposal,
    *,
    composition: CompositionV2,
    destination: MotifDestinationSpec,
    id_seed: str,
    variation_strength: float,
) -> MotifTransformResult:
    _validate_proposal_length(source, proposal.note_count)
    transformed = tuple(
        replace(
            source[index],
            pitch_semitone_offset=source[index].pitch_semitone_offset + proposal.pitch_semitone_offsets[index],
        )
        for index in range(len(source))
    )
    return _materialize_transform(
        source,
        transformed=transformed,
        composition=composition,
        destination=destination,
        operation="melodic_variation",
        id_seed=id_seed,
        variation_strength=variation_strength,
        exact_verified=None,
    )


def validate_answer_or_counterphrase(
    source: Sequence[RelativeMotifNote],
    proposal: AnswerCounterphraseProposal,
    *,
    operation: Literal["answer", "counterphrase"],
    composition: CompositionV2,
    destination: MotifDestinationSpec,
    id_seed: str,
    variation_strength: float,
) -> MotifTransformResult:
    _validate_proposal_length(source, proposal.note_count)
    transformed: list[RelativeMotifNote] = []
    for index, note in enumerate(source):
        start = (
            proposal.onset_delta_ticks[index]
            if proposal.onset_delta_ticks is not None
            else note.relative_start_tick
        )
        duration = (
            proposal.duration_ticks[index]
            if proposal.duration_ticks is not None
            else note.duration_ticks
        )
        transformed.append(
            replace(
                note,
                relative_start_tick=start,
                duration_ticks=duration,
                pitch_semitone_offset=note.pitch_semitone_offset + proposal.pitch_semitone_offsets[index],
            )
        )
    return _materialize_transform(
        source,
        transformed=tuple(transformed),
        composition=composition,
        destination=destination,
        operation=operation,
        id_seed=id_seed,
        variation_strength=variation_strength,
        exact_verified=None,
    )


def _transform_time_scale(
    source: Sequence[RelativeMotifNote],
    *,
    numerator: int,
    denominator: int,
    composition: CompositionV2,
    destination: MotifDestinationSpec,
    operation: Literal["augmentation", "diminution"],
    id_seed: str,
) -> MotifTransformResult:
    if numerator < 1 or denominator < 1 or numerator > 8 or denominator > 8:
        raise MotifTransformError(
            "Time scale factors out of bounds",
            code="motif_invalid_parameters",
            context={"numerator": numerator, "denominator": denominator},
        )

    def _scale(value: int) -> int:
        return int(round(value * Fraction(numerator, denominator)))

    transformed = tuple(
        replace(
            note,
            relative_start_tick=_scale(note.relative_start_tick),
            duration_ticks=max(1, _scale(note.duration_ticks)),
            tie_segment_durations=tuple(max(1, _scale(part)) for part in note.tie_segment_durations)
            if note.tie_segment_durations
            else None,
        )
        for note in source
    )
    return _materialize_transform(
        source,
        transformed=transformed,
        composition=composition,
        destination=destination,
        operation=operation,
        id_seed=id_seed,
        variation_strength=None,
        exact_verified=verify_exact_time_scale(
            source,
            transformed,
            numerator=numerator,
            denominator=denominator,
        ),
    )


def _validate_proposal_length(source: Sequence[RelativeMotifNote], note_count: int) -> None:
    if len(source) != note_count:
        raise MotifTransformError(
            "Proposal note_count does not match source motif",
            code="motif_proposal_mismatch",
            context={"source_note_count": len(source), "proposal_note_count": note_count},
        )


def _collapse_to_relative_cells(
    track: CompositionV2Track,
    ordered_events: list[CompositionV2NoteEvent],
) -> list[dict]:
    """Collapse tie chains within ordered source events into logical cells."""
    consumed: set[str] = set()
    cells: list[dict] = []
    for event in ordered_events:
        event_id = event.id
        if event_id is not None and event_id in consumed:
            continue
        if event.tie is None:
            cells.append(
                {
                    "start": event.start_tick,
                    "duration": event.duration_ticks,
                    "pitch": event.pitch,
                    "velocity": event.velocity,
                    "staff": event.staff,
                    "voice": event.voice,
                    "articulations": tuple(event.articulations),
                    "tie_segment_durations": None,
                }
            )
            if event_id:
                consumed.add(event_id)
            continue

        members = _tie_group_members(track, event)
        for member in members:
            if member.id:
                consumed.add(member.id)
        head = members[0]
        cells.append(
            {
                "start": head.start_tick,
                "duration": sum(member.duration_ticks for member in members),
                "pitch": head.pitch,
                "velocity": head.velocity,
                "staff": head.staff,
                "voice": head.voice,
                "articulations": tuple(head.articulations),
                "tie_segment_durations": tuple(member.duration_ticks for member in members),
            }
        )
    return cells


def _rhythmic_grid(composition: CompositionV2) -> int:
    return max(1, composition.ticks_per_quarter // 4)


def _quantize_tick(value: int, grid: int) -> tuple[int, bool]:
    quantized = int(round(value / grid)) * grid
    return max(0, quantized), quantized != value


def _materialize_transform(
    source: Sequence[RelativeMotifNote],
    *,
    transformed: Sequence[RelativeMotifNote],
    composition: CompositionV2,
    destination: MotifDestinationSpec,
    operation: MotifRelationshipKind,
    id_seed: str,
    variation_strength: float | None,
    exact_verified: bool | None,
) -> MotifTransformResult:
    if not transformed:
        raise MotifTransformError("Transformed motif is empty", code="motif_transform_empty")

    grid = _rhythmic_grid(composition)
    warnings: list[str] = []
    low, high = _pitch_range_for_track(
        destination.track.role,
        destination.track.instrument,
        destination.track.staff,
    )

    span = max(note.relative_start_tick + note.duration_ticks for note in transformed)
    dest_end = destination.start_tick + span
    if destination.start_tick < 0 or dest_end > destination.duration_limit:
        logger.error(
            "Motif destination out of bounds",
            extra={
                "code": "motif_destination_out_of_bounds",
                "operation": operation,
                "start_tick": destination.start_tick,
                "span_ticks": span,
                "duration_limit": destination.duration_limit,
            },
        )
        raise MotifTransformError(
            "Destination span exceeds composition bounds",
            code="motif_destination_out_of_bounds",
            context={
                "start_tick": destination.start_tick,
                "span_ticks": span,
                "duration_limit": destination.duration_limit,
            },
        )

    if not destination.allow_overlap:
        for event in destination.track.events:
            if event.id and event.id in destination.existing_event_ids:
                continue
            event_end = event.start_tick + event.duration_ticks
            if event.start_tick < dest_end and event_end > destination.start_tick:
                logger.error(
                    "Motif destination overlap rejected",
                    extra={
                        "code": "motif_overlap_rejected",
                        "operation": operation,
                        "start_tick": destination.start_tick,
                        "span_ticks": span,
                    },
                )
                raise MotifTransformError(
                    "Destination overlaps existing events",
                    code="motif_overlap_rejected",
                    context={"start_tick": destination.start_tick, "span_ticks": span},
                )

    used_ids = {event.id for event in destination.track.events if event.id}
    used_ids.update(destination.existing_event_ids)
    anchor_midi = destination.anchor_midi
    events: list[CompositionV2NoteEvent] = []
    note_index = 0

    for note in transformed:
        start, start_quantized = _quantize_tick(destination.start_tick + note.relative_start_tick, grid)
        duration, duration_quantized = _quantize_tick(note.duration_ticks, grid)
        if start_quantized or duration_quantized:
            if WARN_QUANTIZED not in warnings:
                warnings.append(WARN_QUANTIZED)
                logger.warning(
                    "Motif transform quantized to rhythmic grid",
                    extra={"code": WARN_QUANTIZED, "operation": operation, "grid": grid},
                )
        if duration <= 0:
            duration = grid

        midi = anchor_midi + note.pitch_semitone_offset
        if midi < 0 or midi > 127:
            logger.error(
                "Motif pitch overflow",
                extra={"code": "motif_pitch_out_of_range", "operation": operation, "midi": midi},
            )
            raise MotifTransformError(
                "Transformed pitch exceeds MIDI range",
                code="motif_pitch_out_of_range",
                context={"midi": midi},
            )
        if midi < low or midi > high:
            logger.error(
                "Motif pitch outside track practical range",
                extra={
                    "code": WARN_PITCH_CLAMPED,
                    "operation": operation,
                    "allowed_low": low,
                    "allowed_high": high,
                },
            )
            raise MotifTransformError(
                "Transformed pitch outside destination track range",
                code="motif_pitch_out_of_range",
                context={"midi": midi, "allowed_low": low, "allowed_high": high},
            )

        tie_segments = note.tie_segment_durations
        if tie_segments and len(tie_segments) > 1:
            scaled_segments = tie_segments
            if operation in {"augmentation", "diminution"}:
                pass  # already scaled in transform
            group_id = _deterministic_tie_group_id(id_seed, note_index, operation)
            segment_start = start
            for seg_index, segment_duration in enumerate(scaled_segments):
                seg_duration, seg_quantized = _quantize_tick(segment_duration, grid)
                if seg_quantized and WARN_QUANTIZED not in warnings:
                    warnings.append(WARN_QUANTIZED)
                if seg_duration <= 0:
                    seg_duration = grid
                tie_type: Literal["start", "continue", "stop"]
                if seg_index == 0:
                    tie_type = "start"
                elif seg_index == len(scaled_segments) - 1:
                    tie_type = "stop"
                else:
                    tie_type = "continue"
                event_id = _allocate_event_id(
                    id_seed=id_seed,
                    operation=operation,
                    note_index=note_index,
                    segment_index=seg_index,
                    used_ids=used_ids,
                )
                segment_articulations = list(note.articulations) if seg_index == 0 else []
                events.append(
                    CompositionV2NoteEvent(
                        pitch=_midi_to_pitch(midi),
                        start_tick=segment_start,
                        duration_ticks=seg_duration,
                        velocity=note.velocity,
                        id=event_id,
                        staff=note.staff,
                        voice=note.voice,
                        articulations=segment_articulations,
                        tie=CompositionV2NoteTie(group_id=group_id, type=tie_type),
                    )
                )
                segment_start += seg_duration
        else:
            event_id = _allocate_event_id(
                id_seed=id_seed,
                operation=operation,
                note_index=note_index,
                segment_index=0,
                used_ids=used_ids,
            )
            events.append(
                CompositionV2NoteEvent(
                    pitch=_midi_to_pitch(midi),
                    start_tick=start,
                    duration_ticks=duration,
                    velocity=note.velocity,
                    id=event_id,
                    staff=note.staff,
                    voice=note.voice,
                    articulations=list(note.articulations),
                )
            )
        note_index += 1

    verification = verify_motif_identity(
        source,
        transformed,
        operation=operation,
        variation_strength=variation_strength,
        exact_transform_verified=exact_verified,
    )
    if not verification.passed:
        logger.error(
            "Motif transform failed identity verification",
            extra={"code": "motif_identity_failed", "operation": operation},
        )
        raise MotifTransformError(
            "Motif transform failed identity verification",
            code="motif_identity_failed",
            context={"operation": operation, "warning_codes": list(verification.warning_codes)},
        )

    logger.info(
        "Motif transform succeeded",
        extra={
            "operation": operation,
            "event_count": len(events),
            "combined_score": verification.components.combined_score,
        },
    )
    return MotifTransformResult(
        events=tuple(events),
        verification=verification,
        warning_codes=tuple(warnings),
        span_ticks=span,
    )


def _allocate_event_id(
    *,
    id_seed: str,
    operation: str,
    note_index: int,
    segment_index: int,
    used_ids: set[str],
) -> str:
    digest = hashlib.sha256(
        f"{id_seed}|{operation}|{note_index}|{segment_index}".encode("utf-8")
    ).hexdigest()[:12]
    candidate = f"motif-{operation}-{digest}"
    suffix = 2
    while candidate in used_ids:
        candidate = f"motif-{operation}-{digest}-{suffix}"
        suffix += 1
    used_ids.add(candidate)
    return candidate


def _deterministic_tie_group_id(id_seed: str, note_index: int, operation: str) -> str:
    digest = hashlib.sha256(f"{id_seed}|tie|{operation}|{note_index}".encode("utf-8")).hexdigest()[:10]
    return f"motif-tie-{digest}"


__all__ = [
    "AnswerCounterphraseProposal",
    "ExtractedRelativeMotif",
    "MelodicVariationProposal",
    "MotifDestinationSpec",
    "MotifTransformError",
    "MotifTransformResult",
    "RelativeMotifNote",
    "RhythmicVariationProposal",
    "build_motif_destination",
    "extract_relative_motif",
    "realize_melodic_variation",
    "realize_rhythmic_variation",
    "transform_augmentation",
    "transform_diminution",
    "transform_inversion",
    "transform_repeat",
    "transform_sequence",
    "transform_transpose",
    "validate_answer_or_counterphrase",
]
