"""Strict composition.analysis.v1 sidecar DTOs, scopes, and validation helpers.

The analysis report is derived, cacheable, and never authoritative composition data.
It must not be submitted as composition_json, persisted in project rows, or consumed by
export/playback. There is no timestamp and no random IDs.
"""

from __future__ import annotations

import hashlib
import logging
import math
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from .composition_schemas import (
    COMPOSITION_SCHEMA_VERSION_V2,
    CompositionV2,
    CompositionV2Section,
    CompositionV2Track,
)


logger = logging.getLogger(__name__)

# --- Contract identity ---------------------------------------------------------

ANALYSIS_SCHEMA_VERSION: Literal["composition.analysis.v1"] = "composition.analysis.v1"
# Semantic algorithm/profile version for report identity and frontend freshness.
ANALYSIS_ALGORITHM_VERSION = "analysis.native.v2"
ANALYSIS_FINGERPRINT_PROFILE = "analysis.source.v1"

# --- Caps / rounding -----------------------------------------------------------

ANALYSIS_FLOAT_PRECISION = 6
ANALYSIS_MAX_EVIDENCE_ITEMS = 32
ANALYSIS_MAX_WARNING_DETAILS_KEYS = 16
ANALYSIS_MAX_WARNING_DETAIL_LIST = 32
ANALYSIS_MAX_WARNING_DETAIL_STR = 200
ANALYSIS_MAX_CANDIDATES = 24
ANALYSIS_MAX_SPANS = 512
ANALYSIS_MAX_PHRASES = 256
ANALYSIS_MAX_MOTIFS = 64
ANALYSIS_MAX_WARNINGS = 256
ANALYSIS_MAX_SECTION_SUMMARIES = 512
ANALYSIS_MAX_TRACK_SUMMARIES = 64
ANALYSIS_FINGERPRINT_LOG_PREFIX_LEN = 12

AnalysisScopeKind = Literal["composition", "section", "track"]
InferenceStatus = Literal[
    "ok",
    "ambiguous",
    "insufficient_evidence",
    "not_applicable",
    "truncated",
]
AnalysisReportStatus = Literal["ok", "partial", "empty", "failed"]
AnalysisWarningSeverity = Literal["info", "warning", "error"]
AnalysisWarningCategory = Literal[
    "data_quality",
    "timing",
    "metadata_conflict",
    "ambiguity",
    "limitation",
    "range",
    "density",
]

# Closed stable warning-code registry (messages are defaults; analyzers may refine).
ANALYSIS_WARNING_CODES: dict[str, str] = {
    "note_outside_instrument_range": "One or more notes fall outside the practical instrument range.",
    "dense_overlapping_material": "Conspicuous concurrent note load exceeded the objective density threshold.",
    "empty_analysis_scope": "The selected scope contains no overlapping logical note attacks.",
    "timing_grid_anomaly": "Valid but suspicious timing relative to the meter grid was detected.",
    "overlapping_same_pitch_timing": "Same-pitch notes overlap heavily within a track.",
    "declared_key_conflicts_with_inference": "Declared root key conflicts with inferred tonality beyond evidence margins.",
    "declared_key_change_conflicts_with_inference": "A declared key change conflicts with inferred local tonality.",
    "declared_harmony_conflicts_with_inference": "Declared harmony conflicts with inferred chord spans.",
    "declared_harmony_unparseable": "A declared harmony symbol could not be parsed for comparison.",
    "excessive_duplicate_notes": "Exact duplicate notes within a track exceeded the warning threshold.",
    "result_truncated": "Analysis result arrays were truncated to configured caps.",
    "evidence_truncated": "Evidence locators were truncated to configured caps.",
    "melody_skyline_reduction": "Polyphonic melody was reduced with a deterministic skyline approximation.",
    "motif_search_truncated": "Motif search stopped at the configured search/result cap.",
    "relative_key_ambiguity": "Relative major/minor candidates remain close; inference is ambiguous.",
    "insufficient_tonal_evidence": "Tonal inference abstained due to insufficient pitched evidence.",
    "unsupported_sustain_interpretation": "Sustain pedals are not interpreted in sounding-duration analysis v1.",
    "percussion_only_scope": "Scope contains only percussion/drum material for pitched analysis.",
}

AnalysisWarningCode = Literal[
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
    "result_truncated",
    "evidence_truncated",
    "melody_skyline_reduction",
    "motif_search_truncated",
    "relative_key_ambiguity",
    "insufficient_tonal_evidence",
    "unsupported_sustain_interpretation",
    "percussion_only_scope",
]

ANALYSIS_ERROR_CODES: dict[str, str] = {
    "analysis_invalid_composition": "Composition is not a valid composition.v2 document for analysis.",
    "analysis_invalid_scope": "Analysis scope selectors are missing, ambiguous, or out of range.",
    "analysis_complexity_exceeded": "Composition exceeds configured analysis size limits.",
    "analysis_internal_error": "Unexpected analysis failure (sanitized).",
}

