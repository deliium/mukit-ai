"""Strict Pydantic contracts for symbolic composition embeddings.

Schema layering
---------------
* ``composition.embedding.v1`` — vector card + profile / model / scope /
  fingerprint identity.
* ``composition.embed_scope.v1`` — discriminated composition | section |
  motif | bar_range.
* ``composition.similarity_query.v1`` / ``similarity_hit.v1`` — nearest-
  neighbor query + ranked hits.
* ``composition.reference_provenance.v1`` — bounded reference ids (never
  full compositions; never artist/composer embedding ids).
* ``composition.style_conditioning.v1`` — how a reference embedding is
  applied (prompt features / tokenizer labels / vector hint).

Artist / composer names are **not** embedding dimensions. Freeform user
prompts may still mention artists; system conditioning uses reference
*material* embeddings only.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.embeddings.errors import EMBEDDING_ERROR_CODES, EmbeddingErrorCode, EmbeddingWarningCode
from app.embeddings.settings import (
    EMBEDDING_ALGORITHM_VERSION,
    EMBEDDING_DEFAULT_DISTANCE_METRIC,
    EMBEDDING_DEFAULT_MODEL_ID,
    EMBEDDING_FINGERPRINT_LOG_PREFIX_LEN,
    EMBEDDING_PROFILE_ID,
    EMBEDDING_PROJECTION_NONE,
    EMBEDDING_REFERENCE_PROVENANCE_SCHEMA,
    EMBEDDING_SCHEMA_VERSION,
    EMBEDDING_SCOPE_SCHEMA_VERSION,
    EMBEDDING_SIMILARITY_HIT_SCHEMA,
    EMBEDDING_SIMILARITY_QUERY_SCHEMA,
    EMBEDDING_STYLE_CONDITIONING_SCHEMA,
)


logger = logging.getLogger(__name__)

EmbedScopeKind = Literal["composition", "section", "motif", "bar_range"]
EmbeddingDistanceMetric = Literal["cosine", "euclidean"]
EmbeddingProjectionId = Literal["none", "offline_linear.v1"]
SimilarityCorpusKind = Literal["projects", "in_request", "dataset"]
StyleConditioningMode = Literal["prompt_features", "tokenizer_labels", "vector_hint"]

# Forbidden field names that must never appear on embedding / provenance DTOs.
_FORBIDDEN_ARTIST_FIELDS = frozenset(
    {
        "artist_id",
        "artist_name",
        "composer_id",
        "composer_name",
        "artist",
        "composer",
    }
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- Embed scopes (composition.embed_scope.v1) ---------------------------------


class EmbedScopeComposition(StrictModel):
    kind: Literal["composition"] = "composition"


class EmbedScopeSection(StrictModel):
    """Select a section by canonical array index; optional id/bounds verify identity."""

    kind: Literal["section"] = "section"
    section_index: int = Field(..., ge=0)
    section_id: str | None = Field(default=None, min_length=1, max_length=120)
    expected_start_bar: int | None = Field(default=None, ge=1)
    expected_bar_count: int | None = Field(default=None, ge=1)


class EmbedScopeMotif(StrictModel):
    """Embed a motif definition or a specific occurrence."""

    kind: Literal["motif"] = "motif"
    motif_id: str = Field(..., min_length=1, max_length=120)
    occurrence_id: str | None = Field(default=None, min_length=1, max_length=120)


class EmbedScopeBarRange(StrictModel):
    """Inclusive start_bar .. end_bar over all pitched tracks (or optional track)."""

    kind: Literal["bar_range"] = "bar_range"
    start_bar: int = Field(..., ge=1)
    end_bar: int = Field(..., ge=1)
    track_id: str | None = Field(default=None, min_length=1, max_length=80)

    @model_validator(mode="after")
    def _validate_bar_order(self) -> EmbedScopeBarRange:
        if self.end_bar < self.start_bar:
            logger.debug(
                "Embed scope validation failed",
                extra={"field_names": ["start_bar", "end_bar"], "scope_kind": "bar_range"},
            )
            raise ValueError("end_bar must be >= start_bar")
        return self


CompositionEmbedScope = Annotated[
    EmbedScopeComposition | EmbedScopeSection | EmbedScopeMotif | EmbedScopeBarRange,
    Field(discriminator="kind"),
]


def embed_scope_digest(scope: CompositionEmbedScope | dict[str, Any]) -> str:
    """Stable short digest of a scope for cache keys (no composition bytes)."""
    if hasattr(scope, "model_dump"):
        payload = scope.model_dump(mode="json")  # type: ignore[union-attr]
    else:
        payload = dict(scope)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
    logger.debug(
        "Computed embed scope digest",
        extra={
            "scope_kind": payload.get("kind"),
            "scope_digest_prefix": digest[:EMBEDDING_FINGERPRINT_LOG_PREFIX_LEN],
        },
    )
    return digest


# --- Embedding card (composition.embedding.v1) ---------------------------------


class CompositionEmbeddingV1(StrictModel):
    """Versioned symbolic embedding card.

    ``vector`` is L2-normalized for cosine. Artist/composer names are not
    dimensions and must not appear in this payload.
    """

    schema_version: Literal["composition.embedding.v1"] = EMBEDDING_SCHEMA_VERSION
    profile_id: str = Field(default=EMBEDDING_PROFILE_ID, min_length=1, max_length=80)
    algorithm_version: str = Field(
        default=EMBEDDING_ALGORITHM_VERSION, min_length=1, max_length=80
    )
    model_id: str = Field(default=EMBEDDING_DEFAULT_MODEL_ID, min_length=1, max_length=160)
    dims: int = Field(..., ge=1, le=4096)
    distance_metric: EmbeddingDistanceMetric = EMBEDDING_DEFAULT_DISTANCE_METRIC
    projection_id: EmbeddingProjectionId = EMBEDDING_PROJECTION_NONE
    projection_digest: str | None = Field(default=None, min_length=8, max_length=128)
    source_fingerprint: str = Field(..., min_length=16, max_length=128)
    scope: CompositionEmbedScope
    scope_digest: str = Field(..., min_length=8, max_length=64)
    note_count: int = Field(..., ge=0)
    vector: list[float] = Field(..., min_length=1, max_length=4096)
    # Explicit policy: embeddings never encode artist identity.
    artist_label_used: Literal[False] = False

    @field_validator("vector")
    @classmethod
    def _validate_finite_vector(cls, value: list[float]) -> list[float]:
        for i, component in enumerate(value):
            if not isinstance(component, (int, float)):
                raise ValueError(f"vector[{i}] must be numeric")
            if component != component or component in (float("inf"), float("-inf")):
                raise ValueError(f"vector[{i}] must be finite")
        return [float(v) for v in value]

    @model_validator(mode="after")
    def _align_dims(self) -> CompositionEmbeddingV1:
        if self.dims != len(self.vector):
            logger.debug(
                "Embedding card validation failed",
                extra={"field_names": ["dims", "vector"], "dims": self.dims, "len": len(self.vector)},
            )
            raise ValueError("dims must equal len(vector)")
        if self.projection_id == "none" and self.projection_digest is not None:
            raise ValueError("projection_digest must be omitted when projection_id is none")
        if self.projection_id == "offline_linear.v1" and not self.projection_digest:
            raise ValueError("projection_digest required for offline_linear.v1")
        return self

    def fingerprint_prefix(self, length: int = EMBEDDING_FINGERPRINT_LOG_PREFIX_LEN) -> str:
        return self.source_fingerprint[:length]

    def vector_debug_prefix(self, dims: int = 8) -> list[float]:
        """First N dims for DEBUG logs only — never log full vectors at INFO."""
        return [round(v, 6) for v in self.vector[: max(0, dims)]]

    def public_summary(self) -> dict[str, Any]:
        """Safe metadata without the vector (for logs / provenance nesting)."""
        return {
            "schema_version": self.schema_version,
            "profile_id": self.profile_id,
            "algorithm_version": self.algorithm_version,
            "model_id": self.model_id,
            "dims": self.dims,
            "distance_metric": self.distance_metric,
            "projection_id": self.projection_id,
            "source_fingerprint_prefix": self.fingerprint_prefix(),
            "scope_kind": self.scope.kind if hasattr(self.scope, "kind") else None,
            "scope_digest_prefix": self.scope_digest[:EMBEDDING_FINGERPRINT_LOG_PREFIX_LEN],
            "note_count": self.note_count,
            "artist_label_used": False,
        }


# --- Similarity query / hit ---------------------------------------------------


class SimilarityCorpusSelector(StrictModel):
    """Where to search. ``dataset`` requires EMBEDDING_ALLOW_DATASET_CORPUS."""

    kind: SimilarityCorpusKind = "projects"
    # Optional project allow-list (empty = all accessible projects, capped by settings).
    project_ids: list[str] = Field(default_factory=list, max_length=500)
    # When kind=in_request: embed these compositions' sections without project store.
    include_composition_sections: bool = True
    include_motifs: bool = False

    @field_validator("project_ids")
    @classmethod
    def _clean_project_ids(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for pid in value:
            normalized = pid.strip()
            if not normalized or len(normalized) > 80:
                raise ValueError("project_ids entries must be 1..80 characters")
            cleaned.append(normalized)
        return cleaned


class CompositionSimilarityQueryV1(StrictModel):
    schema_version: Literal["composition.similarity_query.v1"] = EMBEDDING_SIMILARITY_QUERY_SCHEMA
    # Query may supply a precomputed embedding *or* composition+scope (service embeds).
    embedding: CompositionEmbeddingV1 | None = None
    composition: dict[str, Any] | None = None  # validated as CompositionV2 by service
    scope: CompositionEmbedScope | None = None
    corpus: SimilarityCorpusSelector = Field(default_factory=SimilarityCorpusSelector)
    top_k: int = Field(default=10, ge=1, le=200)
    # Exclude the query project's own fingerprint prefix hits when set.
    exclude_project_id: str | None = Field(default=None, min_length=1, max_length=80)
    exclude_source_fingerprint: str | None = Field(default=None, min_length=16, max_length=128)
    distance_metric: EmbeddingDistanceMetric = EMBEDDING_DEFAULT_DISTANCE_METRIC

    @model_validator(mode="after")
    def _require_query_source(self) -> CompositionSimilarityQueryV1:
        has_embedding = self.embedding is not None
        has_comp_scope = self.composition is not None and self.scope is not None
        if not has_embedding and not has_comp_scope:
            logger.debug(
                "Similarity query validation failed",
                extra={"field_names": ["embedding", "composition", "scope"]},
            )
            raise ValueError("Provide embedding or composition+scope")
        return self


class SimilarityTargetRef(StrictModel):
    """Bounded pointer to a similar scope (never embeds full composition)."""

    project_id: str | None = Field(default=None, min_length=1, max_length=80)
    revision_id: str | None = Field(default=None, min_length=1, max_length=80)
    scope: CompositionEmbedScope
    source_fingerprint: str = Field(..., min_length=16, max_length=128)
    corpus: SimilarityCorpusKind = "projects"


class CompositionSimilarityHitV1(StrictModel):
    schema_version: Literal["composition.similarity_hit.v1"] = EMBEDDING_SIMILARITY_HIT_SCHEMA
    rank: int = Field(..., ge=1)
    score: float = Field(..., ge=-1.0, le=1.0)  # cosine similarity in [-1, 1]
    distance: float = Field(..., ge=0.0)
    distance_metric: EmbeddingDistanceMetric = EMBEDDING_DEFAULT_DISTANCE_METRIC
    target: SimilarityTargetRef
    model_id: str = Field(..., min_length=1, max_length=160)
    profile_id: str = Field(default=EMBEDDING_PROFILE_ID, min_length=1, max_length=80)
    # Explicit: score is affinity, not aesthetic quality.
    musical_quality_claim: Literal[False] = False


# --- Reference provenance + style conditioning --------------------------------


class CompositionReferenceProvenanceV1(StrictModel):
    """Bounded provenance for a musical reference used in conditioning.

    Never stores the full reference composition, event arrays, or artist ids
    as embedding identity. ``artist_label_used`` is always false for system
    conditioning paths.
    """

    schema_version: Literal["composition.reference_provenance.v1"] = (
        EMBEDDING_REFERENCE_PROVENANCE_SCHEMA
    )
    project_id: str | None = Field(default=None, min_length=1, max_length=80)
    revision_id: str | None = Field(default=None, min_length=1, max_length=80)
    scope: CompositionEmbedScope
    source_fingerprint: str = Field(..., min_length=16, max_length=128)
    embedding_model_id: str = Field(..., min_length=1, max_length=160)
    profile_id: str = Field(default=EMBEDDING_PROFILE_ID, min_length=1, max_length=80)
    algorithm_version: str = Field(
        default=EMBEDDING_ALGORITHM_VERSION, min_length=1, max_length=80
    )
    scope_digest: str = Field(..., min_length=8, max_length=64)
    artist_label_used: Literal[False] = False

    def fingerprint_prefix(self, length: int = EMBEDDING_FINGERPRINT_LOG_PREFIX_LEN) -> str:
        return self.source_fingerprint[:length]

    def to_generation_parameters_fragment(self) -> dict[str, Any]:
        """Bounded dict suitable for AiProvenance.generation_parameters nesting."""
        return {
            "reference_provenance": {
                "schema_version": self.schema_version,
                "project_id": self.project_id,
                "revision_id": self.revision_id,
                "scope_kind": self.scope.kind,
                "scope_digest_prefix": self.scope_digest[:EMBEDDING_FINGERPRINT_LOG_PREFIX_LEN],
                "source_fingerprint_prefix": self.fingerprint_prefix(),
                "embedding_model_id": self.embedding_model_id,
                "profile_id": self.profile_id,
                "algorithm_version": self.algorithm_version,
                "artist_label_used": False,
            }
        }


class StyleReferenceRequest(StrictModel):
    """Request block: resolve a musical reference then embed for conditioning."""

    project_id: str | None = Field(default=None, min_length=1, max_length=80)
    revision_id: str | None = Field(default=None, min_length=1, max_length=80)
    # Inline composition when reference is the current workspace (no project id).
    composition: dict[str, Any] | None = None
    scope: CompositionEmbedScope
    # Optional expected fingerprint for CAS-style mismatch detection.
    expected_fingerprint: str | None = Field(default=None, min_length=16, max_length=128)
    mode: StyleConditioningMode = "prompt_features"
    # Optional dimension mask (reference.features.v1). None = legacy whole summary;
    # empty list is rejected at conditioning time as reference_feature_mask_empty.
    dimensions: list[str] | None = Field(default=None, max_length=32)

    @field_validator("dimensions")
    @classmethod
    def _validate_dimension_ids(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        # Lazy import avoids cycles with reference_feature_schemas.
        from app.reference_feature_schemas import DIMENSION_ID_SET

        cleaned: list[str] = []
        seen: set[str] = set()
        for item in value:
            dim = str(item).strip()
            if dim not in DIMENSION_ID_SET:
                logger.debug(
                    "Style reference dimension validation failed",
                    extra={"field_names": ["dimensions"], "dimension": dim},
                )
                raise ValueError(f"unknown reference feature dimension: {dim}")
            if dim in seen:
                continue
            seen.add(dim)
            cleaned.append(dim)
        return cleaned

    @model_validator(mode="after")
    def _require_source(self) -> StyleReferenceRequest:
        if self.project_id is None and self.composition is None:
            raise ValueError("style_reference requires project_id or composition")
        return self


class CompositionStyleConditioningV1(StrictModel):
    """How a resolved reference embedding is applied (never a free artist enum)."""

    schema_version: Literal["composition.style_conditioning.v1"] = (
        EMBEDDING_STYLE_CONDITIONING_SCHEMA
    )
    mode: StyleConditioningMode = "prompt_features"
    reference_provenance: CompositionReferenceProvenanceV1
    # Bounded human-readable feature summary for LLM context (not a full vector).
    feature_summary: str = Field(..., min_length=1, max_length=8000)
    # Optional coarse tokenizer-compatible labels (genre/key only when inferred).
    tokenizer_labels: dict[str, str] = Field(default_factory=dict, max_length=8)
    # Compact vector hint only when dims ≤ configured prompt_vector_max_dims.
    vector_hint: list[float] | None = Field(default=None, max_length=64)
    artist_label_used: Literal[False] = False

    @field_validator("tokenizer_labels")
    @classmethod
    def _forbid_artist_tokenizer_keys(cls, value: dict[str, str]) -> dict[str, str]:
        for key in value:
            lowered = key.strip().lower()
            if lowered in _FORBIDDEN_ARTIST_FIELDS or "artist" in lowered or "composer" in lowered:
                raise ValueError(
                    f"tokenizer_labels must not include artist/composer keys ({key!r})"
                )
            if len(key) > 40 or len(value[key]) > 80:
                raise ValueError("tokenizer_labels keys/values exceed length caps")
        return value


# --- HTTP request/response envelopes (used by Task 6 routes) ------------------


class EmbedComputeRequest(StrictModel):
    composition: dict[str, Any]
    scope: CompositionEmbedScope = Field(default_factory=EmbedScopeComposition)
    model_id: str | None = Field(default=None, min_length=1, max_length=160)


class EmbedComputeResponse(StrictModel):
    embedding: CompositionEmbeddingV1
    warning_codes: list[EmbeddingWarningCode] = Field(default_factory=list, max_length=32)


class SimilaritySearchRequest(StrictModel):
    query: CompositionSimilarityQueryV1
    model_id: str | None = Field(default=None, min_length=1, max_length=160)


class SimilaritySearchResponse(StrictModel):
    hits: list[CompositionSimilarityHitV1] = Field(default_factory=list, max_length=200)
    query_fingerprint_prefix: str | None = Field(default=None, max_length=32)
    model_id: str = Field(..., min_length=1, max_length=160)
    profile_id: str = Field(default=EMBEDDING_PROFILE_ID, min_length=1, max_length=80)
    warning_codes: list[EmbeddingWarningCode] = Field(default_factory=list, max_length=32)
    musical_quality_claim: Literal[False] = False


class RelatedMotifsRequest(StrictModel):
    composition: dict[str, Any]
    motif_id: str = Field(..., min_length=1, max_length=120)
    occurrence_id: str | None = Field(default=None, min_length=1, max_length=120)
    top_k: int = Field(default=10, ge=1, le=50)
    search_cross_project: bool = False
    model_id: str | None = Field(default=None, min_length=1, max_length=160)


class RelatedMotifsResponse(StrictModel):
    hits: list[CompositionSimilarityHitV1] = Field(default_factory=list, max_length=50)
    model_id: str = Field(..., min_length=1, max_length=160)
    warning_codes: list[EmbeddingWarningCode] = Field(default_factory=list, max_length=32)
    musical_quality_claim: Literal[False] = False


class ReferenceResolveRequest(StrictModel):
    style_reference: StyleReferenceRequest
    model_id: str | None = Field(default=None, min_length=1, max_length=160)


class ReferenceResolveResponse(StrictModel):
    embedding: CompositionEmbeddingV1
    provenance: CompositionReferenceProvenanceV1
    conditioning: CompositionStyleConditioningV1 | None = None
    warning_codes: list[EmbeddingWarningCode] = Field(default_factory=list, max_length=32)


def assert_no_artist_fields(data: dict[str, Any], *, context: str) -> None:
    """Reject payloads that smuggle artist/composer identity into embedding DTOs."""
    for key in data:
        lowered = str(key).strip().lower()
        if lowered in _FORBIDDEN_ARTIST_FIELDS:
            logger.debug(
                "Rejected artist/composer field on embedding DTO",
                extra={"context": context, "field_name": key},
            )
            raise ValueError(
                f"{context}: artist/composer fields are not embedding dimensions ({key!r})"
            )


def public_error_message(code: EmbeddingErrorCode) -> str:
    return EMBEDDING_ERROR_CODES.get(code, code)


# Log schema constants once at import (INFO is fine; no vectors).
logger.info(
    "Embedding schema constants",
    extra={
        "schema_version": EMBEDDING_SCHEMA_VERSION,
        "scope_schema": EMBEDDING_SCOPE_SCHEMA_VERSION,
        "profile_id": EMBEDDING_PROFILE_ID,
        "algorithm_version": EMBEDDING_ALGORITHM_VERSION,
        "default_model_id": EMBEDDING_DEFAULT_MODEL_ID,
        "artist_as_style_id": False,
    },
)
