"""Typed motif-application API contracts for POST /motifs/apply."""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.composition_schemas import (
    CompositionV2,
    CompositionV2MotifTransformProvenance,
    midi_pitch_number,
)
from app.schemas import LLMGenerationOptions, LLMModelSelection
from app.services.composition_motif_similarity import CREATIVE_OPERATIONS, MECHANICAL_OPERATIONS


logger = logging.getLogger(__name__)

MotifApplyOperation = Literal[
    "repeat",
    "transpose",
    "rhythmic_variation",
    "melodic_variation",
    "inversion",
    "augmentation",
    "diminution",
    "sequence",
    "answer",
    "counterphrase",
]

MOTIF_APPLY_OPERATIONS: frozenset[str] = frozenset(
    {
        "repeat",
        "transpose",
        "rhythmic_variation",
        "melodic_variation",
        "inversion",
        "augmentation",
        "diminution",
        "sequence",
        "answer",
        "counterphrase",
    }
)

# Bounded request guards (checked before any provider invocation).
MOTIF_APPLY_MAX_EVENT_COUNT = 50_000
MOTIF_APPLY_MAX_MOTIF_DEFINITIONS = 128
MOTIF_APPLY_MAX_MOTIF_OCCURRENCES = 512
MOTIF_APPLY_MAX_CREATED_EVENT_IDS = 256

MOTIF_ERROR_CODES: dict[str, str] = {
    "motif_request_too_large": "Composition or motif payload exceeds configured apply limits.",
    "motif_source_unresolved": "Motif or source occurrence could not be resolved in the composition.",
    "motif_destination_unresolved": "Destination section or track could not be resolved.",
    "motif_destination_invalid_track": "Destination track must be pitched and non-percussion.",
    "motif_destination_out_of_bounds": "Destination placement exceeds composition bounds.",
    "motif_invalid_parameters": "Operation parameters are missing or inconsistent with the operation.",
    "motif_invalid_operation": "Operation is not supported for motif apply.",
    "motif_overlap_rejected": "Destination overlaps existing events and replacement was rejected.",
    "motif_boundary_crossing_note": "Destination span intersects a note that continues outside the span.",
    "motif_boundary_crossing_tie_chain": "Destination span intersects a tie chain that continues outside the span.",
    "motif_source_protection": "Destination would replace events from the original motif occurrence.",
    "motif_identity_failed": "Transformed material failed identity verification.",
    "motif_pitch_out_of_range": "Transformed pitch exceeds destination track range.",
    "motif_creative_provider_required": "Creative motif operations require LLM provider selection.",
    "motif_creative_unavailable": "Creative motif editing is not available yet.",
    "motif_internal_error": "Unexpected motif apply failure (sanitized).",
}

MotifApplyErrorCode = Literal[
    "motif_request_too_large",
    "motif_source_unresolved",
    "motif_destination_unresolved",
    "motif_destination_invalid_track",
    "motif_destination_out_of_bounds",
    "motif_invalid_parameters",
    "motif_invalid_operation",
    "motif_overlap_rejected",
    "motif_boundary_crossing_note",
    "motif_boundary_crossing_tie_chain",
    "motif_source_protection",
    "motif_identity_failed",
    "motif_pitch_out_of_range",
    "motif_creative_provider_required",
    "motif_creative_unavailable",
    "motif_internal_error",
]