AnalysisErrorCode = Literal[
    "analysis_invalid_composition",
    "analysis_invalid_scope",
    "analysis_complexity_exceeded",
    "analysis_internal_error",
]


class CompositionAnalysisError(Exception):
    """Domain error for analysis request validation mapped to structured HTTP 422."""

    def __init__(
        self,
        code: AnalysisErrorCode,
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
            "Composition analysis domain error",
            extra={
                "error_code": code,
                "http_status": http_status,
                "error_type": type(self).__name__,
                "detail_keys": sorted(self.details.keys()),
            },
        )


def round_analysis_float(value: float, *, precision: int = ANALYSIS_FLOAT_PRECISION) -> float:
    """Shared fixed-precision rounding (Python half-even / banker's rounding).

    Rejects NaN and infinities so report payloads stay JSON-safe and deterministic.
    """
    if not math.isfinite(value):
        raise ValueError("analysis floats must be finite (no NaN/infinity)")
    return round(float(value), precision)


def make_derived_id(*parts: Any) -> str:
    """Deterministic derived ID from stable scalar parts (no randomness)."""
    normalized = [str(part).strip().replace(" ", "_") for part in parts if part is not None]
    if not normalized:
        raise ValueError("derived id requires at least one part")
    return ":".join(normalized)[:200]


def make_sha256_derived_id(prefix: str, *parts: Any) -> str:
    """Fixed-length SHA-256 digest ID for bounded motif family/occurrence identity."""
    if not prefix.strip():
        raise ValueError("sha256 derived id requires a non-empty prefix")
    normalized = "|".join(str(part) for part in parts if part is not None)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}:{digest}"


# --- Shared inference / evidence ----------------------------------------------


class AnalysisSourceLocator(BaseModel):
    """Bounded locator; never carries full event payloads or freeform prose dumps."""

    model_config = ConfigDict(extra="forbid")

    track_id: str | None = Field(default=None, min_length=1, max_length=80)
    track_index: int | None = Field(default=None, ge=0)
    section_id: str | None = Field(default=None, min_length=1, max_length=120)
    section_index: int | None = Field(default=None, ge=0)
    event_id: str | None = Field(default=None, min_length=1, max_length=120)
    start_tick: int | None = Field(default=None, ge=0)
    end_tick: int | None = Field(default=None, ge=0)
    start_bar: int | None = Field(default=None, ge=1)
    end_bar: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_bounds(self) -> AnalysisSourceLocator:
        if self.start_tick is not None and self.end_tick is not None and self.end_tick < self.start_tick:
            raise ValueError("locator end_tick must be >= start_tick")
        if self.start_bar is not None and self.end_bar is not None and self.end_bar < self.start_bar:
            raise ValueError("locator end_bar must be >= start_bar")
        return self


class AnalysisEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int = Field(default=0, ge=0)
    mass: float | None = None
    coverage: float | None = None
    locators: list[AnalysisSourceLocator] = Field(default_factory=list)

    @field_validator("mass", "coverage")
    @classmethod
    def validate_finite_optional(cls, value: float | None) -> float | None:
        if value is None:
            return None
        return round_analysis_float(value)

    @field_validator("locators")
    @classmethod
    def validate_locator_cap(cls, value: list[AnalysisSourceLocator]) -> list[AnalysisSourceLocator]:
        if len(value) > ANALYSIS_MAX_EVIDENCE_ITEMS:
            raise ValueError(f"evidence locators limited to {ANALYSIS_MAX_EVIDENCE_ITEMS}")
        return value


class InferenceMeta(BaseModel):
    """Common inference envelope: status, confidence, evidence, method identity."""

    model_config = ConfigDict(extra="forbid")

    status: InferenceStatus = "ok"
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence: AnalysisEvidence = Field(default_factory=AnalysisEvidence)
    method: str = Field(default=ANALYSIS_ALGORITHM_VERSION, min_length=1, max_length=80)
    method_version: str = Field(default=ANALYSIS_ALGORITHM_VERSION, min_length=1, max_length=80)

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, value: float | None) -> float | None:
        if value is None:
            return None
        return round_analysis_float(value)


# --- Request scopes -----------------------------------------------------------


class CompositionAnalysisScopeComposition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["composition"] = "composition"


