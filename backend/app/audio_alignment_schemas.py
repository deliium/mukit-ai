"""Strict ``audio.alignment.v1`` + ``audio.roundtrip.provenance.v1`` DTOs.

Alignment is a non-playable sibling of recovery Bind assets. It never lives on
``composition.v2`` note events (``extra=\"forbid\"``). Stem bindings in v1 are
role → track_id only — no durable stem WAV asset ids.

Logging: DEBUG accept/reject field counts; INFO method + overall_confidence;
never dump large map arrays at INFO.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


logger = logging.getLogger(__name__)

AUDIO_ALIGNMENT_SCHEMA_VERSION: Literal["audio.alignment.v1"] = "audio.alignment.v1"
AUDIO_ROUNDTRIP_PROVENANCE_SCHEMA_VERSION: Literal["audio.roundtrip.provenance.v1"] = (
    "audio.roundtrip.provenance.v1"
)
AUDIO_ALIGNMENT_BOUND_SCHEMA_VERSION: Literal["audio.alignment.bound.v1"] = (
    "audio.alignment.bound.v1"
)

AudioAlignmentMethod = Literal[
    "scaffolding",
    "timeline_parametric",
    "defaulted",
    "user_adjusted",  # reserved; unused in v1 UI
]

AudioAlignmentStemRole = Literal[
    "vocals",
    "melody",
    "bass",
    "drums",
    "harmonic",
    "other",
]

# Closed issue-code registry (alignment quality diagnostics; not HTTP errors).
AUDIO_ALIGNMENT_ISSUE_CODES: dict[str, str] = {
    "alignment_low_confidence": "Overall alignment confidence is below the soft threshold.",
    "alignment_tempo_diverged": "Composition tempo/meter diverged from scaffolding used at Bind.",
    "alignment_missing_source": "Source audio asset id is missing; map cannot be trusted.",
    "alignment_rebuild_failed": "Alignment rebuild failed; prior map retained or cleared.",
    "alignment_defaulted_offset": "Downbeat offset was defaulted (scaffolding missing or zero-trust).",
    "alignment_sparse_beat_grid": "Beat-grid confidence is low; parametric map has higher uncertainty.",
}

AudioAlignmentIssueCode = Literal[
    "alignment_low_confidence",
    "alignment_tempo_diverged",
    "alignment_missing_source",
    "alignment_rebuild_failed",
    "alignment_defaulted_offset",
    "alignment_sparse_beat_grid",
]

# Stable HTTP-mapped / domain error codes for alignment build / discovery.
AUDIO_ALIGNMENT_ERROR_CODES: dict[str, str] = {
    "audio_alignment_missing_source": "Alignment requires a bound source_audio asset.",
    "audio_alignment_rebuild_failed": "Alignment build or rebuild failed validation.",
    "audio_alignment_not_found": "Alignment asset was not found for the project/job.",
    "audio_alignment_invalid_document": "Alignment document failed schema validation.",
    "audio_alignment_bound_not_found": "No bound recovery job with alignment for this project.",
}

AudioAlignmentErrorCode = Literal[
    "audio_alignment_missing_source",
    "audio_alignment_rebuild_failed",
    "audio_alignment_not_found",
    "audio_alignment_invalid_document",
    "audio_alignment_bound_not_found",
]


class AudioAlignmentError(Exception):
    """Domain error for alignment build / discovery (mapped to HTTP by routers)."""

    def __init__(
        self,
        code: AudioAlignmentErrorCode,
        message: str,
        *,
        http_status: int,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.details = details or {}
        logger.error(
            "Audio alignment domain error",
            extra={
                "error_code": code,
                "http_status": http_status,
                "detail_keys": sorted(self.details.keys()),
            },
        )


class AudioAlignmentStemBinding(BaseModel):
    """v1 stem binding: role → track_id only (no durable stem WAV asset ids)."""

    model_config = ConfigDict(extra="forbid")

    stem: AudioAlignmentStemRole
    track_id: str = Field(min_length=1, max_length=64)
    provisional_stem_label: str | None = Field(
        default=None, min_length=1, max_length=64
    )


class AudioAlignmentStemQuality(BaseModel):
    """Per-stem-role quality hint (roles only — never PCM or event arrays)."""

    model_config = ConfigDict(extra="forbid")

    stem: AudioAlignmentStemRole
    confidence: float = Field(ge=0.0, le=1.0)


class AudioAlignmentQuality(BaseModel):
    """Alignment quality / confidence — never a V2 note field."""

    model_config = ConfigDict(extra="forbid")

    overall_confidence: float = Field(ge=0.0, le=1.0)
    tempo_confidence: float = Field(ge=0.0, le=1.0)
    beat_grid_confidence: float = Field(ge=0.0, le=1.0)
    offset_uncertainty_ms: float = Field(ge=0.0, le=60_000.0)
    method: AudioAlignmentMethod
    issues: list[AudioAlignmentIssueCode] = Field(default_factory=list, max_length=32)
    stem_qualities: list[AudioAlignmentStemQuality] = Field(
        default_factory=list, max_length=16
    )

    @field_validator("issues")
    @classmethod
    def _unique_issues(
        cls, issues: list[AudioAlignmentIssueCode]
    ) -> list[AudioAlignmentIssueCode]:
        seen: set[str] = set()
        out: list[AudioAlignmentIssueCode] = []
        for code in issues:
            if code in seen:
                continue
            seen.add(code)
            out.append(code)
        return out


class AudioAlignmentMapParams(BaseModel):
    """Parametric map inputs (tempo + downbeat offset + timeline origin)."""

    model_config = ConfigDict(extra="forbid")

    tempo_bpm: float = Field(ge=20.0, le=400.0)
    ticks_per_quarter: int = Field(ge=24, le=9600)
    downbeat_offset_seconds: float = Field(ge=0.0, default=0.0)
    origin_tick: int = Field(ge=0, default=0)
    meter: str = Field(default="4/4", min_length=3, max_length=16)
    # Scaffolding tempo captured at build time (for divergence detection).
    scaffolding_tempo_bpm: float | None = Field(default=None, ge=20.0, le=400.0)


class AudioAlignmentV1(BaseModel):
    """Versioned audio↔symbolic alignment document (non-playable)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["audio.alignment.v1"] = AUDIO_ALIGNMENT_SCHEMA_VERSION
    source_audio_asset_id: str = Field(min_length=1, max_length=64)
    result_asset_id: str | None = Field(default=None, min_length=1, max_length=64)
    job_id: str | None = Field(default=None, min_length=1, max_length=64)
    project_id: str | None = Field(default=None, min_length=1, max_length=64)
    # composition.snapshot.v1 fingerprint at build / Bind time
    composition_fingerprint: str = Field(min_length=8, max_length=128)
    map: AudioAlignmentMapParams
    stem_bindings: list[AudioAlignmentStemBinding] = Field(
        default_factory=list, max_length=16
    )
    quality: AudioAlignmentQuality
    created_at: str = Field(min_length=1, max_length=64)
    # Explicit contract reminder — alignment is never playable score data.
    playable: Literal[False] = False

    @field_validator("stem_bindings")
    @classmethod
    def _unique_stem_roles(
        cls, bindings: list[AudioAlignmentStemBinding]
    ) -> list[AudioAlignmentStemBinding]:
        seen: set[str] = set()
        for binding in bindings:
            if binding.stem in seen:
                raise ValueError(f"duplicate stem binding role: {binding.stem}")
            seen.add(binding.stem)
        return bindings

    @model_validator(mode="after")
    def _log_accept(self) -> AudioAlignmentV1:
        field_count = len(self.__class__.model_fields)
        logger.debug(
            "audio.alignment.v1 accepted",
            extra={
                "field_count": field_count,
                "method": self.quality.method,
                "issue_count": len(self.quality.issues),
                "stem_binding_count": len(self.stem_bindings),
                "source_asset_prefix": self.source_audio_asset_id[:8],
            },
        )
        logger.info(
            "Alignment document validated",
            extra={
                "method": self.quality.method,
                "overall_confidence": round(self.quality.overall_confidence, 4),
                "source_asset_prefix": self.source_audio_asset_id[:8],
            },
        )
        return self


