"""Typed contracts for composition arrangement preview (orchestration / texture).

Providers return relative drafts only. Deterministic services realize absolute ticks,
IDs, channels, and preservation assertions. Candidates are ephemeral — never
persisted by the API.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.composition_schemas import (
    SUPPORTED_TRACK_ROLES,
    ArticulationName,
    CompositionV2,
    NOTE_PITCH_PATTERN,
    TieType,
    midi_pitch_number,
)
from app.schemas import LLMModelSelection


logger = logging.getLogger(__name__)

# --- Contract identity ---------------------------------------------------------

ARRANGEMENT_ALGORITHM_VERSION = "composition.arrangement.v1"
EDIT_FINGERPRINT_PROFILE = "composition.edit.v1"
EDIT_FINGERPRINT_LOG_PREFIX_LEN = 12

# Catalog / range-policy version strings (must match packaged catalog; schemas
# do not import the catalog service).
ARRANGEMENT_CATALOG_VERSION = "arrangement.instruments.v1"
ARRANGEMENT_RANGE_POLICY_VERSION = "arrangement.ranges.v1"

ArrangementOperation = Literal[
    "change_instrumentation",
    "add_accompaniment",
    "remove_accompaniment",
    "orchestrate_selected_tracks",
    "piano_to_ensemble",
    "simplify_arrangement",
    "increase_texture_density",
    "decrease_texture_density",
    "create_countermelody",
    "double_melody",
]

# Range repair for authorized non-melody material only.
# - reject: out-of-range notes fail the candidate
# - octave_shift_unprotected: fold unprotected notes by octaves (pitch class + timing)
RangeAdjustmentPolicy = Literal["reject", "octave_shift_unprotected"]

# Intentional doubling semantics for after parts (default none = no clone exemption).
# - none: part is not a declared double; accidental clones are rejected
# - unison: same pitch class + register as the named source
# - octave: same pitch class at a different octave
# - declared: explicit source/target overlap exempt from duplicate rejection
DoublingPolicy = Literal["none", "unison", "octave", "declared"]

ArrangementDraftPartAction = Literal[
    "retain",
    "remove",
    "reinstrument",
    "add",
    "split",
    "merge",
    "redistribute",
    "double",
]

ArrangementRejectedStage = Literal[
    "request",
    "context",
    "draft_validation",
    "realization",
    "preservation",
    "range",
    "density",
    "duplicate",
    "harmony",
    "identity",
    "repair",
    "provider",
]

ArrangementAssertionKind = Literal[
    "melody_preservation",
    "harmony_preservation",
    "protected_tracks",
    "unselected_tracks",
    "topology_authorization",
    "instrumentation_after",
    "source_note_cardinality",
    "density_direction",
    "range_policy",
    "motif_integrity",
    "audible_effect",
    "declared_doubling",
]

ARRANGEMENT_OPERATIONS: frozenset[str] = frozenset(
    {
        "change_instrumentation",
        "add_accompaniment",
        "remove_accompaniment",
        "orchestrate_selected_tracks",
        "piano_to_ensemble",
        "simplify_arrangement",
        "increase_texture_density",
        "decrease_texture_density",
        "create_countermelody",
        "double_melody",
    }
)

# Operations whose before/after instrumentation inventories must not be identical.
_OPERATIONS_REQUIRING_INSTRUMENTATION_CHANGE: frozenset[str] = frozenset(
    {
        "change_instrumentation",
        "add_accompaniment",
        "remove_accompaniment",
        "orchestrate_selected_tracks",
        "piano_to_ensemble",
        "create_countermelody",
        "double_melody",
    }
)

# Roles treated as accompaniment for remove_accompaniment (bass allowed when selected).
ARRANGEMENT_ACCOMPANIMENT_ROLES: frozenset[str] = frozenset(
    {"harmony", "pad", "rhythm", "bass"}
)

AUTHORIZED_DOUBLING_POLICIES: frozenset[str] = frozenset({"unison", "octave", "declared"})

# Bounded request / draft / response guards (checked before provider invocation).
ARRANGEMENT_MIN_CANDIDATE_COUNT = 1
ARRANGEMENT_MAX_CANDIDATE_COUNT = 4
ARRANGEMENT_MAX_INSTRUCTION_CHARS = 500
ARRANGEMENT_MAX_SOURCE_TRACKS = 64
ARRANGEMENT_MAX_PROTECTED_TRACKS = 64
ARRANGEMENT_MAX_BEFORE_PARTS = 64
ARRANGEMENT_MAX_AFTER_PARTS = 64
ARRANGEMENT_MAX_PART_SOURCE_TRACKS = 32
ARRANGEMENT_MAX_EVENT_COUNT = 50_000
ARRANGEMENT_MAX_TRACK_COUNT = 64
ARRANGEMENT_MAX_SECTION_COUNT = 128
ARRANGEMENT_MAX_MOTIF_DEFINITIONS = 128
ARRANGEMENT_MAX_WARNINGS = 32
ARRANGEMENT_MAX_ASSERTIONS = 64
ARRANGEMENT_MAX_REJECTED_ATTEMPTS = 16
ARRANGEMENT_MAX_REJECTED_REASONS = 8
ARRANGEMENT_MAX_CONTEXT_BUDGET_CHARS = 48_000
ARRANGEMENT_DEFAULT_CONTEXT_BUDGET_CHARS = 12_000
ARRANGEMENT_MAX_DRAFT_NOTES_PER_PART = 4_096
ARRANGEMENT_MAX_DRAFT_PARTS = 64
ARRANGEMENT_MAX_DRAFT_SOURCE_NOTE_REFS = 8_192
ARRANGEMENT_MAX_MANIFEST_MAPPINGS = 256
ARRANGEMENT_MAX_RANGE_FINDINGS = 64
ARRANGEMENT_MAX_DUPLICATE_FINDINGS = 64
ARRANGEMENT_MAX_TARGET_PROFILE_FINGERPRINTS = 64
ARRANGEMENT_MAX_REASON_CHARS = 240

ARRANGEMENT_ERROR_CODES: dict[str, str] = {
    "arrangement_request_too_large": "Composition or arrangement request exceeds configured limits.",
    "arrangement_invalid_operation": "Operation parameters are inconsistent with the requested arrangement mode.",
    "arrangement_noop_instrumentation": "Before and after instrumentation inventories are identical for an operation that requires change.",
    "arrangement_invalid_accompaniment_role": "remove_accompaniment requires accompaniment-role parts (harmony/pad/rhythm/bass).",
    "arrangement_piano_required": "piano_to_ensemble requires at least one piano instrument in the before inventory.",
    "arrangement_ensemble_targets_required": "piano_to_ensemble requires at least two distinct after instruments.",
    "arrangement_countermelody_required": "create_countermelody requires at least one after part with role countermelody.",
    "arrangement_doubling_required": "double_melody requires exactly one authorized doubling relationship.",
    "arrangement_source_protected_overlap": "source_track_ids and protected_track_ids must be disjoint.",
    "arrangement_invalid_source": "Source or protected track authorization failed.",
    "arrangement_inventory_mismatch": "Explicit before/after instrumentation inventory failed validation.",
    "arrangement_preservation_failed": "Required arrangement preservation checks failed.",
    "arrangement_range_failed": "Absolute range or range-policy checks failed for changed target material.",
    "arrangement_draft_invalid": "Provider draft failed relative-scope validation.",
    "arrangement_candidate_exhausted": "No valid arrangement candidate survived generation and repair.",
    "arrangement_provider_unavailable": "No usable LLM provider is configured for composition arrangement.",
    "arrangement_provider_error": "Provider failure prevented candidate generation (sanitized).",
    "arrangement_catalog_unavailable": "Arrangement instrument catalog is unavailable or invalid.",
    "arrangement_internal_error": "Unexpected composition arrangement failure (sanitized).",
}

ArrangementErrorCode = Literal[
    "arrangement_request_too_large",
    "arrangement_invalid_operation",
    "arrangement_noop_instrumentation",
    "arrangement_invalid_accompaniment_role",
    "arrangement_piano_required",
    "arrangement_ensemble_targets_required",
    "arrangement_countermelody_required",
    "arrangement_doubling_required",
    "arrangement_source_protected_overlap",
    "arrangement_invalid_source",
    "arrangement_inventory_mismatch",
    "arrangement_preservation_failed",
    "arrangement_range_failed",
    "arrangement_draft_invalid",
    "arrangement_candidate_exhausted",
    "arrangement_provider_unavailable",
    "arrangement_provider_error",
    "arrangement_catalog_unavailable",
    "arrangement_internal_error",
]

ARRANGEMENT_WARNING_CODES: frozenset[str] = frozenset(
    {
        "candidate_failed_validation",
        "candidate_failed_preservation",
        "candidate_failed_range",
        "candidate_failed_density",
        "candidate_failed_duplicate",
        "candidate_repaired",
        "candidate_partial_success",
        "context_truncated",
        "empty_harmony_context",
        "questionable_range",
        "baseline_range_retained",
        "baseline_instrument_mismatch",
        "ambiguous_role",
        "mild_harmony_tension",
        "density_metric_advisory",
        "motif_occurrence_pruned",
        "declared_doubling_applied",
        "octave_adjustment_applied",
        "unlisted_after_allowed",
    }
)


class CompositionArrangementError(Exception):
    """Domain error for composition arrangement preview failures."""

    def __init__(
        self,
        code: ArrangementErrorCode,
        message: str | None = None,
        *,
        http_status: int = 422,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message = message or ARRANGEMENT_ERROR_CODES.get(code, code)
        self.http_status = http_status
        self.details = details or {}
        super().__init__(self.message)
        logger.error(
            "Composition arrangement domain error",
            extra={
                "error_code": code,
                "http_status": http_status,
                "detail_keys": sorted(self.details.keys()),
            },
        )


def _log_schema_validation_failure(
    model_name: str,
    field_name: str,
    *,
    code: str | None = None,
    reason: str,
) -> None:
    logger.debug(
        "Composition arrangement schema validation failed",
        extra={
            "model": model_name,
            "field": field_name,
            "error_code": code,
            "reason": reason[:200],
        },
    )


def _normalize_token(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def _is_piano_instrument_id(instrument_id: str) -> bool:
    """Heuristic piano detection without importing the catalog service."""
    return "piano" in _normalize_token(instrument_id)


def _unique_normalized_ids(values: list[str], *, field_name: str, model_name: str) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in values:
        item = raw.strip()
        if not item:
            _log_schema_validation_failure(
                model_name,
                field_name,
                code="arrangement_invalid_operation",
                reason=f"{field_name} contains an empty id",
            )
            raise ValueError(f"{field_name} must not contain empty ids")
        if item in seen:
            _log_schema_validation_failure(
                model_name,
                field_name,
                code="arrangement_invalid_operation",
                reason=f"duplicate id in {field_name}",
            )
            raise ValueError(f"{field_name} must be unique")
        seen.add(item)
        cleaned.append(item)
    return cleaned


def _part_inventory_signature(part: ArrangementPartRequirement) -> tuple[Any, ...]:
    return (
        part.instrument_id,
        part.role,
        part.doubling_policy,
        tuple(part.source_track_ids),
    )


class ArrangementPartRequirement(BaseModel):
    """One row per explicit before/after part (counts derived from multiplicity)."""

    model_config = ConfigDict(extra="forbid")

    part_id: str = Field(..., min_length=1, max_length=80)
    instrument_id: str = Field(..., min_length=1, max_length=120)
    role: str | None = Field(default=None, max_length=32)
    source_track_ids: list[str] = Field(
        default_factory=list,
        max_length=ARRANGEMENT_MAX_PART_SOURCE_TRACKS,
    )
    # Default none — intentional doubles must declare unison/octave/declared.
    doubling_policy: DoublingPolicy = "none"

    @field_validator("part_id", "instrument_id")
    @classmethod
    def normalize_required_ids(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("id fields must not be empty")
        return normalized

    @field_validator("role")
    @classmethod
    def validate_role(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = _normalize_token(value)
        if normalized not in SUPPORTED_TRACK_ROLES:
            _log_schema_validation_failure(
                cls.__name__,
                "role",
                code="arrangement_invalid_operation",
                reason=f"unsupported role: {value}",
            )
            raise ValueError(f"Unsupported track role: {value}")
        return normalized

    @field_validator("source_track_ids")
    @classmethod
    def validate_source_track_ids(cls, value: list[str]) -> list[str]:
        return _unique_normalized_ids(
            value,
            field_name="source_track_ids",
            model_name=cls.__name__,
        )


class ArrangementInstrumentationRequirements(BaseModel):
    """Explicit before (precondition) and after (postcondition) part inventories."""

    model_config = ConfigDict(extra="forbid")

    before: list[ArrangementPartRequirement] = Field(
        ...,
        min_length=1,
        max_length=ARRANGEMENT_MAX_BEFORE_PARTS,
    )
    after: list[ArrangementPartRequirement] = Field(
        ...,
        min_length=1,
        max_length=ARRANGEMENT_MAX_AFTER_PARTS,
    )

    @model_validator(mode="after")
    def validate_unique_part_ids(self) -> ArrangementInstrumentationRequirements:
        before_ids = [part.part_id for part in self.before]
        after_ids = [part.part_id for part in self.after]
        for label, ids in (("before", before_ids), ("after", after_ids)):
            duplicates = sorted({item for item in ids if ids.count(item) > 1})
            if duplicates:
                _log_schema_validation_failure(
                    self.__class__.__name__,
                    label,
                    code="arrangement_inventory_mismatch",
                    reason=f"duplicate part_id in {label}",
                )
                raise ValueError(f"duplicate part_id values in instrumentation.{label}")
        return self

    @property
    def before_part_count(self) -> int:
        return len(self.before)

    @property
    def after_part_count(self) -> int:
        return len(self.after)

    @property
    def before_instrument_ids(self) -> list[str]:
        return [part.instrument_id for part in self.before]

    @property
    def after_instrument_ids(self) -> list[str]:
        return [part.instrument_id for part in self.after]

    @property
    def before_distinct_instrument_count(self) -> int:
        return len(set(self.before_instrument_ids))

    @property
    def after_distinct_instrument_count(self) -> int:
        return len(set(self.after_instrument_ids))

    def instrument_count(self, side: Literal["before", "after"], instrument_id: str) -> int:
        parts = self.before if side == "before" else self.after
        return sum(1 for part in parts if part.instrument_id == instrument_id)

    def role_count(self, side: Literal["before", "after"], role: str) -> int:
        normalized = _normalize_token(role)
        parts = self.before if side == "before" else self.after
        return sum(1 for part in parts if part.role == normalized)


class CompositionArrangementOptions(BaseModel):
    """Bounded repair and context budget using existing LLM option conventions."""

    model_config = ConfigDict(extra="forbid")

    temperature: float | None = Field(default=None, ge=0, le=2)
    timeout_seconds: int | None = Field(default=None, ge=1, le=300)
    max_repairs: int = Field(default=1, ge=0, le=5)
    context_budget_chars: int = Field(
        default=ARRANGEMENT_DEFAULT_CONTEXT_BUDGET_CHARS,
        ge=1_000,
        le=ARRANGEMENT_MAX_CONTEXT_BUDGET_CHARS,
    )


class ArrangementDraftNoteTie(BaseModel):
    """Draft-local tie metadata (no persistent event IDs)."""

    model_config = ConfigDict(extra="forbid")

    group_id: str = Field(..., min_length=1, max_length=120)
    type: TieType


class ArrangementDraftNote(BaseModel):
    """Relative canonical note inside a draft part (not absolute composition ticks)."""

    model_config = ConfigDict(extra="forbid")

    pitch: str = Field(..., min_length=2, max_length=5)
    relative_start_tick: int = Field(..., ge=0)
    duration_ticks: int = Field(..., gt=0)
    velocity: int = Field(default=80, ge=1, le=127)
    staff: Literal["treble", "bass"] | None = None
    voice: int | None = Field(default=None, ge=1, le=4)
    articulations: list[ArticulationName] = Field(default_factory=list, max_length=8)
    tie: ArrangementDraftNoteTie | None = None

    @field_validator("pitch")
    @classmethod
    def validate_pitch(cls, value: str) -> str:
        pitch = value.strip()
        if not NOTE_PITCH_PATTERN.match(pitch):
            raise ValueError(f"Invalid pitch: {value}")
        midi_pitch_number(pitch)
        return pitch

    @field_validator("articulations")
    @classmethod
    def validate_articulations(cls, value: list[ArticulationName]) -> list[ArticulationName]:
        if len(value) != len(set(value)):
            raise ValueError("articulations must be duplicate-free")
        return value


class ArrangementDraftPart(BaseModel):
    """One provider draft part — topology action plus optional generated notes."""

    model_config = ConfigDict(extra="forbid")

    action: ArrangementDraftPartAction
    part_id: str = Field(..., min_length=1, max_length=80)
    source_track_ids: list[str] = Field(
        default_factory=list,
        max_length=ARRANGEMENT_MAX_PART_SOURCE_TRACKS,
    )
    # Transient request-local note references (never persistent event IDs).
    source_note_refs: list[str] = Field(
        default_factory=list,
        max_length=ARRANGEMENT_MAX_DRAFT_SOURCE_NOTE_REFS,
    )
    notes: list[ArrangementDraftNote] = Field(
        default_factory=list,
        max_length=ARRANGEMENT_MAX_DRAFT_NOTES_PER_PART,
    )

    @field_validator("part_id")
    @classmethod
    def normalize_part_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("part_id must not be empty")
        return normalized

    @field_validator("source_track_ids", "source_note_refs")
    @classmethod
    def validate_ref_lists(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in value:
            item = raw.strip()
            if not item:
                raise ValueError("draft references must not be empty")
            if item in seen:
                raise ValueError("draft references must be unique within a part")
            seen.add(item)
            cleaned.append(item)
        return cleaned


class CompositionArrangementDraft(BaseModel):
    """Bounded relative provider draft — never a complete composition.

    Forbidden: top-level composition fields, persistent track/event IDs,
    MIDI channels, and GM program numbers.
    """

    model_config = ConfigDict(extra="forbid")

    parts: list[ArrangementDraftPart] = Field(
        ...,
        min_length=1,
        max_length=ARRANGEMENT_MAX_DRAFT_PARTS,
    )

    @model_validator(mode="after")
    def validate_unique_part_ids(self) -> CompositionArrangementDraft:
        part_ids = [part.part_id for part in self.parts]
        duplicates = sorted({item for item in part_ids if part_ids.count(item) > 1})
        if duplicates:
            raise ValueError(f"duplicate draft part_ids: {duplicates}")
        return self


class ArrangementTrackInventoryItem(BaseModel):
    """Actual before/after track inventory row (realized candidate summary)."""

    model_config = ConfigDict(extra="forbid")

    track_id: str = Field(..., min_length=1, max_length=80)
    instrument: str = Field(..., min_length=1, max_length=120)
    role: str = Field(..., min_length=1, max_length=32)
    midi_program: int | None = Field(default=None, ge=0, le=127)
    event_count: int = Field(..., ge=0)
    part_id: str | None = Field(default=None, max_length=80)


class ArrangementSourceTargetMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_track_id: str = Field(..., min_length=1, max_length=80)
    target_track_id: str = Field(..., min_length=1, max_length=80)
    relationship: Literal[
        "retained",
        "reinstrumented",
        "redistributed",
        "split",
        "merged",
        "doubled",
        "removed",
    ]


class ArrangementTopologyManifest(BaseModel):
    """Topology / change manifest explaining every base and candidate track."""

    model_config = ConfigDict(extra="forbid")

    retained_track_ids: list[str] = Field(default_factory=list, max_length=ARRANGEMENT_MAX_TRACK_COUNT)
    removed_track_ids: list[str] = Field(default_factory=list, max_length=ARRANGEMENT_MAX_TRACK_COUNT)
    added_track_ids: list[str] = Field(default_factory=list, max_length=ARRANGEMENT_MAX_TRACK_COUNT)
    reordered_track_ids: list[str] = Field(default_factory=list, max_length=ARRANGEMENT_MAX_TRACK_COUNT)
    reinstrumented_track_ids: list[str] = Field(
        default_factory=list,
        max_length=ARRANGEMENT_MAX_TRACK_COUNT,
    )
    split_track_ids: list[str] = Field(default_factory=list, max_length=ARRANGEMENT_MAX_TRACK_COUNT)
    merged_track_ids: list[str] = Field(default_factory=list, max_length=ARRANGEMENT_MAX_TRACK_COUNT)
    source_to_target: list[ArrangementSourceTargetMapping] = Field(
        default_factory=list,
        max_length=ARRANGEMENT_MAX_MANIFEST_MAPPINGS,
    )


class ArrangementEventCounts(BaseModel):
    """Per-candidate aggregate event accounting."""

    model_config = ConfigDict(extra="forbid")

    copied: int = Field(default=0, ge=0)
    moved: int = Field(default=0, ge=0)
    generated: int = Field(default=0, ge=0)
    removed: int = Field(default=0, ge=0)
    octave_adjusted: int = Field(default=0, ge=0)
    unchanged: int = Field(default=0, ge=0)


class ArrangementDensityMetrics(BaseModel):
    """Measured density over selected source/target tracks."""

    model_config = ConfigDict(extra="forbid")

    event_count: int = Field(..., ge=0)
    attack_count: int = Field(..., ge=0)
    active_track_count: int = Field(..., ge=0)
    union_occupancy_ticks: int = Field(..., ge=0)
    max_simultaneity: int = Field(..., ge=0)


class ArrangementDensitySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    before: ArrangementDensityMetrics
    after: ArrangementDensityMetrics


class ArrangementRangeFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: Literal["error", "warning", "info"]
    code: Literal[
        "absolute_out_of_range",
        "questionable_range",
        "baseline_range_retained",
        "unbounded_policy",
        "unknown_policy",
    ]
    track_id: str | None = Field(default=None, max_length=80)
    instrument_id: str | None = Field(default=None, max_length=120)
    detail: str = Field(..., min_length=1, max_length=ARRANGEMENT_MAX_REASON_CHARS)


class ArrangementDuplicateFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: Literal["error", "warning", "info"]
    code: Literal[
        "exact_clone",
        "high_overlap_clone",
        "cross_instrument_clone",
        "cross_role_clone",
        "octave_melody_clone",
        "declared_doubling",
        "legitimate_same_instrument_parts",
    ]
    source_track_id: str | None = Field(default=None, max_length=80)
    target_track_id: str | None = Field(default=None, max_length=80)
    detail: str = Field(..., min_length=1, max_length=ARRANGEMENT_MAX_REASON_CHARS)


class ArrangementHarmonyCompatibilitySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checked_note_count: int = Field(..., ge=0)
    compatible_note_count: int = Field(..., ge=0)
    tension_note_count: int = Field(default=0, ge=0)
    failed: bool = False
    detail: str | None = Field(default=None, max_length=ARRANGEMENT_MAX_REASON_CHARS)


class ArrangementPreservationAssertion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ArrangementAssertionKind
    satisfied: bool
    required: bool = True
    detail: str = Field(..., min_length=1, max_length=ARRANGEMENT_MAX_REASON_CHARS)
    track_id: str | None = Field(default=None, max_length=80)


class ArrangementTargetProfileFingerprint(BaseModel):
    """Immutable fingerprint for a catalog profile used by a candidate."""

    model_config = ConfigDict(extra="forbid")

    instrument_id: str = Field(..., min_length=1, max_length=120)
    profile_fingerprint: str = Field(..., min_length=16, max_length=128)


class ArrangementRejectedAttempt(BaseModel):
    """Bounded rejection summary — never includes a partial composition."""

    model_config = ConfigDict(extra="forbid")

    ordinal: int = Field(..., ge=1, le=ARRANGEMENT_MAX_CANDIDATE_COUNT)
    stage: ArrangementRejectedStage
    codes: list[str] = Field(..., min_length=1, max_length=ARRANGEMENT_MAX_REJECTED_REASONS)
    reasons: list[str] = Field(
        default_factory=list,
        max_length=ARRANGEMENT_MAX_REJECTED_REASONS,
    )

    @field_validator("codes")
    @classmethod
    def validate_codes(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in value:
            code = item.strip()
            if not code or code in seen:
                continue
            if len(code) > 64:
                raise ValueError("rejected attempt codes must be <= 64 characters")
            seen.add(code)
            cleaned.append(code)
        if not cleaned:
            raise ValueError("rejected attempt requires at least one stable code")
        return cleaned

    @field_validator("reasons")
    @classmethod
    def validate_reasons(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in value:
            text = " ".join(item.strip().split())
            if not text:
                continue
            cleaned.append(text[:ARRANGEMENT_MAX_REASON_CHARS])
        return cleaned


class ArrangementCandidate(BaseModel):
    """One independently generated, fully realized arrangement candidate."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(..., min_length=16, max_length=128)
    candidate_fingerprint: str = Field(..., min_length=16, max_length=128)
    edit_source_fingerprint: str = Field(..., min_length=16, max_length=128)
    algorithm_version: str = Field(
        default=ARRANGEMENT_ALGORITHM_VERSION,
        min_length=1,
        max_length=64,
    )
    catalog_version: str = Field(default=ARRANGEMENT_CATALOG_VERSION, min_length=1, max_length=64)
    range_policy_version: str = Field(
        default=ARRANGEMENT_RANGE_POLICY_VERSION,
        min_length=1,
        max_length=64,
    )
    catalog_fingerprint: str = Field(..., min_length=16, max_length=128)
    target_profile_fingerprints: list[ArrangementTargetProfileFingerprint] = Field(
        default_factory=list,
        max_length=ARRANGEMENT_MAX_TARGET_PROFILE_FINGERPRINTS,
    )
    operation: ArrangementOperation
    composition: CompositionV2
    provider: Literal["openai", "deepseek", "fake"]
    model: str | None = Field(default=None, max_length=120)
    before_inventory: list[ArrangementTrackInventoryItem] = Field(
        default_factory=list,
        max_length=ARRANGEMENT_MAX_TRACK_COUNT,
    )
    after_inventory: list[ArrangementTrackInventoryItem] = Field(
        default_factory=list,
        max_length=ARRANGEMENT_MAX_TRACK_COUNT,
    )
    manifest: ArrangementTopologyManifest = Field(default_factory=ArrangementTopologyManifest)
    event_counts: ArrangementEventCounts = Field(default_factory=ArrangementEventCounts)
    density: ArrangementDensitySummary | None = None
    range_findings: list[ArrangementRangeFinding] = Field(
        default_factory=list,
        max_length=ARRANGEMENT_MAX_RANGE_FINDINGS,
    )
    duplicate_findings: list[ArrangementDuplicateFinding] = Field(
        default_factory=list,
        max_length=ARRANGEMENT_MAX_DUPLICATE_FINDINGS,
    )
    harmony_compatibility: ArrangementHarmonyCompatibilitySummary | None = None
    assertions: list[ArrangementPreservationAssertion] = Field(
        default_factory=list,
        max_length=ARRANGEMENT_MAX_ASSERTIONS,
    )
    warning_codes: list[str] = Field(default_factory=list, max_length=ARRANGEMENT_MAX_WARNINGS)

    @field_validator("warning_codes")
    @classmethod
    def validate_warning_codes(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in value:
            code = item.strip()
            if not code or code in seen:
                continue
            if code not in ARRANGEMENT_WARNING_CODES:
                raise ValueError(f"Unknown arrangement warning code: {code}")
            seen.add(code)
            cleaned.append(code)
        return cleaned


class CompositionArrangementPreviewRequest(BaseModel):
    """Stateless multi-candidate arrangement preview — never mutates a project."""

    model_config = ConfigDict(extra="forbid")

    composition: CompositionV2
    operation: ArrangementOperation
    source_track_ids: list[str] = Field(
        ...,
        min_length=1,
        max_length=ARRANGEMENT_MAX_SOURCE_TRACKS,
    )
    protected_track_ids: list[str] = Field(
        default_factory=list,
        max_length=ARRANGEMENT_MAX_PROTECTED_TRACKS,
    )
    instrumentation: ArrangementInstrumentationRequirements
    allow_unlisted_after: bool = False
    preserve_melody: bool = True
    preserve_harmony: bool = True
    range_adjustment: RangeAdjustmentPolicy = "reject"
    candidate_count: int = Field(
        default=1,
        ge=ARRANGEMENT_MIN_CANDIDATE_COUNT,
        le=ARRANGEMENT_MAX_CANDIDATE_COUNT,
    )
    instruction: str | None = Field(default=None, max_length=ARRANGEMENT_MAX_INSTRUCTION_CHARS)
    selection: LLMModelSelection = Field(default_factory=LLMModelSelection)
    options: CompositionArrangementOptions = Field(default_factory=CompositionArrangementOptions)

    @field_validator("source_track_ids")
    @classmethod
    def validate_source_track_ids(cls, value: list[str]) -> list[str]:
        return _unique_normalized_ids(
            value,
            field_name="source_track_ids",
            model_name=cls.__name__,
        )

    @field_validator("protected_track_ids")
    @classmethod
    def validate_protected_track_ids(cls, value: list[str]) -> list[str]:
        return _unique_normalized_ids(
            value,
            field_name="protected_track_ids",
            model_name=cls.__name__,
        )

    @field_validator("instruction")
    @classmethod
    def validate_instruction(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = " ".join(value.strip().split())
        return text or None

    @model_validator(mode="after")
    def validate_operation_contract(self) -> CompositionArrangementPreviewRequest:
        source_set = set(self.source_track_ids)
        protected_set = set(self.protected_track_ids)
        overlap = sorted(source_set & protected_set)
        if overlap:
            _log_schema_validation_failure(
                self.__class__.__name__,
                "protected_track_ids",
                code="arrangement_source_protected_overlap",
                reason="source and protected overlap",
            )
            raise ValueError("source_track_ids and protected_track_ids must not intersect")

        before_sigs = sorted(_part_inventory_signature(p) for p in self.instrumentation.before)
        after_sigs = sorted(_part_inventory_signature(p) for p in self.instrumentation.after)
        if (
            self.operation in _OPERATIONS_REQUIRING_INSTRUMENTATION_CHANGE
            and before_sigs == after_sigs
        ):
            _log_schema_validation_failure(
                self.__class__.__name__,
                "instrumentation",
                code="arrangement_noop_instrumentation",
                reason="before and after inventories are identical",
            )
            raise ValueError(
                "before and after instrumentation must differ for this operation"
            )

        if self.operation == "remove_accompaniment":
            for part in self.instrumentation.before:
                if part.role is None:
                    continue
                if part.role not in ARRANGEMENT_ACCOMPANIMENT_ROLES:
                    _log_schema_validation_failure(
                        self.__class__.__name__,
                        "instrumentation.before",
                        code="arrangement_invalid_accompaniment_role",
                        reason=f"non-accompaniment role: {part.role}",
                    )
                    raise ValueError(
                        "remove_accompaniment requires accompaniment-role before parts "
                        "(harmony, pad, rhythm, or bass)"
                    )

        if self.operation == "piano_to_ensemble":
            if not any(_is_piano_instrument_id(p.instrument_id) for p in self.instrumentation.before):
                _log_schema_validation_failure(
                    self.__class__.__name__,
                    "instrumentation.before",
                    code="arrangement_piano_required",
                    reason="no piano instrument in before inventory",
                )
                raise ValueError("piano_to_ensemble requires at least one piano in before")
            if self.instrumentation.after_distinct_instrument_count < 2:
                _log_schema_validation_failure(
                    self.__class__.__name__,
                    "instrumentation.after",
                    code="arrangement_ensemble_targets_required",
                    reason="after needs >= 2 distinct instruments",
                )
                raise ValueError(
                    "piano_to_ensemble requires at least two distinct after instruments"
                )

        if self.operation == "create_countermelody":
            if not any(part.role == "countermelody" for part in self.instrumentation.after):
                _log_schema_validation_failure(
                    self.__class__.__name__,
                    "instrumentation.after",
                    code="arrangement_countermelody_required",
                    reason="missing countermelody role in after",
                )
                raise ValueError(
                    "create_countermelody requires at least one after part with role countermelody"
                )

        if self.operation == "double_melody":
            doubling_parts = [
                part
                for part in self.instrumentation.after
                if part.doubling_policy in AUTHORIZED_DOUBLING_POLICIES
            ]
            if len(doubling_parts) != 1:
                _log_schema_validation_failure(
                    self.__class__.__name__,
                    "instrumentation.after",
                    code="arrangement_doubling_required",
                    reason=f"expected 1 doubling relationship, got {len(doubling_parts)}",
                )
                raise ValueError(
                    "double_melody requires exactly one authorized doubling relationship"
                )

        return self


class CompositionArrangementPreviewResponse(BaseModel):
    """Multi-candidate preview; valid candidates and rejected attempts are separate."""

    model_config = ConfigDict(extra="forbid")

    edit_source_fingerprint: str = Field(..., min_length=16, max_length=128)
    algorithm_version: str = Field(
        default=ARRANGEMENT_ALGORITHM_VERSION,
        min_length=1,
        max_length=64,
    )
    catalog_version: str = Field(default=ARRANGEMENT_CATALOG_VERSION, min_length=1, max_length=64)
    range_policy_version: str = Field(
        default=ARRANGEMENT_RANGE_POLICY_VERSION,
        min_length=1,
        max_length=64,
    )
    catalog_fingerprint: str = Field(..., min_length=16, max_length=128)
    operation: ArrangementOperation
    requested_candidate_count: int = Field(
        ...,
        ge=ARRANGEMENT_MIN_CANDIDATE_COUNT,
        le=ARRANGEMENT_MAX_CANDIDATE_COUNT,
    )
    candidates: list[ArrangementCandidate] = Field(
        default_factory=list,
        max_length=ARRANGEMENT_MAX_CANDIDATE_COUNT,
    )
    rejected_attempts: list[ArrangementRejectedAttempt] = Field(
        default_factory=list,
        max_length=ARRANGEMENT_MAX_REJECTED_ATTEMPTS,
    )
    warning_codes: list[str] = Field(default_factory=list, max_length=ARRANGEMENT_MAX_WARNINGS)
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
            if code not in ARRANGEMENT_WARNING_CODES:
                raise ValueError(f"Unknown arrangement warning code: {code}")
            seen.add(code)
            cleaned.append(code)
        return cleaned

    @model_validator(mode="after")
    def validate_response_consistency(self) -> CompositionArrangementPreviewResponse:
        if not self.candidates and not self.rejected_attempts:
            raise ValueError("preview response requires candidates and/or rejected_attempts")

        candidate_ids = [candidate.candidate_id for candidate in self.candidates]
        duplicates = sorted({item for item in candidate_ids if candidate_ids.count(item) > 1})
        if duplicates:
            raise ValueError("candidate_id values must be unique within a response")

        for candidate in self.candidates:
            if candidate.edit_source_fingerprint != self.edit_source_fingerprint:
                raise ValueError("all candidates must share edit_source_fingerprint")
            if candidate.catalog_fingerprint != self.catalog_fingerprint:
                raise ValueError("all candidates must share catalog_fingerprint")
            if candidate.operation != self.operation:
                raise ValueError("candidate operation must match response operation")
            if candidate.algorithm_version != self.algorithm_version:
                raise ValueError("candidate algorithm_version must match response")
            if candidate.catalog_version != self.catalog_version:
                raise ValueError("candidate catalog_version must match response")
            if candidate.range_policy_version != self.range_policy_version:
                raise ValueError("candidate range_policy_version must match response")
        return self


def validate_arrangement_request_limits(
    request: CompositionArrangementPreviewRequest,
) -> None:
    """Reject oversized compositions before any provider call."""
    composition = request.composition
    event_count = sum(len(track.events) for track in composition.tracks)
    motif_count = len(composition.motifs)
    occurrence_count = sum(len(motif.occurrences) for motif in composition.motifs)

    if (
        event_count > ARRANGEMENT_MAX_EVENT_COUNT
        or len(composition.tracks) > ARRANGEMENT_MAX_TRACK_COUNT
        or len(composition.sections) > ARRANGEMENT_MAX_SECTION_COUNT
        or motif_count > ARRANGEMENT_MAX_MOTIF_DEFINITIONS
    ):
        logger.info(
            "Rejected oversized composition arrangement request",
            extra={
                "error_code": "arrangement_request_too_large",
                "event_count": event_count,
                "track_count": len(composition.tracks),
                "section_count": len(composition.sections),
                "motif_count": motif_count,
                "occurrence_count": occurrence_count,
                "candidate_count": request.candidate_count,
                "before_part_count": request.instrumentation.before_part_count,
                "after_part_count": request.instrumentation.after_part_count,
            },
        )
        raise CompositionArrangementError(
            "arrangement_request_too_large",
            http_status=422,
            details={
                "event_count": event_count,
                "track_count": len(composition.tracks),
                "section_count": len(composition.sections),
                "motif_count": motif_count,
                "max_event_count": ARRANGEMENT_MAX_EVENT_COUNT,
                "max_track_count": ARRANGEMENT_MAX_TRACK_COUNT,
                "max_section_count": ARRANGEMENT_MAX_SECTION_COUNT,
                "max_motif_definitions": ARRANGEMENT_MAX_MOTIF_DEFINITIONS,
            },
        )


def normalized_arrangement_request_fingerprint_payload(
    request: CompositionArrangementPreviewRequest,
) -> dict[str, Any]:
    """Stable operation identity without the composition body."""
    return {
        "algorithm_version": ARRANGEMENT_ALGORITHM_VERSION,
        "edit_fingerprint_profile": EDIT_FINGERPRINT_PROFILE,
        "operation": request.operation,
        "source_track_ids": list(request.source_track_ids),
        "protected_track_ids": list(request.protected_track_ids),
        "instrumentation": request.instrumentation.model_dump(mode="json"),
        "allow_unlisted_after": request.allow_unlisted_after,
        "preserve_melody": request.preserve_melody,
        "preserve_harmony": request.preserve_harmony,
        "range_adjustment": request.range_adjustment,
        "candidate_count": request.candidate_count,
        "instruction": request.instruction,
        "selection": request.selection.model_dump(mode="json", exclude_none=True),
        "options": request.options.model_dump(mode="json"),
    }