class CompositionAnalysisScopeSection(BaseModel):
    """Select a section by canonical array index; optional id/bounds verify identity."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["section"] = "section"
    section_index: int = Field(..., ge=0)
    section_id: str | None = Field(default=None, min_length=1, max_length=120)
    expected_start_bar: int | None = Field(default=None, ge=1)
    expected_bar_count: int | None = Field(default=None, ge=1)
    expected_start_tick: int | None = Field(default=None, ge=0)
    expected_duration_ticks: int | None = Field(default=None, gt=0)


class CompositionAnalysisScopeTrack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["track"] = "track"
    track_id: str = Field(..., min_length=1, max_length=80)


CompositionAnalysisScope = Annotated[
    CompositionAnalysisScopeComposition
    | CompositionAnalysisScopeSection
    | CompositionAnalysisScopeTrack,
    Field(discriminator="kind"),
]


class CompositionAnalysisRequest(BaseModel):
    """Analyze the complete current V2 document for a single scope.

    Analysis is request-scoped over unsaved editedMusicJson; there is no project-id
    authority in this endpoint.
    """

    model_config = ConfigDict(extra="forbid")

    composition: CompositionV2
    scope: CompositionAnalysisScope = Field(default_factory=CompositionAnalysisScopeComposition)


class ResolvedAnalysisScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: AnalysisScopeKind
    start_tick: int = Field(..., ge=0)
    end_tick: int = Field(..., ge=0)
    start_bar: int = Field(..., ge=1)
    end_bar_exclusive: int = Field(..., ge=1)
    track_id: str | None = Field(default=None, min_length=1, max_length=80)
    track_index: int | None = Field(default=None, ge=0)
    section_id: str | None = Field(default=None, min_length=1, max_length=120)
    section_index: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_interval(self) -> ResolvedAnalysisScope:
        if self.end_tick < self.start_tick:
            raise ValueError("resolved scope end_tick must be >= start_tick")
        if self.end_bar_exclusive < self.start_bar:
            raise ValueError("resolved scope end_bar_exclusive must be >= start_bar")
        return self


# --- Result groups (filled by later analyzer tasks) ---------------------------


class KeyCandidateScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(..., min_length=1, max_length=40)
    score: float
    pitch_class: int = Field(..., ge=0, le=11)
    mode: Literal["major", "minor"]

    @field_validator("score")
    @classmethod
    def validate_score(cls, value: float) -> float:
        return round_analysis_float(value)


class KeySpanResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, max_length=200)
    start_tick: int = Field(..., ge=0)
    end_tick: int = Field(..., ge=0)
    start_bar: int = Field(..., ge=1)
    end_bar_exclusive: int = Field(..., ge=1)
    key: str | None = Field(default=None, max_length=40)
    inference: InferenceMeta = Field(default_factory=InferenceMeta)
    candidates: list[KeyCandidateScore] = Field(default_factory=list)
    declared_key: str | None = Field(default=None, max_length=40)

    @field_validator("candidates")
    @classmethod
    def validate_candidate_cap(cls, value: list[KeyCandidateScore]) -> list[KeyCandidateScore]:
        if len(value) > ANALYSIS_MAX_CANDIDATES:
            raise ValueError(f"key candidates limited to {ANALYSIS_MAX_CANDIDATES}")
        return value


class TonalityAnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    global_key: KeySpanResult | None = None
    local_spans: list[KeySpanResult] = Field(default_factory=list)
    declared_key: str | None = Field(default=None, max_length=40)
    effective_key: str | None = Field(default=None, max_length=40)
    inference: InferenceMeta = Field(default_factory=InferenceMeta)

    @field_validator("local_spans")
    @classmethod
    def validate_span_cap(cls, value: list[KeySpanResult]) -> list[KeySpanResult]:
        if len(value) > ANALYSIS_MAX_SPANS:
            raise ValueError(f"local key spans limited to {ANALYSIS_MAX_SPANS}")
        return value


class ChordSpanResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, max_length=200)
    start_tick: int = Field(..., ge=0)
    end_tick: int = Field(..., ge=0)
    symbol: str | None = Field(default=None, max_length=40)
    root_pc: int | None = Field(default=None, ge=0, le=11)
    quality: str | None = Field(default=None, max_length=40)
    bass_pc: int | None = Field(default=None, ge=0, le=11)
    roman: str | None = Field(default=None, max_length=24)
    function: str | None = Field(default=None, max_length=24)
    inference: InferenceMeta = Field(default_factory=InferenceMeta)


class HarmonicRhythmMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    changes_per_bar: float | None = None
    changes_per_quarter: float | None = None
    unique_chord_count: int = Field(default=0, ge=0)

    @field_validator("changes_per_bar", "changes_per_quarter")
    @classmethod
    def validate_rates(cls, value: float | None) -> float | None:
        if value is None:
            return None
        return round_analysis_float(value)


class HarmonyAnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spans: list[ChordSpanResult] = Field(default_factory=list)
    harmonic_rhythm: HarmonicRhythmMetrics = Field(default_factory=HarmonicRhythmMetrics)
    declared_agreement: Literal[
        "agreement",
        "partial_agreement",
        "conflict",
        "unparseable",
        "insufficient_evidence",
        "not_applicable",
    ] = "not_applicable"
    inference: InferenceMeta = Field(default_factory=InferenceMeta)

    @field_validator("spans")
    @classmethod
    def validate_span_cap(cls, value: list[ChordSpanResult]) -> list[ChordSpanResult]:
        if len(value) > ANALYSIS_MAX_SPANS:
            raise ValueError(f"chord spans limited to {ANALYSIS_MAX_SPANS}")
        return value


class ScaleDegreeBucket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    degree: int = Field(..., ge=1, le=7)
    alteration: int = Field(default=0, ge=-2, le=2)
    count: int = Field(default=0, ge=0)
    mass: float | None = None

    @field_validator("mass")
    @classmethod
    def validate_mass(cls, value: float | None) -> float | None:
        if value is None:
            return None
        return round_analysis_float(value)


class ScaleDegreeAnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    buckets: list[ScaleDegreeBucket] = Field(default_factory=list)
    inference: InferenceMeta = Field(default_factory=InferenceMeta)


class MelodicProfileResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    track_id: str | None = Field(default=None, max_length=80)
    pitch_min: str | None = Field(default=None, max_length=5)
    pitch_max: str | None = Field(default=None, max_length=5)
    range_semitones: int | None = Field(default=None, ge=0)
    tessitura_midi: float | None = None
    contour: Literal["ascending", "descending", "arch", "static", "mixed", "unknown"] = "unknown"
    net_displacement_semitones: int | None = None
    direction_changes: int = Field(default=0, ge=0)
    skyline_reduced: bool = False
    inference: InferenceMeta = Field(default_factory=InferenceMeta)

    @field_validator("tessitura_midi")
    @classmethod
    def validate_tessitura(cls, value: float | None) -> float | None:
        if value is None:
            return None
        return round_analysis_float(value)


class PhraseSpanResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, max_length=200)
    start_tick: int = Field(..., ge=0)
    end_tick: int = Field(..., ge=0)
    inference: InferenceMeta = Field(default_factory=InferenceMeta)


class CadenceResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, max_length=200)
    kind: Literal[
        "authentic_perfect",
        "authentic_imperfect",
        "half",
        "plagal",
        "deceptive",
        "unclassified",
    ] = "unclassified"
    tick: int = Field(..., ge=0)
    bar: int | None = Field(default=None, ge=1)
    inference: InferenceMeta = Field(default_factory=InferenceMeta)


class MelodyAnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profiles: list[MelodicProfileResult] = Field(default_factory=list)
    phrases: list[PhraseSpanResult] = Field(default_factory=list)
    cadences: list[CadenceResult] = Field(default_factory=list)
    inference: InferenceMeta = Field(default_factory=InferenceMeta)

    @field_validator("phrases")
    @classmethod
    def validate_phrase_cap(cls, value: list[PhraseSpanResult]) -> list[PhraseSpanResult]:
        if len(value) > ANALYSIS_MAX_PHRASES:
            raise ValueError(f"phrases limited to {ANALYSIS_MAX_PHRASES}")
        return value


class DensityMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attacks_per_quarter: float | None = None
    attacks_per_bar: float | None = None
    median_inter_onset_ticks: float | None = None
    active_time_union_ratio: float | None = None
    note_load: float | None = None
    mean_simultaneity: float | None = None
    max_simultaneity: int = Field(default=0, ge=0)
    distinct_pitch_class_density: float | None = None
    chord_changes: int = Field(default=0, ge=0)
    active_track_ratio: float | None = None
    section_relative_density: float | None = None

    @field_validator(
        "attacks_per_quarter",
        "attacks_per_bar",
        "median_inter_onset_ticks",
        "active_time_union_ratio",
        "note_load",
        "mean_simultaneity",
        "distinct_pitch_class_density",
        "active_track_ratio",
        "section_relative_density",
    )
    @classmethod
    def validate_metrics(cls, value: float | None) -> float | None:
        if value is None:
            return None
        return round_analysis_float(value)


class DensityAnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metrics: DensityMetrics = Field(default_factory=DensityMetrics)
    inference: InferenceMeta = Field(default_factory=InferenceMeta)


class TrackRoleEstimate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    track_id: str = Field(..., min_length=1, max_length=80)
    declared_role: str | None = Field(default=None, max_length=40)
    inferred_role: str | None = Field(default=None, max_length=40)
    effective_role: str | None = Field(default=None, max_length=40)
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    inference: InferenceMeta = Field(default_factory=InferenceMeta)

    @field_validator("candidates")
    @classmethod
    def validate_candidates(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(value) > ANALYSIS_MAX_CANDIDATES:
            raise ValueError(f"role candidates limited to {ANALYSIS_MAX_CANDIDATES}")
        for item in value:
            if len(item) > ANALYSIS_MAX_WARNING_DETAILS_KEYS:
                raise ValueError("role candidate detail keys exceed bound")
        return value


class RoleAnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tracks: list[TrackRoleEstimate] = Field(default_factory=list)
    inference: InferenceMeta = Field(default_factory=InferenceMeta)

    @field_validator("tracks")
    @classmethod
    def validate_track_cap(cls, value: list[TrackRoleEstimate]) -> list[TrackRoleEstimate]:
        if len(value) > ANALYSIS_MAX_TRACK_SUMMARIES:
            raise ValueError(f"track role estimates limited to {ANALYSIS_MAX_TRACK_SUMMARIES}")
        return value


DetectedMotifRelationshipKind = Literal[
    "exact",
    "transposed",
    "rhythm_only",
    "inversion",
    "augmentation",
    "diminution",
    "sequence",
]

# Shared bound for motif note spans (analysis repetition + occurrence notes).
MAX_MOTIF_NOTES_BOUND = 12


class DetectedNoteReference(BaseModel):
    """Fingerprint-bound locator for one logical note in a detected occurrence."""

    model_config = ConfigDict(extra="forbid")

    event_ids: list[str] | None = Field(
        default=None,
        description="Canonical event IDs when present on the source composition.",
    )
    event_indexes: list[int] | None = Field(
        default=None,
        description="Fallback indexes into track.events when IDs are absent.",
    )

    @field_validator("event_ids")
    @classmethod
    def validate_event_ids(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        if len(value) > ANALYSIS_MAX_EVIDENCE_ITEMS:
            raise ValueError(f"note event_ids limited to {ANALYSIS_MAX_EVIDENCE_ITEMS}")
        return value

    @field_validator("event_indexes")
    @classmethod
    def validate_event_indexes(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return None
        if len(value) > ANALYSIS_MAX_EVIDENCE_ITEMS:
            raise ValueError(f"note event_indexes limited to {ANALYSIS_MAX_EVIDENCE_ITEMS}")
        for index in value:
            if index < 0:
                raise ValueError("event_indexes must be >= 0")
        return value

    @model_validator(mode="after")
    def validate_has_reference(self) -> DetectedNoteReference:
        if not self.event_ids and not self.event_indexes:
            raise ValueError("note reference requires event_ids or event_indexes")
        return self


class DetectedMotifOccurrence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, max_length=80)
    kind: DetectedMotifRelationshipKind
    track_id: str | None = Field(default=None, max_length=80)
    start_tick: int = Field(..., ge=0)
    end_tick: int = Field(..., ge=0)
    note_count: int = Field(..., ge=1)
    identity_score: float = Field(..., ge=0.0, le=1.0)
    transposition_semitones: int | None = None
    time_scale_numerator: int | None = Field(default=None, ge=1, le=8)
    time_scale_denominator: int | None = Field(default=None, ge=1, le=8)
    notes: list[DetectedNoteReference] = Field(default_factory=list)
    inference: InferenceMeta = Field(default_factory=InferenceMeta)

    @field_validator("identity_score")
    @classmethod
    def validate_identity_score(cls, value: float) -> float:
        return round_analysis_float(value)

    @field_validator("notes")
    @classmethod
    def validate_notes_cap(
        cls, value: list[DetectedNoteReference]
    ) -> list[DetectedNoteReference]:
        if len(value) > MAX_MOTIF_NOTES_BOUND:
            raise ValueError(f"occurrence notes limited to {MAX_MOTIF_NOTES_BOUND}")
        return value


class DetectedMotifFamily(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, max_length=80)
    reference: DetectedMotifOccurrence
    matched_occurrences: list[DetectedMotifOccurrence] = Field(default_factory=list)
    relationship_kinds: list[DetectedMotifRelationshipKind] = Field(default_factory=list)
    note_count: int = Field(..., ge=1)
    inference: InferenceMeta = Field(default_factory=InferenceMeta)

    @field_validator("matched_occurrences")
    @classmethod
    def validate_matched_cap(
        cls, value: list[DetectedMotifOccurrence]
    ) -> list[DetectedMotifOccurrence]:
        # Keep family payloads bounded for analysis.v1 response size caps.
        if len(value) > ANALYSIS_MAX_EVIDENCE_ITEMS:
            raise ValueError(f"matched occurrences limited to {ANALYSIS_MAX_EVIDENCE_ITEMS}")
        return value

    @field_validator("relationship_kinds")
    @classmethod
    def validate_kinds_cap(
        cls, value: list[DetectedMotifRelationshipKind]
    ) -> list[DetectedMotifRelationshipKind]:
        if len(value) > 8:
            raise ValueError("relationship_kinds limited to 8 entries")
        return value


class MotifOccurrence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, max_length=200)
    kind: Literal["exact", "transposed", "rhythm_only"]
    track_id: str | None = Field(default=None, max_length=80)
    start_tick: int = Field(..., ge=0)
    end_tick: int = Field(..., ge=0)
    transposition_semitones: int | None = None
    inference: InferenceMeta = Field(default_factory=InferenceMeta)


class RepetitionAnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    motifs: list[MotifOccurrence] = Field(
        default_factory=list,
        description=(
            "Flattened matched targets for backward-compatible consumers. "
            "Prefer motif_families for grouping and reference/match pairing."
        ),
    )
    motif_families: list[DetectedMotifFamily] = Field(
        default_factory=list,
        description=(
            "Authoritative motif grouping: each family contains a reference occurrence "
            "and all matched occurrences with actionable note references."
        ),
    )
    section_fingerprint_ids: list[str] = Field(default_factory=list)
    inference: InferenceMeta = Field(default_factory=InferenceMeta)

    @field_validator("motifs")
    @classmethod
    def validate_motif_cap(cls, value: list[MotifOccurrence]) -> list[MotifOccurrence]:
        if len(value) > ANALYSIS_MAX_MOTIFS:
            raise ValueError(f"motifs limited to {ANALYSIS_MAX_MOTIFS}")
        return value

    @field_validator("motif_families")
    @classmethod
    def validate_family_cap(
        cls, value: list[DetectedMotifFamily]
    ) -> list[DetectedMotifFamily]:
        if len(value) > ANALYSIS_MAX_MOTIFS:
            raise ValueError(f"motif_families limited to {ANALYSIS_MAX_MOTIFS}")
        return value


class TensionComponents(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vertical_dissonance: float | None = None
    chromatic_mass: float | None = None
    non_chord_tone_mass: float | None = None
    functional_distance: float | None = None
    unresolved_tendency: float | None = None

    @field_validator(
        "vertical_dissonance",
        "chromatic_mass",
        "non_chord_tone_mass",
        "functional_distance",
        "unresolved_tendency",
    )
    @classmethod
    def validate_components(cls, value: float | None) -> float | None:
        if value is None:
            return None
        return round_analysis_float(value)


class TensionAnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    components: TensionComponents = Field(default_factory=TensionComponents)
    limitations: list[str] = Field(default_factory=list)
    inference: InferenceMeta = Field(default_factory=InferenceMeta)

    @field_validator("limitations")
    @classmethod
    def validate_limitations(cls, value: list[str]) -> list[str]:
        if len(value) > ANALYSIS_MAX_EVIDENCE_ITEMS:
            raise ValueError("tension limitations exceed bound")
        return [item[:80] for item in value]


class SectionSummaryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_index: int = Field(..., ge=0)
    section_id: str | None = Field(default=None, max_length=120)
    section_type: str | None = Field(default=None, max_length=40)
    start_tick: int = Field(..., ge=0)
    end_tick: int = Field(..., ge=0)
    attack_count: int = Field(default=0, ge=0)
    note_load: float | None = None
    inferred_key: str | None = Field(default=None, max_length=40)

    @field_validator("note_load")
    @classmethod
    def validate_note_load(cls, value: float | None) -> float | None:
        if value is None:
            return None
        return round_analysis_float(value)


class AnalysisWarning(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: AnalysisWarningCode
    severity: AnalysisWarningSeverity = "warning"
    category: AnalysisWarningCategory = "data_quality"
    message: str = Field(..., min_length=1, max_length=500)
    locator: AnalysisSourceLocator | None = None
    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("analysis warning message must not be empty")
        return normalized

    @field_validator("details")
    @classmethod
    def validate_bounded_details(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(value) > ANALYSIS_MAX_WARNING_DETAILS_KEYS:
            raise ValueError(
                f"analysis warning details are limited to {ANALYSIS_MAX_WARNING_DETAILS_KEYS} keys"
            )
        for key, item in value.items():
            if isinstance(item, float) and not math.isfinite(item):
                raise ValueError(f"analysis warning detail {key!r} must be finite")
            if isinstance(item, (list, dict)) and len(item) > ANALYSIS_MAX_WARNING_DETAIL_LIST:
                raise ValueError(f"analysis warning detail {key!r} exceeds bounded size")
            if isinstance(item, str) and len(item) > ANALYSIS_MAX_WARNING_DETAIL_STR:
                raise ValueError(f"analysis warning detail {key!r} exceeds bounded length")
        return value


class CompositionAnalysisReport(BaseModel):
    """Derived musical analysis sidecar (composition.analysis.v1).

    Not authoritative composition data. Cacheable by source_fingerprint +
    algorithm_version + resolved scope. Contains no timestamps.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["composition.analysis.v1"] = ANALYSIS_SCHEMA_VERSION
    algorithm_version: str = Field(default=ANALYSIS_ALGORITHM_VERSION, min_length=1, max_length=80)
    source_schema_version: Literal["composition.v2"] = COMPOSITION_SCHEMA_VERSION_V2
    source_fingerprint: str = Field(..., min_length=16, max_length=128)
    status: AnalysisReportStatus = "ok"
    resolved_scope: ResolvedAnalysisScope
    tonality: TonalityAnalysisResult = Field(default_factory=TonalityAnalysisResult)
    harmony: HarmonyAnalysisResult = Field(default_factory=HarmonyAnalysisResult)
    scale_degrees: ScaleDegreeAnalysisResult = Field(default_factory=ScaleDegreeAnalysisResult)
    melody: MelodyAnalysisResult = Field(default_factory=MelodyAnalysisResult)
    density: DensityAnalysisResult = Field(default_factory=DensityAnalysisResult)
    roles: RoleAnalysisResult = Field(default_factory=RoleAnalysisResult)
    repetition: RepetitionAnalysisResult = Field(default_factory=RepetitionAnalysisResult)
    tension: TensionAnalysisResult = Field(default_factory=TensionAnalysisResult)
    section_summaries: list[SectionSummaryResult] = Field(default_factory=list)
    warnings: list[AnalysisWarning] = Field(default_factory=list)

    @field_validator("section_summaries")
    @classmethod
    def validate_section_cap(cls, value: list[SectionSummaryResult]) -> list[SectionSummaryResult]:
        if len(value) > ANALYSIS_MAX_SECTION_SUMMARIES:
            raise ValueError(f"section summaries limited to {ANALYSIS_MAX_SECTION_SUMMARIES}")
        return value

    @field_validator("warnings")
    @classmethod
    def validate_warning_cap(cls, value: list[AnalysisWarning]) -> list[AnalysisWarning]:
        if len(value) > ANALYSIS_MAX_WARNINGS:
            raise ValueError(f"warnings limited to {ANALYSIS_MAX_WARNINGS}")
        return value


