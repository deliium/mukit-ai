"""Typed DTOs for immutable V2 harmony timeline operations and compatibility."""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.composition_schemas import CompositionV2, CompositionV2HarmonyItem


logger = logging.getLogger(__name__)

HarmonyTimelineOperationName = Literal["add", "replace", "remove", "move", "resize"]
HarmonyResizeEdge = Literal["start", "end"]

HARMONY_TIMELINE_ERROR_CODES: dict[str, str] = {
    "harmony_overlap": "Harmony span overlaps an existing span.",
    "harmony_out_of_bounds": "Harmony span is outside the composition duration.",
    "harmony_non_positive_duration": "Harmony duration_ticks must be positive.",
    "harmony_ambiguous_clear": "Clear/replace requires an explicit empty spans list.",
    "harmony_span_not_found": "No harmony span matches the requested identity.",
    "harmony_invalid_resize": "Resize would produce an invalid or overlapping span.",
    "harmony_move_overlap": "Moved harmony span would overlap another span.",
}


class HarmonyTimelineError(ValueError):
    """Domain error for immutable harmony timeline mutations."""

    def __init__(self, message: str, *, code: str, context: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.context = context or {}


class HarmonySpanInput(BaseModel):
    """Explicit half-open harmony span input (never playable notes)."""

    model_config = ConfigDict(extra="forbid")

    start_tick: int = Field(..., ge=0)
    duration_ticks: int = Field(..., gt=0)
    chord: str = Field(..., min_length=1, max_length=32)

    @field_validator("chord")
    @classmethod
    def validate_chord(cls, value: str) -> str:
        chord = value.strip()
        if not chord:
            raise ValueError("Chord must not be empty")
        return chord


class HarmonyAddOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["add"] = "add"
    span: HarmonySpanInput


class HarmonyReplaceOperation(BaseModel):
    """Replace the selected tick range. ``spans=[]`` is an explicit clear."""

    model_config = ConfigDict(extra="forbid")

    operation: Literal["replace"] = "replace"
    start_tick: int = Field(..., ge=0)
    duration_ticks: int = Field(..., gt=0)
    spans: list[HarmonySpanInput] = Field(default_factory=list)


class HarmonyRemoveOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["remove"] = "remove"
    start_tick: int = Field(..., ge=0)
    duration_ticks: int = Field(..., gt=0)


class HarmonyMoveOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["move"] = "move"
    source_start_tick: int = Field(..., ge=0)
    new_start_tick: int = Field(..., ge=0)


class HarmonyResizeOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["resize"] = "resize"
    source_start_tick: int = Field(..., ge=0)
    edge: HarmonyResizeEdge
    new_tick: int = Field(..., ge=0)


HarmonyTimelineOperation = Annotated[
    HarmonyAddOperation
    | HarmonyReplaceOperation
    | HarmonyRemoveOperation
    | HarmonyMoveOperation
    | HarmonyResizeOperation,
    Field(discriminator="operation"),
]


class HarmonyTimelineEditRequest(BaseModel):
    """Apply one immutable harmony-range operation to a composition.v2 document."""

    model_config = ConfigDict(extra="forbid")

    composition: CompositionV2
    edit: HarmonyTimelineOperation


class HarmonyTimelineEditResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    composition: CompositionV2
    operation: HarmonyTimelineOperationName
    span_count_before: int
    span_count_after: int
    changed_span_count: int


class HarmonyRegionPatchSpan(BaseModel):
    """Strict V2-only harmony patch payload for region edits (explicit spans)."""

    model_config = ConfigDict(extra="forbid")

    spans: list[CompositionV2HarmonyItem] = Field(default_factory=list)
    clear: bool = False

    @model_validator(mode="after")
    def validate_clear_semantics(self) -> HarmonyRegionPatchSpan:
        # Explicit discriminator: clear=true with non-empty spans is invalid.
        if self.clear and self.spans:
            raise ValueError("harmony clear patch must not include replacement spans")
        return self


CompatibilityFindingSeverity = Literal["info", "warning", "error"]
CompatibilityStatus = Literal["compatible", "compatible_with_warnings", "incompatible"]

# Stable finding codes for declared-harmony vs note evidence (not a diatonic allow-list).
HARMONY_COMPATIBILITY_FINDING_CODES: frozenset[str] = frozenset(
    {
        "declared_realized_agreement",
        "declared_realized_partial",
        "declared_realized_conflict",
        "unsupported_chord_symbol",
        "melody_chord_tone",
        "melody_non_chord_tone",
        "melody_strong_beat_clash",
        "melody_avoid_note_pressure",
        "melody_tension_resolution",
        "bass_root_support",
        "bass_inversion_support",
        "bass_weak_support",
        "accompaniment_pc_support",
        "accompaniment_density_delta",
        "voice_leading_leap",
        "tension_delta",
        "tonal_center_support",
        "secondary_dominant_function",
        "modal_mixture",
        "competing_tonal_center",
        "cadence_evidence",
        "cadence_weak",
        "preservation_violation",
        "unauthorized_target_change",
        "structural_corruption",
    }
)

MAX_COMPATIBILITY_FINDINGS = 64


class CompatibilityFinding(BaseModel):
    """One bounded compatibility evidence item (codes/counts only in logs)."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(..., min_length=1, max_length=64)
    severity: CompatibilityFindingSeverity
    message: str = Field(..., min_length=1, max_length=240)
    start_tick: int | None = Field(default=None, ge=0)
    end_tick: int | None = Field(default=None, ge=0)
    track_id: str | None = Field(default=None, max_length=64)
    evidence: dict[str, Any] = Field(default_factory=dict)

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        code = value.strip()
        if code not in HARMONY_COMPATIBILITY_FINDING_CODES:
            raise ValueError(f"Unknown compatibility finding code: {code}")
        return code


class CompatibilityReport(BaseModel):
    """Duration-weighted declared-harmony vs realized-note compatibility."""

    model_config = ConfigDict(extra="forbid")

    status: CompatibilityStatus
    findings: list[CompatibilityFinding] = Field(default_factory=list, max_length=MAX_COMPATIBILITY_FINDINGS)
    finding_code_counts: dict[str, int] = Field(default_factory=dict)
    evidence_counts: dict[str, int] = Field(default_factory=dict)
    active_key: str | None = None


ReharmonizeOperation = Literal[
    "suggest_progression",
    "reharmonize",
    "increase_tension",
    "decrease_tension",
    "strengthen_cadence",
    "tonicize_target",
    "use_secondary_dominants",
    "use_modal_interchange",
    "simplify_harmony",
]

ReharmonizeContentPolicy = Literal[
    "preserve_melody_adapt_harmony",
    "preserve_harmony_adapt_melody",
    "adapt_accompaniment_only",
]

ReharmonizeEngine = Literal["deterministic", "ai"]

REHARMONIZE_OPERATIONS: frozenset[str] = frozenset(
    {
        "suggest_progression",
        "reharmonize",
        "increase_tension",
        "decrease_tension",
        "strengthen_cadence",
        "tonicize_target",
        "use_secondary_dominants",
        "use_modal_interchange",
        "simplify_harmony",
    }
)

REHARMONIZE_ERROR_CODES: dict[str, str] = {
    "reharmonize_invalid_selection": "Bar selection is invalid for the composition.",
    "reharmonize_invalid_targets": "Target track authorization failed.",
    "reharmonize_no_realizable_target": "No realizable harmonic-support track was selected.",
    "reharmonize_crossing_note": "Selection intersects a note or tie that continues outside the range.",
    "reharmonize_unsupported_symbol": "A declared chord symbol cannot be transformed deterministically.",
    "reharmonize_modulation_required": "Modulation options are incomplete or unauthorized.",
    "reharmonize_tonicize_required": "tonicize_target requires a parseable target_chord.",
    "reharmonize_preservation_failed": "Required preservation checks failed.",
    "reharmonize_inaudible_success": "Operation would not change any audible target or harmony as required.",
    "reharmonize_internal_error": "Unexpected reharmonization failure (sanitized).",
}

ReharmonizeErrorCode = Literal[
    "reharmonize_invalid_selection",
    "reharmonize_invalid_targets",
    "reharmonize_no_realizable_target",
    "reharmonize_crossing_note",
    "reharmonize_unsupported_symbol",
    "reharmonize_modulation_required",
    "reharmonize_tonicize_required",
    "reharmonize_preservation_failed",
    "reharmonize_inaudible_success",
    "reharmonize_internal_error",
]


class ReharmonizeError(Exception):
    """Domain error for reharmonization preview failures."""

    def __init__(
        self,
        code: ReharmonizeErrorCode,
        message: str | None = None,
        *,
        http_status: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message = message or REHARMONIZE_ERROR_CODES.get(code, code)
        self.http_status = http_status
        self.details = details or {}
        super().__init__(self.message)


class ReharmonizeBarSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_bar: int = Field(..., ge=1)
    end_bar: int = Field(..., ge=1)

    @model_validator(mode="after")
    def validate_range(self) -> ReharmonizeBarSelection:
        if self.end_bar < self.start_bar:
            raise ValueError("end_bar must be >= start_bar")
        return self


class ReharmonizeTonalContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow_modulation: bool = False
    target_key: str | None = Field(default=None, max_length=32)
    target_chord: str | None = Field(default=None, max_length=32)

    @field_validator("target_key")
    @classmethod
    def validate_target_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = " ".join(value.strip().split())
        return text or None

    @field_validator("target_chord")
    @classmethod
    def validate_target_chord(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        return text or None


class ReharmonizeSelectionOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=128)


class HarmonyChangeSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_tick: int = Field(..., ge=0)
    duration_ticks: int = Field(..., gt=0)
    before_chord: str | None = Field(default=None, max_length=32)
    after_chord: str | None = Field(default=None, max_length=32)


class TrackChangeSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    track_id: str = Field(..., min_length=1, max_length=64)
    role: str = Field(..., min_length=1, max_length=32)
    events_before: int = Field(..., ge=0)
    events_after: int = Field(..., ge=0)
    events_changed: int = Field(..., ge=0)


class PreservationAssertion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "melody_events",
        "harmony_spans",
        "outside_range_events",
        "track_events",
        "key_metadata",
        "structural_ids",
    ]
    track_id: str | None = Field(default=None, max_length=64)
    satisfied: bool
    detail: str = Field(..., min_length=1, max_length=240)


class ReharmonizePreviewRequest(BaseModel):
    """Stateless reharmonization preview — never mutates a project."""

    model_config = ConfigDict(extra="forbid")

    composition: CompositionV2
    selection: ReharmonizeBarSelection
    operation: ReharmonizeOperation
    content_policy: ReharmonizeContentPolicy
    target_track_ids: list[str] = Field(default_factory=list, max_length=64)
    engine: ReharmonizeEngine = "deterministic"
    instruction: str | None = Field(default=None, max_length=500)
    tonal_context: ReharmonizeTonalContext = Field(default_factory=ReharmonizeTonalContext)
    selection_options: ReharmonizeSelectionOptions = Field(default_factory=ReharmonizeSelectionOptions)

    @field_validator("target_track_ids")
    @classmethod
    def validate_target_ids(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in value:
            track_id = item.strip()
            if not track_id or track_id in seen:
                continue
            seen.add(track_id)
            cleaned.append(track_id)
        return cleaned

    @field_validator("instruction")
    @classmethod
    def validate_instruction(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = " ".join(value.strip().split())
        return text or None


class ReharmonizePreviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_fingerprint: str = Field(..., min_length=16, max_length=128)
    proposal_fingerprint: str = Field(..., min_length=16, max_length=128)
    composition: CompositionV2
    harmony_changes: list[HarmonyChangeSummary] = Field(default_factory=list, max_length=256)
    track_changes: list[TrackChangeSummary] = Field(default_factory=list, max_length=64)
    preservation: list[PreservationAssertion] = Field(default_factory=list, max_length=64)
    compatibility: CompatibilityReport
    provider: str
    model: str | None = None
    warnings: list[str] = Field(default_factory=list, max_length=32)
    recommended_target_track_ids: list[str] = Field(default_factory=list, max_length=64)
    start_tick: int = Field(..., ge=0)
    end_tick: int = Field(..., ge=0)
    active_key: str