class AudioRoundtripProvenanceV1(BaseModel):
    """Secret-safe lineage: source → recovery → alignment → composition → render.

    Never embeds PCM, prompts, full overlays, or event arrays.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["audio.roundtrip.provenance.v1"] = (
        AUDIO_ROUNDTRIP_PROVENANCE_SCHEMA_VERSION
    )
    source_audio_asset_id: str | None = Field(default=None, min_length=1, max_length=64)
    source_sha256_prefix: str | None = Field(default=None, min_length=8, max_length=16)
    result_asset_id: str | None = Field(default=None, min_length=1, max_length=64)
    alignment_asset_id: str | None = Field(default=None, min_length=1, max_length=64)
    recovery_job_id: str | None = Field(default=None, min_length=1, max_length=64)
    composition_fingerprint: str | None = Field(
        default=None, min_length=8, max_length=128
    )
    revision_id: str | None = Field(default=None, min_length=1, max_length=64)
    neural_render_id: str | None = Field(default=None, min_length=1, max_length=64)
    bound_at: str | None = Field(default=None, min_length=1, max_length=64)
    rendered_at: str | None = Field(default=None, min_length=1, max_length=64)
    alignment_schema_version: Literal["audio.alignment.v1"] | None = None
    recovery_result_schema_version: Literal["audio.recovery.result.v1"] | None = None

    @model_validator(mode="after")
    def _log_accept(self) -> AudioRoundtripProvenanceV1:
        filled = sum(
            1
            for key in (
                self.source_audio_asset_id,
                self.result_asset_id,
                self.alignment_asset_id,
                self.composition_fingerprint,
                self.neural_render_id,
            )
            if key
        )
        logger.debug(
            "audio.roundtrip.provenance.v1 accepted",
            extra={"filled_chain_slots": filled, "field_count": len(self.__class__.model_fields)},
        )
        logger.info(
            "Round-trip provenance fragment validated",
            extra={
                "has_source": bool(self.source_audio_asset_id),
                "has_alignment": bool(self.alignment_asset_id),
                "has_render": bool(self.neural_render_id),
                "fp_prefix": (self.composition_fingerprint or "")[:12] or None,
            },
        )
        return self


class AudioAlignmentBoundJobV1(BaseModel):
    """One bound recovery job summary for project reopen / hydrate."""

    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(min_length=1, max_length=64)
    source_audio_asset_id: str = Field(min_length=1, max_length=64)
    result_asset_id: str = Field(min_length=1, max_length=64)
    alignment_asset_id: str | None = Field(default=None, min_length=1, max_length=64)
    source_sha256_prefix: str | None = Field(default=None, min_length=8, max_length=16)
    result_sha256_prefix: str | None = Field(default=None, min_length=8, max_length=16)
    alignment_sha256_prefix: str | None = Field(
        default=None, min_length=8, max_length=16
    )
    bound_at: str | None = Field(default=None, min_length=1, max_length=64)
    preview_fingerprint: str | None = Field(default=None, min_length=8, max_length=128)


class AudioAlignmentBoundDiscoveryV1(BaseModel):
    """Project-scoped bound discovery response for FE hydrate after reopen."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["audio.alignment.bound.v1"] = (
        AUDIO_ALIGNMENT_BOUND_SCHEMA_VERSION
    )
    project_id: str = Field(min_length=1, max_length=64)
    bound: bool = False
    # Latest bound job (preferred hydrate target); null when unbound.
    latest: AudioAlignmentBoundJobV1 | None = None
    jobs: list[AudioAlignmentBoundJobV1] = Field(default_factory=list, max_length=64)