# --- Validation / scope resolution --------------------------------------------


def revalidate_composition_v2(composition: CompositionV2 | dict[str, Any] | Any) -> CompositionV2:
    """Serialize and revalidate so mutated model instances cannot bypass invariants."""
    try:
        if isinstance(composition, CompositionV2):
            payload = composition.model_dump(mode="json")
        elif isinstance(composition, dict):
            payload = composition
        else:
            raise CompositionAnalysisError(
                "analysis_invalid_composition",
                "Composition must be composition.v2 JSON or a CompositionV2 model",
                details={"reason": "unsupported_input_type"},
            )

        schema_version = payload.get("schema_version") if isinstance(payload, dict) else None
        if schema_version != COMPOSITION_SCHEMA_VERSION_V2:
            logger.warning(
                "Analysis rejected non-v2 composition",
                extra={
                    "rejection_code": "analysis_invalid_composition",
                    "schema_version": schema_version,
                },
            )
            raise CompositionAnalysisError(
                "analysis_invalid_composition",
                "Analysis accepts only composition.v2 documents",
                details={"schema_version": schema_version, "reason": "non_v2"},
            )

        validated = CompositionV2.model_validate(payload)
        logger.debug(
            "Analysis composition revalidated",
            extra={
                "schema_version": validated.schema_version,
                "track_count": len(validated.tracks),
                "section_count": len(validated.sections),
                "note_count": sum(len(track.events) for track in validated.tracks),
                "bar_count": validated.bar_count,
            },
        )
        return validated
    except CompositionAnalysisError:
        raise
    except Exception as exc:
        logger.error(
            "Analysis composition validation failed",
            extra={"error_type": type(exc).__name__},
        )
        raise CompositionAnalysisError(
            "analysis_invalid_composition",
            "Composition failed composition.v2 validation",
            details={"reason": "validation_error", "error_type": type(exc).__name__},
        ) from exc