class MotifApplyError(Exception):
    """Domain error for motif apply failures mapped to structured HTTP responses."""

    def __init__(
        self,
        code: MotifApplyErrorCode,
        message: str,
        *,
        http_status: int = 422,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.details = details or {}
        logger.error(
            "Motif apply domain error",
            extra={
                "error_code": code,
                "http_status": http_status,
                "detail_keys": sorted(self.details.keys()),
            },
        )


class MotifSourceSelector(BaseModel):
    """Canonical motif source: definition id + occurrence id."""

    model_config = ConfigDict(extra="forbid")

    motif_id: str = Field(..., min_length=1, max_length=120)
    occurrence_id: str = Field(..., min_length=1, max_length=120)


class MotifDestinationSelector(BaseModel):
    """Destination section/track and bar-aligned start position."""

    model_config = ConfigDict(extra="forbid")

    section_id: str | None = Field(default=None, min_length=1, max_length=120)
    track_id: str = Field(..., min_length=1, max_length=80)
    start_bar: int = Field(..., ge=1)
    start_tick: int | None = Field(default=None, ge=0)


class MotifOperationParameters(BaseModel):
    """Operation-specific parameters; only fields relevant to the chosen operation are required."""

    model_config = ConfigDict(extra="forbid")

    transpose_semitones: int | None = Field(default=None, ge=-48, le=48)
    inversion_axis_pitch: str | None = Field(default=None, min_length=2, max_length=5)
    time_scale_numerator: int | None = Field(default=None, ge=1, le=8)
    time_scale_denominator: int | None = Field(default=None, ge=1, le=8)
    sequence_steps: int | None = Field(default=None, ge=1, le=16)
    sequence_interval_semitones: int | None = Field(default=None, ge=-24, le=24)
    sequence_step_ticks: int | None = Field(default=None, gt=0)

    @field_validator("inversion_axis_pitch")
    @classmethod
    def validate_inversion_axis_pitch(cls, value: str | None) -> str | None:
        if value is None:
            return value
        pitch = value.strip()
        midi_pitch_number(pitch)
        return pitch

    @model_validator(mode="after")
    def validate_time_scale_pair(self) -> MotifOperationParameters:
        has_num = self.time_scale_numerator is not None
        has_den = self.time_scale_denominator is not None
        if has_num != has_den:
            raise ValueError("time_scale_numerator and time_scale_denominator must be set together")
        return self


class MotifApplyDiagnostics(BaseModel):
    """Bounded operation diagnostics returned to clients."""

    model_config = ConfigDict(extra="forbid")

    source_event_count: int = Field(..., ge=0)
    created_event_count: int = Field(..., ge=0)
    replaced_event_count: int = Field(..., ge=0)
    destination_span_ticks: int = Field(..., ge=0)
    identity_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    exact_transform_verified: bool | None = None
    warning_codes: list[str] = Field(default_factory=list, max_length=32)


class MotifApplyOperationResult(BaseModel):
    """Resolved apply outcome — references created ids, never note payloads."""

    model_config = ConfigDict(extra="forbid")

    motif_id: str = Field(..., min_length=1, max_length=120)
    source_occurrence_id: str = Field(..., min_length=1, max_length=120)
    destination_section_id: str | None = Field(default=None, min_length=1, max_length=120)
    destination_track_id: str = Field(..., min_length=1, max_length=80)
    destination_start_bar: int = Field(..., ge=1)
    destination_start_tick: int = Field(..., ge=0)
    created_event_ids: list[str] = Field(..., min_length=1, max_length=MOTIF_APPLY_MAX_CREATED_EVENT_IDS)
    new_occurrence_id: str = Field(..., min_length=1, max_length=120)
    relationship: MotifApplyOperation
    identity_score: float = Field(..., ge=0.0, le=1.0)
    provider: Literal["openai", "deepseek", "fake"] | None = None
    model: str | None = Field(default=None, max_length=120)
    transform: CompositionV2MotifTransformProvenance
    diagnostics: MotifApplyDiagnostics


class MotifApplyRequest(BaseModel):
    """Apply a motif transformation to a destination section/track."""

    model_config = ConfigDict(extra="forbid")

    composition: CompositionV2
    source: MotifSourceSelector
    destination: MotifDestinationSelector
    operation: MotifApplyOperation
    parameters: MotifOperationParameters = Field(default_factory=MotifOperationParameters)
    variation_strength: float | None = Field(default=None, ge=0.0, le=1.0)
    selection: LLMModelSelection = Field(default_factory=LLMModelSelection)
    options: LLMGenerationOptions = Field(default_factory=LLMGenerationOptions)

    @model_validator(mode="after")
    def validate_operation_contract(self) -> MotifApplyRequest:
        operation = self.operation
        params = self.parameters
        strength = self.variation_strength

        if operation in MECHANICAL_OPERATIONS:
            if strength is not None:
                raise ValueError("variation_strength is not applicable for mechanical motif operations")
            if self.selection.provider is not None or self.selection.model is not None:
                raise ValueError("LLM selection must be omitted for mechanical motif operations")
        elif operation in CREATIVE_OPERATIONS:
            if strength is None:
                raise ValueError("variation_strength is required for creative motif operations")
            if self.selection.provider is None and self.selection.model is None:
                raise ValueError("LLM provider or model selection is required for creative motif operations")
        else:
            raise ValueError(f"Unsupported motif apply operation: {operation}")

        if operation == "transpose" and params.transpose_semitones is None:
            raise ValueError("transpose_semitones is required for transpose")
        if operation == "augmentation":
            if params.time_scale_numerator is None or params.time_scale_denominator is None:
                raise ValueError("time_scale_numerator and time_scale_denominator are required for augmentation")
        if operation == "diminution":
            if params.time_scale_numerator is None or params.time_scale_denominator is None:
                raise ValueError("time_scale_numerator and time_scale_denominator are required for diminution")
        if operation == "sequence":
            missing = [
                name
                for name, value in (
                    ("sequence_steps", params.sequence_steps),
                    ("sequence_interval_semitones", params.sequence_interval_semitones),
                    ("sequence_step_ticks", params.sequence_step_ticks),
                )
                if value is None
            ]
            if missing:
                raise ValueError(f"{', '.join(missing)} required for sequence")

        if operation in {"repeat", "inversion", "rhythmic_variation", "melodic_variation", "answer", "counterphrase"}:
            forbidden = []
            if operation != "transpose" and params.transpose_semitones is not None:
                forbidden.append("transpose_semitones")
            if operation != "inversion" and params.inversion_axis_pitch is not None:
                forbidden.append("inversion_axis_pitch")
            if operation not in {"augmentation", "diminution"} and (
                params.time_scale_numerator is not None or params.time_scale_denominator is not None
            ):
                forbidden.extend(["time_scale_numerator", "time_scale_denominator"])
            if operation != "sequence" and any(
                value is not None
                for value in (
                    params.sequence_steps,
                    params.sequence_interval_semitones,
                    params.sequence_step_ticks,
                )
            ):
                forbidden.extend(
                    ["sequence_steps", "sequence_interval_semitones", "sequence_step_ticks"]
                )
            if forbidden:
                raise ValueError(
                    f"Unexpected parameters for {operation}: {', '.join(sorted(set(forbidden)))}"
                )

        if self.destination.start_bar > self.composition.bar_count:
            raise ValueError("destination.start_bar exceeds composition bar_count")

        known_tracks = {track.id for track in self.composition.tracks}
        if self.destination.track_id not in known_tracks:
            raise ValueError("destination.track_id must exist in the composition")

        if self.source.motif_id:
            motif_ids = {motif.id for motif in self.composition.motifs}
            if self.composition.motifs and self.source.motif_id not in motif_ids:
                raise ValueError("source.motif_id must exist in composition.motifs")

        return self


class MotifApplyResponse(BaseModel):
    """Validated canonical composition plus bounded apply metadata and MusicXML preview."""

    model_config = ConfigDict(extra="forbid")

    composition: CompositionV2
    result: MotifApplyOperationResult
    warnings: list[str] = Field(default_factory=list, max_length=64)
    musicxml: str | None = None
    musicxml_filename: str | None = Field(default=None, max_length=160)


def count_composition_events(composition: CompositionV2) -> int:
    return sum(len(track.events) for track in composition.tracks)


def count_motif_occurrences(composition: CompositionV2) -> int:
    return sum(len(motif.occurrences) for motif in composition.motifs)


def validate_motif_apply_request_limits(request: MotifApplyRequest) -> None:
    """Reject oversized compositions before provider invocation."""
    event_count = count_composition_events(request.composition)
    motif_count = len(request.composition.motifs)
    occurrence_count = count_motif_occurrences(request.composition)

    if event_count > MOTIF_APPLY_MAX_EVENT_COUNT:
        raise MotifApplyError(
            "motif_request_too_large",
            "Composition event count exceeds motif apply limit",
            details={
                "event_count": event_count,
                "limit": MOTIF_APPLY_MAX_EVENT_COUNT,
            },
        )
    if motif_count > MOTIF_APPLY_MAX_MOTIF_DEFINITIONS:
        raise MotifApplyError(
            "motif_request_too_large",
            "Motif definition count exceeds motif apply limit",
            details={"motif_count": motif_count, "limit": MOTIF_APPLY_MAX_MOTIF_DEFINITIONS},
        )
    if occurrence_count > MOTIF_APPLY_MAX_MOTIF_OCCURRENCES:
        raise MotifApplyError(
            "motif_request_too_large",
            "Motif occurrence count exceeds motif apply limit",
            details={
                "occurrence_count": occurrence_count,
                "limit": MOTIF_APPLY_MAX_MOTIF_OCCURRENCES,
            },
        )

    logger.debug(
        "Motif apply request limits validated",
        extra={
            "event_count": event_count,
            "motif_count": motif_count,
            "occurrence_count": occurrence_count,
            "operation": request.operation,
        },
    )


def build_transform_provenance(
    operation: MotifApplyOperation,
    *,
    source_occurrence_id: str,
    parameters: MotifOperationParameters,
    variation_strength: float | None,
) -> CompositionV2MotifTransformProvenance:
    return CompositionV2MotifTransformProvenance(
        operation=operation,  # type: ignore[arg-type]
        variation_strength=variation_strength,
        source_occurrence_id=source_occurrence_id,
        transpose_semitones=parameters.transpose_semitones,
        inversion_axis_pitch=parameters.inversion_axis_pitch,
        time_scale_numerator=parameters.time_scale_numerator,
        time_scale_denominator=parameters.time_scale_denominator,
        sequence_steps=parameters.sequence_steps,
        sequence_interval_semitones=parameters.sequence_interval_semitones,
        sequence_step_ticks=parameters.sequence_step_ticks,
    )


__all__ = [
    "CREATIVE_OPERATIONS",
    "MECHANICAL_OPERATIONS",
    "MOTIF_APPLY_MAX_CREATED_EVENT_IDS",
    "MOTIF_APPLY_MAX_EVENT_COUNT",
    "MOTIF_APPLY_MAX_MOTIF_DEFINITIONS",
    "MOTIF_APPLY_MAX_MOTIF_OCCURRENCES",
    "MOTIF_APPLY_OPERATIONS",
    "MOTIF_ERROR_CODES",
    "MotifApplyDiagnostics",
    "MotifApplyError",
    "MotifApplyErrorCode",
    "MotifApplyOperation",
    "MotifApplyOperationResult",
    "MotifApplyRequest",
    "MotifApplyResponse",
    "MotifDestinationSelector",
    "MotifOperationParameters",
    "MotifSourceSelector",
    "build_transform_provenance",
    "count_composition_events",
    "count_motif_occurrences",
    "validate_motif_apply_request_limits",
]
