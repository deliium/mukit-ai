"""Strict ``reference.features.v1`` contracts for selective reference conditioning.

Reference feature reports are derived sidecars — never playable scores, never
Composer Profiles, never embedding vector dumps, never dataset rows.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.embeddings.schemas import CompositionEmbedScope, EmbedScopeComposition

logger = logging.getLogger(__name__)

REFERENCE_FEATURES_SCHEMA: Literal["reference.features.v1"] = "reference.features.v1"
REFERENCE_FEATURES_ALGORITHM_VERSION: Literal["reference.features.v1.0"] = (
    "reference.features.v1.0"
)

# Closed dimension registry (UI label → id).
DimensionId = Literal[
    "harmony",
    "harmonic_rhythm",
    "rhythm",
    "melodic_contour",
    "texture",
    "instrumentation",
    "density",
    "dynamics",
    "form",
    "tension_curve",
    "motif_characteristics",
    "performance_characteristics",
]

DIMENSION_IDS: tuple[DimensionId, ...] = (
    "harmony",
    "harmonic_rhythm",
    "rhythm",
    "melodic_contour",
    "texture",
    "instrumentation",
    "density",
    "dynamics",
    "form",
    "tension_curve",
    "motif_characteristics",
    "performance_characteristics",
)

DIMENSION_ID_SET: frozenset[str] = frozenset(DIMENSION_IDS)

# AC UI labels (human-facing).
DIMENSION_UI_LABELS: dict[DimensionId, str] = {
    "harmony": "Harmony",
    "harmonic_rhythm": "Harmonic rhythm",
    "rhythm": "Rhythm",
    "melodic_contour": "Melodic contour",
    "texture": "Orchestration texture",
    "instrumentation": "Instrumentation",
    "density": "Rhythmic density",
    "dynamics": "Dynamics",
    "form": "Form",
    "tension_curve": "Tension curve",
    "motif_characteristics": "Motif characteristics",
    "performance_characteristics": "Performance",
}

DimensionStatus = Literal["ok", "degraded", "unavailable"]
ReferenceSourceKind = Literal["project", "revision", "inline"]
ImportOrigin = Literal["midi", "musicxml", "transcription", "generated", "unknown"]
FeatureGroupId = Literal["rhythm", "contour", "texture", "form"]

PreferenceBand = Literal["very_low", "low", "moderate", "high", "very_high"]

# Reject playable / private / identity payloads at any nest level.
FORBIDDEN_REFERENCE_FEATURE_KEYS: frozenset[str] = frozenset(
    {
        "tracks",
        "musicxml",
        "midi",
        "wav",
        "analysis",
        "analysis_report",
        "composition",
        "music",
        "events",
        "note_events",
        "embedding",
        "vector",
        "artist",
        "artist_id",
        "composer",
        "composer_id",
        "artist_name",
        "composer_name",
        "motif_notes",
        "pitch_sequence",
        "note_list",
    }
)

REFERENCE_FEATURE_ERROR_CODES: dict[str, str] = {
    "reference_feature_invalid": "Reference feature request or report failed validation.",
    "reference_feature_unknown_dimension": "One or more requested dimensions are not in the closed registry.",
    "reference_feature_mask_empty": "Dimension mask is empty or all selected dimensions are unavailable.",
    "reference_feature_scope_empty": "The selected embed scope contains no usable note material.",
    "reference_feature_not_found": "Reference project, revision, or inline composition could not be resolved.",
    "reference_feature_fingerprint_mismatch": "Reference fingerprint no longer matches the requested source.",
    "reference_feature_cap_exceeded": "A configured reference-features cap was exceeded.",
    "reference_feature_forbidden_payload": "Payload contains forbidden playable, vector, or identity fields.",
    "reference_feature_dataset_forbidden": "Reference feature analysis must not write to DATASET_ROOT.",
    "rights_reference_refused": "This source is not eligible for reference analysis.",
    # reference.conditioning.policy.v1 (shared mapper)
    "reference_conditioning_partition_overlap": (
        "A dimension appears in more than one of preserve, borrow, or regenerate."
    ),
    "reference_conditioning_borrow_dimension_conflict": (
        "The same dimension is borrowed from more than one reference binding."
    ),
    "reference_conditioning_preserve_without_source": (
        "Preserve dimensions require a current composition scope (not available on generate)."
    ),
    "reference_conditioning_motif_reuse_forbidden": (
        "allow_motif_reuse requires active_project_id matching every borrow binding project_id."
    ),
    "reference_conditioning_unknown_strength": (
        "dimension_strengths keys must be a subset of the borrow dimension union."
    ),
}

ReferenceFeatureErrorCode = Literal[
    "reference_feature_invalid",
    "reference_feature_unknown_dimension",
    "reference_feature_mask_empty",
    "reference_feature_scope_empty",
    "reference_feature_not_found",
    "reference_feature_fingerprint_mismatch",
    "reference_feature_cap_exceeded",
    "reference_feature_forbidden_payload",
    "reference_feature_dataset_forbidden",
    "rights_reference_refused",
    "reference_conditioning_partition_overlap",
    "reference_conditioning_borrow_dimension_conflict",
    "reference_conditioning_preserve_without_source",
    "reference_conditioning_motif_reuse_forbidden",
    "reference_conditioning_unknown_strength",
]

REFERENCE_FEATURE_WARNING_CODES: dict[str, str] = {
    "reference_feature_singular_ignored": "style_references[] is authoritative; singular style_reference was ignored.",
    "reference_feature_affinity_unsupported": "Affinity is unavailable for one or more selected dimensions.",
    "reference_feature_degraded": "One or more dimensions were extracted with thin evidence.",
    "reference_feature_insufficient_harmony": "Harmony-related dimensions lack chord material.",
    "reference_feature_insufficient_dynamics": "Dynamics/expression fields are missing or sparse.",
    "reference_feature_insufficient_motifs": "Motif metadata is absent; motif characteristics unavailable.",
    "reference_feature_monophonic_texture": "Texture evidence is monophonic-only; orchestration summary degraded.",
    "reference_feature_compare_omitted": "Embedding affinity omitted because no compare target was provided.",
    "reference_conditioning_strength_off_dropped": (
        "Borrow dimension dropped because strength=off (unspecified; not forced to regenerate)."
    ),
    "reference_conditioning_preserve_degraded": (
        "Preserve summary for one or more dimensions used thin evidence."
    ),
}

ReferenceFeatureWarningCode = Literal[
    "reference_feature_singular_ignored",
    "reference_feature_affinity_unsupported",
    "reference_feature_degraded",
    "reference_feature_insufficient_harmony",
    "reference_feature_insufficient_dynamics",
    "reference_feature_insufficient_motifs",
    "reference_feature_monophonic_texture",
    "reference_feature_compare_omitted",
    "reference_conditioning_strength_off_dropped",
    "reference_conditioning_preserve_degraded",
]


class ReferenceFeatureError(Exception):
    """Domain error for reference feature operations mapped to structured HTTP."""

    def __init__(
        self,
        code: ReferenceFeatureErrorCode,
        message: str | None = None,
        *,
        http_status: int = 422,
        details: dict[str, Any] | None = None,
    ) -> None:
        resolved = message or REFERENCE_FEATURE_ERROR_CODES.get(code, code)
        super().__init__(resolved)
        self.code = code
        self.message = resolved
        self.http_status = http_status
        self.details = details or {}
        logger.error(
            "Reference feature domain error",
            extra={
                "error_code": code,
                "http_status": http_status,
                "detail_keys": sorted(self.details.keys()),
            },
        )


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _reject_forbidden_keys(payload: Any, *, path: str = "root") -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            lowered = str(key).strip().lower()
            if lowered in FORBIDDEN_REFERENCE_FEATURE_KEYS:
                logger.debug(
                    "Reference feature validation rejected forbidden key",
                    extra={"field_names": [f"{path}.{key}"], "forbidden_key": lowered},
                )
                raise ValueError(
                    f"forbidden field {key!r} at {path} "
                    "(reference features must not carry playable, vector, or identity data)"
                )
            _reject_forbidden_keys(value, path=f"{path}.{key}")
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            _reject_forbidden_keys(item, path=f"{path}[{index}]")


def parse_dimension_id(raw: str) -> DimensionId:
    """Parse a dimension id or raise ``reference_feature_unknown_dimension``."""
    value = str(raw).strip()
    if value not in DIMENSION_ID_SET:
        raise ReferenceFeatureError(
            "reference_feature_unknown_dimension",
            details={"dimension": value},
        )
    return value  # type: ignore[return-value]


def normalize_requested_dimensions(
    requested: list[DimensionId] | list[str] | None,
    *,
    default_all: bool,
) -> list[DimensionId]:
    """Normalize analyze / mask dimension lists.

    Analyze: omitted or empty → all registry dims when ``default_all``.
    Conditioning masks: empty list must raise ``reference_feature_mask_empty``.
    """
    if requested is None or (isinstance(requested, list) and len(requested) == 0):
        if default_all:
            return list(DIMENSION_IDS)
        raise ReferenceFeatureError("reference_feature_mask_empty")
    seen: set[str] = set()
    out: list[DimensionId] = []
    for item in requested:
        dim = parse_dimension_id(str(item))
        if dim in seen:
            continue
        seen.add(dim)
        out.append(dim)
    if not out:
        raise ReferenceFeatureError("reference_feature_mask_empty")
    return out


# --- Report payload -----------------------------------------------------------


class ReferenceFeatureEvidence(StrictModel):
    note_count: int = Field(default=0, ge=0)
    track_count: int | None = Field(default=None, ge=0)
    section_count: int | None = Field(default=None, ge=0)
    chord_span_count: int | None = Field(default=None, ge=0)
    motif_count: int | None = Field(default=None, ge=0)
    has_velocity: bool | None = None
    has_expression: bool | None = None


class ReferenceFeatureBands(StrictModel):
    """Bounded abstract bands / histograms — never note events."""

    density_band: PreferenceBand | None = None
    syncopation_band: PreferenceBand | None = None
    range_semitones_band: PreferenceBand | None = None
    harmonic_complexity_band: PreferenceBand | None = None
    chord_change_rate_band: PreferenceBand | None = None
    arrangement_density_band: PreferenceBand | None = None
    repetition_band: PreferenceBand | None = None
    tension_shape: str | None = Field(default=None, max_length=40)
    top_instruments: list[str] = Field(default_factory=list, max_length=16)
    top_section_types: list[str] = Field(default_factory=list, max_length=16)
    top_chord_labels: list[str] = Field(default_factory=list, max_length=16)
    interval_histogram_bins: list[float] = Field(default_factory=list, max_length=25)
    onset_histogram_bins: list[float] = Field(default_factory=list, max_length=16)

    @field_validator("top_instruments", "top_section_types", "top_chord_labels", mode="before")
    @classmethod
    def _cap_labels(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("label lists must be arrays")
        out: list[str] = []
        seen: set[str] = set()
        for item in value:
            label = str(item).strip()[:80]
            if not label:
                continue
            key = label.casefold()
            if key in seen:
                continue
            seen.add(key)
            out.append(label)
            if len(out) >= 16:
                break
        return out


class ReferenceFeatureDimensionPayload(StrictModel):
    status: DimensionStatus
    summary: str = Field(..., min_length=1, max_length=2_000)
    soft_fragment: str = Field(..., min_length=0, max_length=2_000)
    bands: ReferenceFeatureBands | None = None
    evidence: ReferenceFeatureEvidence = Field(default_factory=ReferenceFeatureEvidence)
    warning_codes: list[ReferenceFeatureWarningCode] = Field(
        default_factory=list, max_length=16
    )

    @model_validator(mode="before")
    @classmethod
    def _forbid_nested(cls, data: Any) -> Any:
        _reject_forbidden_keys(data, path="dimension")
        return data


class ReferenceFeatureUnavailableItem(StrictModel):
    dimension: DimensionId
    code: str = Field(..., min_length=1, max_length=80)
    message: str = Field(..., min_length=1, max_length=400)


class ReferenceFeatureSource(StrictModel):
    kind: ReferenceSourceKind
    project_id: str | None = Field(default=None, min_length=1, max_length=80)
    revision_id: str | None = Field(default=None, min_length=1, max_length=80)
    source_fingerprint: str = Field(..., min_length=16, max_length=128)
    import_origin: ImportOrigin | None = None

    @model_validator(mode="after")
    def _align_kind(self) -> ReferenceFeatureSource:
        if self.kind == "project" and not self.project_id:
            raise ValueError("project source requires project_id")
        if self.kind == "revision" and (not self.project_id or not self.revision_id):
            raise ValueError("revision source requires project_id and revision_id")
        if self.kind == "inline" and self.project_id is not None:
            # Inline may optionally carry project_id for provenance; allow either.
            pass
        return self


class ReferenceFeatureAffinity(StrictModel):
    """Group-slice affinity vs compare target — never a musical quality claim."""

    model_id: str = Field(..., min_length=1, max_length=160)
    profile_id: str = Field(..., min_length=1, max_length=80)
    per_group: dict[FeatureGroupId, float] = Field(default_factory=dict, max_length=8)
    per_dimension: dict[DimensionId, float] = Field(default_factory=dict, max_length=16)
    musical_quality_claim: Literal[False] = False

    @field_validator("per_group", "per_dimension")
    @classmethod
    def _finite_scores(cls, value: dict[str, float]) -> dict[str, float]:
        out: dict[str, float] = {}
        for key, score in value.items():
            if not isinstance(score, (int, float)):
                raise ValueError("affinity scores must be numeric")
            if score != score or score in (float("inf"), float("-inf")):
                raise ValueError("affinity scores must be finite")
            out[key] = float(score)  # type: ignore[index]
        return out  # type: ignore[return-value]


class ReferenceFeaturesV1(StrictModel):
    """Derived reference feature report (session/request only)."""

    schema_version: Literal["reference.features.v1"] = REFERENCE_FEATURES_SCHEMA
    algorithm_version: Literal["reference.features.v1.0"] = (
        REFERENCE_FEATURES_ALGORITHM_VERSION
    )
    source: ReferenceFeatureSource
    scope: CompositionEmbedScope
    scope_digest: str = Field(..., min_length=8, max_length=64)
    requested_dimensions: list[DimensionId] = Field(default_factory=list, max_length=32)
    dimensions: dict[DimensionId, ReferenceFeatureDimensionPayload] = Field(
        default_factory=dict, max_length=32
    )
    unavailable: list[ReferenceFeatureUnavailableItem] = Field(
        default_factory=list, max_length=32
    )
    embedding_affinity: ReferenceFeatureAffinity | None = None
    warning_codes: list[ReferenceFeatureWarningCode] = Field(
        default_factory=list, max_length=32
    )

    @model_validator(mode="before")
    @classmethod
    def _forbid_root(cls, data: Any) -> Any:
        _reject_forbidden_keys(data, path="root")
        return data

    @model_validator(mode="after")
    def _align_requested(self) -> ReferenceFeaturesV1:
        for dim in self.requested_dimensions:
            if dim not in DIMENSION_ID_SET:
                raise ValueError(f"unknown dimension in requested_dimensions: {dim}")
        for dim in self.dimensions:
            if dim not in DIMENSION_ID_SET:
                raise ValueError(f"unknown dimension key: {dim}")
        return self


# --- Analyze / compare request envelopes --------------------------------------


class ReferenceFeatureCompareTarget(StrictModel):
    """Optional compare target for affinity (project / revision / inline V2)."""

    project_id: str | None = Field(default=None, min_length=1, max_length=80)
    revision_id: str | None = Field(default=None, min_length=1, max_length=80)
    composition: dict[str, Any] | None = None
    scope: CompositionEmbedScope = Field(default_factory=EmbedScopeComposition)

    @model_validator(mode="before")
    @classmethod
    def _forbid_compare_payload(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # composition is allowed as V2 body but nested forbid still applies to extras
            for key in data:
                lowered = str(key).strip().lower()
                if lowered in FORBIDDEN_REFERENCE_FEATURE_KEYS and lowered != "composition":
                    raise ValueError(f"forbidden field {key!r} on compare_to")
        return data

    @model_validator(mode="after")
    def _require_source(self) -> ReferenceFeatureCompareTarget:
        if self.project_id is None and self.composition is None:
            raise ValueError("compare_to requires project_id or composition")
        return self


class ReferenceFeatureAnalyzeRequest(StrictModel):
    """``POST /reference-features/analyze`` body."""

    project_id: str | None = Field(default=None, min_length=1, max_length=80)
    revision_id: str | None = Field(default=None, min_length=1, max_length=80)
    composition: dict[str, Any] | None = None
    scope: CompositionEmbedScope = Field(default_factory=EmbedScopeComposition)
    expected_fingerprint: str | None = Field(default=None, min_length=16, max_length=128)
    requested_dimensions: list[DimensionId] | None = Field(default=None, max_length=32)
    compare_to: ReferenceFeatureCompareTarget | None = None
    import_origin: ImportOrigin | None = None
    # Part K attestation when no studio registry row exists (legacy DatasetProvenance).
    rights: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def _forbid_analyze_extras(cls, data: Any) -> Any:
        if isinstance(data, dict):
            for key in data:
                lowered = str(key).strip().lower()
                if lowered in FORBIDDEN_REFERENCE_FEATURE_KEYS and lowered not in {
                    "composition",
                }:
                    logger.debug(
                        "Analyze request validation rejected forbidden key",
                        extra={"field_names": [key]},
                    )
                    raise ValueError(f"forbidden field {key!r}")
        return data

    @model_validator(mode="after")
    def _require_source(self) -> ReferenceFeatureAnalyzeRequest:
        if self.project_id is None and self.composition is None:
            raise ValueError("analyze requires project_id or composition")
        if self.requested_dimensions is not None:
            # Validate known ids early; empty list means "all" for analyze.
            for dim in self.requested_dimensions:
                if dim not in DIMENSION_ID_SET:
                    raise ValueError(f"unknown dimension: {dim}")
        return self


class ReferenceFeatureAnalyzeResponse(StrictModel):
    report: ReferenceFeaturesV1
    warning_codes: list[ReferenceFeatureWarningCode] = Field(
        default_factory=list, max_length=32
    )


# --- Conditioning provenance (generation_parameters nest) ----------------------


class ReferenceFeatureProvenanceEntry(StrictModel):
    """Bounded provenance for generate/develop — never summaries or vectors."""

    project_id: str | None = Field(default=None, max_length=80)
    revision_id: str | None = Field(default=None, max_length=80)
    scope_kind: str = Field(..., min_length=1, max_length=40)
    fingerprint_prefix: str = Field(..., min_length=4, max_length=32)
    dimensions: list[DimensionId] = Field(default_factory=list, max_length=32)
    unavailable_codes: list[str] = Field(default_factory=list, max_length=32)


def dimension_ui_label(dimension_id: DimensionId | str) -> str:
    """Return the locked AC UI label for a dimension id."""
    return DIMENSION_UI_LABELS.get(dimension_id, str(dimension_id))  # type: ignore[arg-type]


def map_reference_feature_error_to_http(
    exc: ReferenceFeatureError,
) -> tuple[int, dict[str, Any]]:
    """Map domain errors to ``(status, detail)`` for HTTPException."""
    status = int(exc.http_status)
    # Locked status overrides by code when caller did not set a specific status.
    if exc.code == "reference_feature_not_found":
        status = 404
    elif exc.code == "reference_feature_dataset_forbidden":
        status = 403
    elif str(exc.code).startswith("reference_conditioning_"):
        status = 422
    elif status < 400:
        status = 422
    detail: dict[str, Any] = {
        "code": exc.code,
        "message": exc.message,
    }
    if exc.details:
        detail["details"] = exc.details
    logger.warning(
        "Mapped reference feature / conditioning error to HTTP",
        extra={"error_code": exc.code, "http_status": status},
    )
    return status, detail