def _section_matches_expectations(
    section: CompositionV2Section,
    scope: CompositionAnalysisScopeSection,
) -> list[str]:
    mismatches: list[str] = []
    if scope.section_id is not None and section.id != scope.section_id:
        mismatches.append("section_id")
    if scope.expected_start_bar is not None and section.start_bar != scope.expected_start_bar:
        mismatches.append("start_bar")
    if scope.expected_bar_count is not None and section.bar_count != scope.expected_bar_count:
        mismatches.append("bar_count")
    if scope.expected_start_tick is not None and section.start_tick != scope.expected_start_tick:
        mismatches.append("start_tick")
    if (
        scope.expected_duration_ticks is not None
        and section.duration_ticks != scope.expected_duration_ticks
    ):
        mismatches.append("duration_ticks")
    return mismatches


def resolve_analysis_scope(
    composition: CompositionV2,
    scope: CompositionAnalysisScope,
) -> ResolvedAnalysisScope:
    """Resolve composition/section/track scope to half-open tick and bar ranges."""
    if isinstance(scope, CompositionAnalysisScopeComposition) or getattr(scope, "kind", None) == "composition":
        resolved = ResolvedAnalysisScope(
            kind="composition",
            start_tick=0,
            end_tick=composition.duration_ticks,
            start_bar=1,
            end_bar_exclusive=composition.bar_count + 1,
        )
        logger.debug(
            "Resolved composition analysis scope",
            extra={
                "scope_kind": resolved.kind,
                "start_tick": resolved.start_tick,
                "end_tick": resolved.end_tick,
            },
        )
        return resolved

    if isinstance(scope, CompositionAnalysisScopeSection) or getattr(scope, "kind", None) == "section":
        section_scope = (
            scope
            if isinstance(scope, CompositionAnalysisScopeSection)
            else CompositionAnalysisScopeSection.model_validate(scope)
        )
        if section_scope.section_index >= len(composition.sections):
            raise CompositionAnalysisError(
                "analysis_invalid_scope",
                "section_index is out of range",
                details={
                    "section_index": section_scope.section_index,
                    "section_count": len(composition.sections),
                },
            )
        section = composition.sections[section_scope.section_index]
        mismatches = _section_matches_expectations(section, section_scope)
        if mismatches:
            logger.warning(
                "Analysis section scope verification failed",
                extra={
                    "rejection_code": "analysis_invalid_scope",
                    "mismatch_fields": mismatches,
                    "section_index": section_scope.section_index,
                },
            )
            raise CompositionAnalysisError(
                "analysis_invalid_scope",
                "section selectors do not match the canonical section at section_index",
                details={
                    "section_index": section_scope.section_index,
                    "mismatch_fields": mismatches,
                },
            )
        resolved = ResolvedAnalysisScope(
            kind="section",
            start_tick=section.start_tick,
            end_tick=section.start_tick + section.duration_ticks,
            start_bar=section.start_bar,
            end_bar_exclusive=section.start_bar + section.bar_count,
            section_id=section.id,
            section_index=section_scope.section_index,
        )
        logger.debug(
            "Resolved section analysis scope",
            extra={
                "scope_kind": resolved.kind,
                "section_index": resolved.section_index,
                "start_tick": resolved.start_tick,
                "end_tick": resolved.end_tick,
                "has_section_id": section.id is not None,
            },
        )
        return resolved

    if isinstance(scope, CompositionAnalysisScopeTrack) or getattr(scope, "kind", None) == "track":
        track_scope = (
            scope
            if isinstance(scope, CompositionAnalysisScopeTrack)
            else CompositionAnalysisScopeTrack.model_validate(scope)
        )
        track: CompositionV2Track | None = None
        track_index: int | None = None
        for index, candidate in enumerate(composition.tracks):
            if candidate.id == track_scope.track_id:
                track = candidate
                track_index = index
                break
        if track is None or track_index is None:
            raise CompositionAnalysisError(
                "analysis_invalid_scope",
                "track_id was not found in the composition",
                details={"track_id_present": True},
            )
        resolved = ResolvedAnalysisScope(
            kind="track",
            start_tick=0,
            end_tick=composition.duration_ticks,
            start_bar=1,
            end_bar_exclusive=composition.bar_count + 1,
            track_id=track.id,
            track_index=track_index,
        )
        logger.debug(
            "Resolved track analysis scope",
            extra={
                "scope_kind": resolved.kind,
                "track_index": resolved.track_index,
                "start_tick": resolved.start_tick,
                "end_tick": resolved.end_tick,
            },
        )
        return resolved

    raise CompositionAnalysisError(
        "analysis_invalid_scope",
        "Unknown analysis scope kind",
        details={"reason": "unknown_kind"},
    )


