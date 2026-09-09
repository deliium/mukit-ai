"""Typed contracts for composition development preview (continue / add / vary).

Providers return relative drafts only. Deterministic services realize absolute ticks,
IDs, and preservation assertions. Candidates are ephemeral — never persisted by the API.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.composition_schemas import (
    SUPPORTED_SECTION_TYPES,
    CompositionV2,
    NOTE_PITCH_PATTERN,
    midi_pitch_number,
)
from app.schemas import LLMModelSelection


logger = logging.getLogger(__name__)

# --- Contract identity ---------------------------------------------------------

DEVELOPMENT_ALGORITHM_VERSION = "composition.development.v1"
EDIT_FINGERPRINT_PROFILE = "composition.edit.v1"
EDIT_FINGERPRINT_LOG_PREFIX_LEN = 12

DevelopmentOperation = Literal["continue", "add_section", "vary_section"]
DevelopmentIntent = Literal["continue", "develop", "contrast"]
VariationStrength = Literal["conservative", "balanced", "experimental"]
PreservationAssertionKind = Literal[
    "append_prefix",
    "outside_range",
    "track_topology",
    "instrumentation",
    "section_geometry",
    "structural_ids",
    "motif_references",
    "harmony_bounds",
    "seam_continuity",
]
IdentityDiagnosticSeverity = Literal["error", "warning", "info"]

DEVELOPMENT_OPERATIONS: frozenset[str] = frozenset({"continue", "add_section", "vary_section"})
DEVELOPMENT_INTENTS: frozenset[str] = frozenset({"continue", "develop", "contrast"})
VARIATION_STRENGTHS: frozenset[str] = frozenset({"conservative", "balanced", "experimental"})

# Bounded request guards (checked before provider invocation).
DEVELOPMENT_MIN_CANDIDATE_COUNT = 1
DEVELOPMENT_MAX_CANDIDATE_COUNT = 4
DEVELOPMENT_MAX_OUTPUT_BARS = 64
DEVELOPMENT_MAX_INSTRUCTION_CHARS = 500
DEVELOPMENT_MAX_SECTION_LABEL_CHARS = 80
DEVELOPMENT_MAX_EVENT_COUNT = 50_000
DEVELOPMENT_MAX_TRACK_COUNT = 64
DEVELOPMENT_MAX_SECTION_COUNT = 128
DEVELOPMENT_MAX_MOTIF_DEFINITIONS = 128
DEVELOPMENT_MAX_WARNINGS = 32
DEVELOPMENT_MAX_ASSERTIONS = 64
DEVELOPMENT_MAX_DIAGNOSTICS = 64
DEVELOPMENT_MAX_CONTEXT_BUDGET_CHARS = 48_000
DEVELOPMENT_DEFAULT_CONTEXT_BUDGET_CHARS = 12_000
DEVELOPMENT_MAX_DRAFT_EVENTS_PER_TRACK = 4_096
DEVELOPMENT_MAX_DRAFT_HARMONY = 256
DEVELOPMENT_MAX_DRAFT_MARKERS = 64
DEVELOPMENT_MAX_DRAFT_TIMELINE_CHANGES = 64
DEVELOPMENT_MAX_DRAFT_MOTIFS = 32

DEVELOPMENT_ERROR_CODES: dict[str, str] = {
    "development_request_too_large": "Composition or development request exceeds configured limits.",
    "development_invalid_operation": "Operation parameters are inconsistent with the requested development mode.",
    "development_source_required": "vary_section requires an explicit source section or bar range.",
    "development_output_bars_required": "continue and add_section require a positive output_bars value.",
    "development_unsupported_section_type": "Target section type is not supported.",
    "development_invalid_source": "Source section or bar range could not be resolved.",
    "development_preservation_failed": "Required source-preservation checks failed.",
    "development_seam_crossing": "Source material crosses the immutable development seam and cannot be preserved.",
    "development_track_topology": "Draft altered track topology or instrumentation.",
    "development_identity_failed": "Candidate failed required identity or seam continuity checks.",
    "development_draft_invalid": "Provider draft failed relative-scope validation.",
    "development_candidate_exhausted": "No valid development candidate survived generation and repair.",
    "development_provider_unavailable": "No usable LLM provider is configured for composition development.",
    "development_provider_error": "Provider failure prevented candidate generation (sanitized).",
    "development_internal_error": "Unexpected composition development failure (sanitized).",
}

DevelopmentErrorCode = Literal[
    "development_request_too_large",
    "development_invalid_operation",
    "development_source_required",
    "development_output_bars_required",
    "development_unsupported_section_type",
    "development_invalid_source",
    "development_preservation_failed",
    "development_seam_crossing",
    "development_track_topology",
    "development_identity_failed",
    "development_draft_invalid",
    "development_candidate_exhausted",
    "development_provider_unavailable",
    "development_provider_error",
    "development_internal_error",
]

DEVELOPMENT_WARNING_CODES: frozenset[str] = frozenset(
    {
        "candidate_failed_validation",
        "candidate_failed_identity",
        "candidate_repaired",
        "candidate_partial_success",
        "context_truncated",
        "empty_harmony_context",
        "empty_motif_context",
        "modulation_at_boundary",
        "restart_like_opening",
        "sparse_track_draft",
        "identity_anchor_weak",
        "seam_gap_advisory",
        "seam_leap_advisory",
        "density_divergence_advisory",
        "register_divergence_advisory",
        "rhythm_divergence_advisory",
        "harmonic_continuity_advisory",
    }
)

DEVELOPMENT_IDENTITY_DIAGNOSTIC_CODES: frozenset[str] = frozenset(
    {
        "motif_anchor_present",
        "motif_anchor_missing",
        "rhythmic_anchor_present",
        "rhythmic_anchor_missing",
        "harmonic_anchor_present",
        "harmonic_anchor_missing",
        "orchestration_anchor_present",
        "orchestration_anchor_missing",
        "register_divergence",
        "rhythm_divergence",
        "density_divergence",
        "harmonic_continuity",
        "seam_gap",
        "seam_leap",
        "missing_track_draft",
        "restart_like_opening",
        "identity_anchor_satisfied",
        "identity_anchor_failed",
        "seam_continuity_ok",
        "seam_continuity_failed",
    }
)


class CompositionDevelopmentError(Exception):
    """Domain error for composition development preview failures."""

    def __init__(
        self,
        code: DevelopmentErrorCode,
        message: str | None = None,
        *,
        http_status: int = 422,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message = message or DEVELOPMENT_ERROR_CODES.get(code, code)
        self.http_status = http_status
        self.details = details or {}
        super().__init__(self.message)
        logger.error(
            "Composition development domain error",
            extra={
                "error_code": code,
                "http_status": http_status,
                "detail_keys": sorted(self.details.keys()),
            },
        )


def _normalize_section_type(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def _log_schema_validation_failure(
    model_name: str,
    field_name: str,
    *,
    code: str | None = None,
    reason: str,
) -> None:
    logger.info(
        "Composition development schema validation failed",
        extra={
            "model": model_name,
            "field": field_name,
            "error_code": code,
            "reason": reason[:200],
        },
    )


class DevelopmentSourceSelector(BaseModel):
    """Optional musical context source; required for vary_section."""

    model_config = ConfigDict(extra="forbid")

    section_id: str | None = Field(default=None, min_length=1, max_length=120)
    start_bar: int | None = Field(default=None, ge=1)
    end_bar: int | None = Field(default=None, ge=1)

    @field_validator("section_id")
    @classmethod
    def validate_section_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            _log_schema_validation_failure(
                cls.__name__,
                "section_id",
                code="development_invalid_source",
                reason="section_id cannot be empty",
            )
            raise ValueError("section_id must not be empty when provided")
        return normalized

    @model_validator(mode="after")
    def validate_selector_shape(self) -> DevelopmentSourceSelector:
        has_section = self.section_id is not None
        has_start = self.start_bar is not None
        has_end = self.end_bar is not None
        if has_start != has_end:
            _log_schema_validation_failure(
                self.__class__.__name__,
                "bar_range",
                code="development_invalid_source",
                reason="start_bar and end_bar must be provided together",
            )
            raise ValueError("start_bar and end_bar must be provided together")
        if has_section and (has_start or has_end):
            _log_schema_validation_failure(
                self.__class__.__name__,
                "source",
                code="development_invalid_source",
                reason="provide either section_id or bar range, not both",
            )
            raise ValueError("provide either section_id or start_bar/end_bar, not both")
        if has_start and has_end and self.end_bar is not None and self.start_bar is not None:
            if self.end_bar < self.start_bar:
                _log_schema_validation_failure(
                    self.__class__.__name__,
                    "bar_range",
                    code="development_invalid_source",
                    reason="end_bar must be >= start_bar",
                )
                raise ValueError("end_bar must be >= start_bar")
        if not has_section and not has_start:
            _log_schema_validation_failure(
                self.__class__.__name__,
                "source",
                code="development_invalid_source",
                reason="source selector is empty",
            )
            raise ValueError("source requires section_id or start_bar/end_bar")
        return self


class CompositionDevelopmentOptions(BaseModel):
    """Bounded repair and context budget using existing LLM option conventions."""

    model_config = ConfigDict(extra="forbid")

    temperature: float | None = Field(default=None, ge=0, le=2)
    timeout_seconds: int | None = Field(default=None, ge=1, le=300)
    max_repairs: int = Field(default=1, ge=0, le=5)
    context_budget_chars: int = Field(
        default=DEVELOPMENT_DEFAULT_CONTEXT_BUDGET_CHARS,
        ge=1_000,
        le=DEVELOPMENT_MAX_CONTEXT_BUDGET_CHARS,
    )


class DevelopmentDraftNote(BaseModel):
    """Relative note inside the generated output scope (not absolute composition ticks)."""

    model_config = ConfigDict(extra="forbid")

    pitch: str = Field(..., min_length=2, max_length=5)
    relative_start_tick: int = Field(..., ge=0)
    duration_ticks: int = Field(..., gt=0)
    velocity: int = Field(default=80, ge=1, le=127)
    staff: Literal["treble", "bass"] | None = None
    voice: int | None = Field(default=None, ge=1, le=4)
    articulations: list[str] = Field(default_factory=list, max_length=8)
    # Optional stable draft-local id for motif occurrence wiring before realization.
    draft_event_id: str | None = Field(default=None, min_length=1, max_length=120)

    @field_validator("pitch")
    @classmethod
    def validate_pitch(cls, value: str) -> str:
        pitch = value.strip()
        if not NOTE_PITCH_PATTERN.match(pitch):
            raise ValueError(f"Invalid pitch: {value}")
        midi_pitch_number(pitch)
        return pitch


class DevelopmentDraftTrack(BaseModel):
    """Per-track relative draft; empty events are intentional rests."""

    model_config = ConfigDict(extra="forbid")

    track_id: str = Field(..., min_length=1, max_length=80)
    events: list[DevelopmentDraftNote] = Field(
        default_factory=list,
        max_length=DEVELOPMENT_MAX_DRAFT_EVENTS_PER_TRACK,
    )


class DevelopmentDraftHarmonyItem(BaseModel):
    """Relative harmony span within the generated output scope."""

    model_config = ConfigDict(extra="forbid")

    relative_start_tick: int = Field(..., ge=0)
    duration_ticks: int = Field(..., gt=0)
    chord: str = Field(..., min_length=1, max_length=32)

    @field_validator("chord")
    @classmethod
    def validate_chord(cls, value: str) -> str:
        chord = value.strip()
        if not chord:
            raise ValueError("Chord must not be empty")
        return chord


class DevelopmentDraftTimelineChange(BaseModel):
    """Optional key/meter/tempo change at a generated bar boundary (relative tick)."""

    model_config = ConfigDict(extra="forbid")

    relative_tick: int = Field(..., ge=0)
    key: str | None = Field(default=None, max_length=32)
    time_signature: str | None = Field(default=None, max_length=16)
    bpm: int | None = Field(default=None, ge=40, le=240)

    @model_validator(mode="after")
    def validate_at_least_one_change(self) -> DevelopmentDraftTimelineChange:
        if self.key is None and self.time_signature is None and self.bpm is None:
            raise ValueError("timeline change requires key, time_signature, or bpm")
        return self


class DevelopmentDraftMarker(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relative_tick: int = Field(..., ge=0)
    kind: Literal["rehearsal", "text", "tempo", "fermata", "breath"] = "text"
    label: str = Field(..., min_length=1, max_length=80)


class DevelopmentDraftMotifOccurrence(BaseModel):
    """Motif occurrence referencing draft-local event ids inside the generated scope."""

    model_config = ConfigDict(extra="forbid")

    motif_id: str = Field(..., min_length=1, max_length=120)
    draft_event_ids: list[str] = Field(..., min_length=1, max_length=256)
    track_id: str = Field(..., min_length=1, max_length=80)
    relationship: str | None = Field(default=None, max_length=64)


class CompositionDevelopmentDraft(BaseModel):
    """Bounded relative provider draft — never a complete composition."""

    model_config = ConfigDict(extra="forbid")

    tracks: list[DevelopmentDraftTrack] = Field(..., min_length=1, max_length=DEVELOPMENT_MAX_TRACK_COUNT)
    harmony: list[DevelopmentDraftHarmonyItem] = Field(
        default_factory=list,
        max_length=DEVELOPMENT_MAX_DRAFT_HARMONY,
    )
    timeline_changes: list[DevelopmentDraftTimelineChange] = Field(
        default_factory=list,
        max_length=DEVELOPMENT_MAX_DRAFT_TIMELINE_CHANGES,
    )
    markers: list[DevelopmentDraftMarker] = Field(
        default_factory=list,
        max_length=DEVELOPMENT_MAX_DRAFT_MARKERS,
    )
    motif_occurrences: list[DevelopmentDraftMotifOccurrence] = Field(
        default_factory=list,
        max_length=DEVELOPMENT_MAX_DRAFT_MOTIFS,
    )
    section_label: str | None = Field(default=None, max_length=DEVELOPMENT_MAX_SECTION_LABEL_CHARS)

    @field_validator("section_label")
    @classmethod
    def validate_section_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        label = " ".join(value.strip().split())
        return label or None

    @model_validator(mode="after")
    def validate_unique_track_ids(self) -> CompositionDevelopmentDraft:
        track_ids = [track.track_id for track in self.tracks]
        duplicates = sorted({track_id for track_id in track_ids if track_ids.count(track_id) > 1})
        if duplicates:
            raise ValueError(f"duplicate draft track_ids: {duplicates}")
        return self


class DevelopmentResolvedRange(BaseModel):
    """Resolved inclusive bars and half-open tick span."""

    model_config = ConfigDict(extra="forbid")

    start_bar: int = Field(..., ge=1)
    end_bar: int = Field(..., ge=1)
    start_tick: int = Field(..., ge=0)
    end_tick: int = Field(..., ge=0)

    @model_validator(mode="after")
    def validate_range(self) -> DevelopmentResolvedRange:
        if self.end_bar < self.start_bar:
            raise ValueError("end_bar must be >= start_bar")
        if self.end_tick < self.start_tick:
            raise ValueError("end_tick must be >= start_tick")
        return self


class DevelopmentSectionChangeSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["appended", "unchanged", "replaced_content"]
    section_id: str | None = Field(default=None, max_length=120)
    section_type: str = Field(..., min_length=1, max_length=32)
    start_bar: int = Field(..., ge=1)
    bar_count: int = Field(..., ge=1)


class DevelopmentHarmonyChangeSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["appended", "replaced", "removed", "unchanged"]
    start_tick: int = Field(..., ge=0)
    duration_ticks: int = Field(..., gt=0)
    chord: str | None = Field(default=None, max_length=32)


class DevelopmentMotifChangeSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["appended_occurrence", "reconciled", "unchanged"]
    motif_id: str = Field(..., min_length=1, max_length=120)
    occurrence_count_delta: int = 0


class DevelopmentTrackChangeSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    track_id: str = Field(..., min_length=1, max_length=80)
    role: str = Field(..., min_length=1, max_length=32)
    events_before: int = Field(..., ge=0)
    events_after: int = Field(..., ge=0)
    events_created: int = Field(..., ge=0)
    events_removed: int = Field(..., ge=0)


class DevelopmentPreservationAssertion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: PreservationAssertionKind
    satisfied: bool
    required: bool = True
    detail: str = Field(..., min_length=1, max_length=240)
    track_id: str | None = Field(default=None, max_length=80)


class DevelopmentIdentityDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(..., min_length=1, max_length=64)
    severity: IdentityDiagnosticSeverity
    message: str = Field(..., min_length=1, max_length=240)
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    track_id: str | None = Field(default=None, max_length=80)

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        code = value.strip()
        if code not in DEVELOPMENT_IDENTITY_DIAGNOSTIC_CODES:
            raise ValueError(f"Unknown identity diagnostic code: {code}")
        return code


class DevelopmentCandidate(BaseModel):
    """One independently generated, fully realized candidate composition."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(..., min_length=16, max_length=128)
    candidate_fingerprint: str = Field(..., min_length=16, max_length=128)
    edit_source_fingerprint: str = Field(..., min_length=16, max_length=128)
    algorithm_version: str = Field(default=DEVELOPMENT_ALGORITHM_VERSION, min_length=1, max_length=64)
    operation: DevelopmentOperation
    development_intent: DevelopmentIntent
    variation_strength: VariationStrength
    composition: CompositionV2
    source_range: DevelopmentResolvedRange
    output_range: DevelopmentResolvedRange
    section_changes: list[DevelopmentSectionChangeSummary] = Field(
        default_factory=list,
        max_length=DEVELOPMENT_MAX_SECTION_COUNT,
    )
    harmony_changes: list[DevelopmentHarmonyChangeSummary] = Field(
        default_factory=list,
        max_length=DEVELOPMENT_MAX_DRAFT_HARMONY,
    )
    motif_changes: list[DevelopmentMotifChangeSummary] = Field(
        default_factory=list,
        max_length=DEVELOPMENT_MAX_MOTIF_DEFINITIONS,
    )
    track_changes: list[DevelopmentTrackChangeSummary] = Field(
        default_factory=list,
        max_length=DEVELOPMENT_MAX_TRACK_COUNT,
    )
    preservation: list[DevelopmentPreservationAssertion] = Field(
        default_factory=list,
        max_length=DEVELOPMENT_MAX_ASSERTIONS,
    )
    identity_diagnostics: list[DevelopmentIdentityDiagnostic] = Field(
        default_factory=list,
        max_length=DEVELOPMENT_MAX_DIAGNOSTICS,
    )
    provider: Literal["openai", "deepseek", "fake"]
    model: str | None = Field(default=None, max_length=120)
    warning_codes: list[str] = Field(default_factory=list, max_length=DEVELOPMENT_MAX_WARNINGS)

    @field_validator("warning_codes")
    @classmethod
    def validate_warning_codes(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in value:
            code = item.strip()
            if not code or code in seen:
                continue
            if code not in DEVELOPMENT_WARNING_CODES:
                raise ValueError(f"Unknown development warning code: {code}")
            seen.add(code)
            cleaned.append(code)
        return cleaned


class CompositionDevelopmentPreviewRequest(BaseModel):
    """Stateless multi-candidate development preview — never mutates a project."""

    model_config = ConfigDict(extra="forbid")

    composition: CompositionV2
    operation: DevelopmentOperation
    source: DevelopmentSourceSelector | None = None
    output_bars: int | None = Field(default=None, ge=1, le=DEVELOPMENT_MAX_OUTPUT_BARS)
    target_section_type: str | None = Field(default=None, max_length=32)
    target_section_label: str | None = Field(default=None, max_length=DEVELOPMENT_MAX_SECTION_LABEL_CHARS)
    development_intent: DevelopmentIntent = "continue"
    variation_strength: VariationStrength
    candidate_count: int = Field(
        default=1,
        ge=DEVELOPMENT_MIN_CANDIDATE_COUNT,
        le=DEVELOPMENT_MAX_CANDIDATE_COUNT,
    )
    allow_modulation: bool = False
    instruction: str | None = Field(default=None, max_length=DEVELOPMENT_MAX_INSTRUCTION_CHARS)
    selection: LLMModelSelection = Field(default_factory=LLMModelSelection)
    options: CompositionDevelopmentOptions = Field(default_factory=CompositionDevelopmentOptions)

    @field_validator("target_section_type")
    @classmethod
    def validate_target_section_type(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = _normalize_section_type(value)
        if normalized not in SUPPORTED_SECTION_TYPES:
            _log_schema_validation_failure(
                cls.__name__,
                "target_section_type",
                code="development_unsupported_section_type",
                reason=f"unsupported section type: {value}",
            )
            raise ValueError(f"Unsupported section type: {value}")
        return normalized

    @field_validator("target_section_label")
    @classmethod
    def validate_target_section_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        label = " ".join(value.strip().split())
        return label or None

    @field_validator("instruction")
    @classmethod
    def validate_instruction(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = " ".join(value.strip().split())
        return text or None

    @model_validator(mode="after")
    def validate_operation_contract(self) -> CompositionDevelopmentPreviewRequest:
        operation = self.operation

        if operation == "vary_section":
            if self.source is None:
                _log_schema_validation_failure(
                    self.__class__.__name__,
                    "source",
                    code="development_source_required",
                    reason="vary_section requires source",
                )
                raise ValueError("vary_section requires an explicit source section or bar range")
            if self.output_bars is not None:
                _log_schema_validation_failure(
                    self.__class__.__name__,
                    "output_bars",
                    code="development_invalid_operation",
                    reason="vary_section must omit output_bars",
                )
                raise ValueError("vary_section must omit output_bars; output span equals the selected span")
            if self.target_section_type is not None or self.target_section_label is not None:
                _log_schema_validation_failure(
                    self.__class__.__name__,
                    "target_section_type",
                    code="development_invalid_operation",
                    reason="vary_section must not set target section fields",
                )
                raise ValueError("vary_section must not set target_section_type or target_section_label")
            return self

        # Append operations: continue / add_section
        if self.output_bars is None:
            _log_schema_validation_failure(
                self.__class__.__name__,
                "output_bars",
                code="development_output_bars_required",
                reason="append operations require output_bars",
            )
            raise ValueError("continue and add_section require a positive output_bars value")

        if operation == "add_section":
            if self.target_section_type is None:
                _log_schema_validation_failure(
                    self.__class__.__name__,
                    "target_section_type",
                    code="development_invalid_operation",
                    reason="add_section requires target_section_type",
                )
                raise ValueError("add_section requires target_section_type")
        elif self.target_section_type is not None or self.target_section_label is not None:
            # Plain continue may optionally name a section type only when adding structure —
            # plan: "section type is optional for plain continuation" — allow type on continue.
            if operation == "continue" and self.target_section_label is not None and self.target_section_type is None:
                _log_schema_validation_failure(
                    self.__class__.__name__,
                    "target_section_label",
                    code="development_invalid_operation",
                    reason="target_section_label requires target_section_type",
                )
                raise ValueError("target_section_label requires target_section_type")

        return self


class CompositionDevelopmentPreviewResponse(BaseModel):
    """Multi-candidate preview response; 1-4 validated candidates share the source fingerprint."""

    model_config = ConfigDict(extra="forbid")

    edit_source_fingerprint: str = Field(..., min_length=16, max_length=128)
    algorithm_version: str = Field(default=DEVELOPMENT_ALGORITHM_VERSION, min_length=1, max_length=64)
    operation: DevelopmentOperation
    development_intent: DevelopmentIntent
    variation_strength: VariationStrength
    requested_candidate_count: int = Field(
        ...,
        ge=DEVELOPMENT_MIN_CANDIDATE_COUNT,
        le=DEVELOPMENT_MAX_CANDIDATE_COUNT,
    )
    candidates: list[DevelopmentCandidate] = Field(
        ...,
        min_length=DEVELOPMENT_MIN_CANDIDATE_COUNT,
        max_length=DEVELOPMENT_MAX_CANDIDATE_COUNT,
    )
    warning_codes: list[str] = Field(default_factory=list, max_length=DEVELOPMENT_MAX_WARNINGS)
    provider: Literal["openai", "deepseek", "fake"]
    model: str | None = Field(default=None, max_length=120)

    @field_validator("warning_codes")
    @classmethod
    def validate_warning_codes(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in value:
            code = item.strip()
            if not code or code in seen:
                continue
            if code not in DEVELOPMENT_WARNING_CODES:
                raise ValueError(f"Unknown development warning code: {code}")
            seen.add(code)
            cleaned.append(code)
        return cleaned

    @model_validator(mode="after")
    def validate_candidate_consistency(self) -> CompositionDevelopmentPreviewResponse:
        candidate_ids = [candidate.candidate_id for candidate in self.candidates]
        duplicates = sorted({item for item in candidate_ids if candidate_ids.count(item) > 1})
        if duplicates:
            raise ValueError("candidate_id values must be unique within a response")

        for candidate in self.candidates:
            if candidate.edit_source_fingerprint != self.edit_source_fingerprint:
                raise ValueError("all candidates must share edit_source_fingerprint")
            if candidate.operation != self.operation:
                raise ValueError("candidate operation must match response operation")
            if candidate.development_intent != self.development_intent:
                raise ValueError("candidate development_intent must match response")
            if candidate.variation_strength != self.variation_strength:
                raise ValueError("candidate variation_strength must match response")
            if candidate.algorithm_version != self.algorithm_version:
                raise ValueError("candidate algorithm_version must match response")
        return self


def validate_development_request_limits(
    request: CompositionDevelopmentPreviewRequest,
) -> None:
    """Reject oversized compositions before any provider call."""
    composition = request.composition
    event_count = sum(len(track.events) for track in composition.tracks)
    motif_count = len(composition.motifs)
    occurrence_count = sum(len(motif.occurrences) for motif in composition.motifs)

    if (
        event_count > DEVELOPMENT_MAX_EVENT_COUNT
        or len(composition.tracks) > DEVELOPMENT_MAX_TRACK_COUNT
        or len(composition.sections) > DEVELOPMENT_MAX_SECTION_COUNT
        or motif_count > DEVELOPMENT_MAX_MOTIF_DEFINITIONS
    ):
        logger.info(
            "Rejected oversized composition development request",
            extra={
                "error_code": "development_request_too_large",
                "event_count": event_count,
                "track_count": len(composition.tracks),
                "section_count": len(composition.sections),
                "motif_count": motif_count,
                "occurrence_count": occurrence_count,
                "candidate_count": request.candidate_count,
            },
        )
        raise CompositionDevelopmentError(
            "development_request_too_large",
            http_status=422,
            details={
                "event_count": event_count,
                "track_count": len(composition.tracks),
                "section_count": len(composition.sections),
                "motif_count": motif_count,
                "max_event_count": DEVELOPMENT_MAX_EVENT_COUNT,
                "max_track_count": DEVELOPMENT_MAX_TRACK_COUNT,
                "max_section_count": DEVELOPMENT_MAX_SECTION_COUNT,
                "max_motif_definitions": DEVELOPMENT_MAX_MOTIF_DEFINITIONS,
            },
        )


def normalized_development_request_fingerprint_payload(
    request: CompositionDevelopmentPreviewRequest,
) -> dict[str, Any]:
    """Stable operation identity without the composition body."""
    source_payload: dict[str, Any] | None = None
    if request.source is not None:
        source_payload = request.source.model_dump(mode="json", exclude_none=True)
    return {
        "algorithm_version": DEVELOPMENT_ALGORITHM_VERSION,
        "operation": request.operation,
        "source": source_payload,
        "output_bars": request.output_bars,
        "target_section_type": request.target_section_type,
        "target_section_label": request.target_section_label,
        "development_intent": request.development_intent,
        "variation_strength": request.variation_strength,
        "candidate_count": request.candidate_count,
        "allow_modulation": request.allow_modulation,
        "instruction": request.instruction,
        "selection": request.selection.model_dump(mode="json", exclude_none=True),
        "options": request.options.model_dump(mode="json"),
    }