def prepare_analysis_request(
    composition: CompositionV2 | dict[str, Any] | Any,
    scope: CompositionAnalysisScope | dict[str, Any] | None = None,
) -> tuple[CompositionV2, CompositionAnalysisScope, ResolvedAnalysisScope]:
    """Validate V2 input and resolve a unique scope selector."""
    validated = revalidate_composition_v2(composition)
    if scope is None:
        parsed_scope: CompositionAnalysisScope = CompositionAnalysisScopeComposition()
    elif isinstance(
        scope,
        (
            CompositionAnalysisScopeComposition,
            CompositionAnalysisScopeSection,
            CompositionAnalysisScopeTrack,
        ),
    ):
        parsed_scope = scope
    else:
        try:
            # Discriminated union via request model field validation.
            parsed_scope = CompositionAnalysisRequest.model_validate(
                {"composition": validated, "scope": scope}
            ).scope
        except Exception as exc:
            logger.warning(
                "Analysis scope parse failed",
                extra={
                    "rejection_code": "analysis_invalid_scope",
                    "error_type": type(exc).__name__,
                },
            )
            raise CompositionAnalysisError(
                "analysis_invalid_scope",
                "Analysis scope is invalid",
                details={"reason": "scope_validation_error", "error_type": type(exc).__name__},
            ) from exc

    resolved = resolve_analysis_scope(validated, parsed_scope)
    note_count = sum(len(track.events) for track in validated.tracks)
    logger.info(
        "Analysis request prepared",
        extra={
            "algorithm_version": ANALYSIS_ALGORITHM_VERSION,
            "scope_kind": resolved.kind,
            "track_count": len(validated.tracks),
            "section_count": len(validated.sections),
            "note_count": note_count,
            "bar_count": validated.bar_count,
            "section_index": resolved.section_index,
            "track_index": resolved.track_index,
        },
    )
    return validated, parsed_scope, resolved


def default_warning(code: AnalysisWarningCode, **kwargs: Any) -> AnalysisWarning:
    return AnalysisWarning(
        code=code,
        message=ANALYSIS_WARNING_CODES[code],
        **kwargs,
    )


def fingerprint_log_prefix(fingerprint: str) -> str:
    return fingerprint[:ANALYSIS_FINGERPRINT_LOG_PREFIX_LEN]
