"""Strict Pydantic contracts for the offline symbolic dataset pipeline."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.composition_schemas import CompositionV2


logger = logging.getLogger(__name__)

DATASET_PIPELINE_SCHEMA = "dataset.pipeline.v1"
DATASET_MANIFEST_SCHEMA = "dataset.manifest.v1"
DATASET_ITEM_SCHEMA = "dataset.item.v1"
DATASET_EXAMPLE_SCHEMA = "dataset.example.v1"
DATASET_STATS_SCHEMA = "dataset.stats.v1"

ProvenanceStatus = Literal[
    "verified_redistributable",
    "user_owned",
    "public_domain",
    "restricted",
    "unknown",
]

TRAIN_ELIGIBLE_STATUSES: frozenset[str] = frozenset(
    {"verified_redistributable", "user_owned", "public_domain"}
)

SegmentationMode = Literal["bars", "sections", "token_limit", "phrases"]
TokenProxyFormula = Literal["note_events", "notes_plus_control"]
PhraseFallbackMode = Literal["bars", "sections", "skip"]
SplitName = Literal["train", "validation", "test"]
SourceFormat = Literal["midi", "musicxml", "mxl", "composition_json"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DatasetProvenance(StrictModel):
    status: ProvenanceStatus
    license: str | None = None
    license_spdx: str | None = None
    source_url: str | None = None
    source_reference: str | None = None
    composer: str | None = None
    author: str | None = None
    user_owned_attested: bool = False

    @model_validator(mode="after")
    def _require_attestation_fields(self) -> DatasetProvenance:
        if self.status == "user_owned" and not self.user_owned_attested:
            logger.debug(
                "Provenance validation failed",
                extra={"field_names": ["user_owned_attested"], "status": self.status},
            )
            raise ValueError("user_owned provenance requires user_owned_attested=true")
        if self.status == "verified_redistributable":
            if not (self.license or self.license_spdx):
                logger.debug(
                    "Provenance validation failed",
                    extra={"field_names": ["license", "license_spdx"], "status": self.status},
                )
                raise ValueError(
                    "verified_redistributable requires license or license_spdx"
                )
            if not (self.source_url or self.source_reference):
                logger.debug(
                    "Provenance validation failed",
                    extra={
                        "field_names": ["source_url", "source_reference"],
                        "status": self.status,
                    },
                )
                raise ValueError(
                    "verified_redistributable requires source_url or source_reference"
                )
        if self.status == "public_domain" and not (self.source_url or self.source_reference):
            logger.debug(
                "Provenance validation failed",
                extra={
                    "field_names": ["source_url", "source_reference"],
                    "status": self.status,
                },
            )
            raise ValueError("public_domain requires source_url or source_reference")
        return self


class DatasetLabels(StrictModel):
    genre: str | None = None
    style: str | None = None
    tags: list[str] = Field(default_factory=list)


class DatasetInstrumentSummary(StrictModel):
    track_id: str
    name: str
    role: str
    program: int | None = None
    is_drum: bool = False


class DatasetIngestIssueSummary(StrictModel):
    codes: list[str] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    note_count: int = 0
    bar_count: int = 0
    track_count: int = 0
    source_bytes: int | None = None
    source_format: SourceFormat | None = None


class DatasetPipelineSource(StrictModel):
    """One source root or file entry in ``dataset.pipeline.v1``."""

    path: str
    default_provenance: ProvenanceStatus | None = None
    license: str | None = None
    license_spdx: str | None = None
    source_url: str | None = None
    source_reference: str | None = None
    composer: str | None = None
    author: str | None = None
    labels: DatasetLabels | None = None
    user_owned_attested: bool = False
    glob: str | None = None


class DatasetSegmentationConfig(StrictModel):
    modes: list[SegmentationMode] = Field(default_factory=lambda: ["bars"])
    token_limit: int = Field(default=256, ge=1, le=100_000)
    token_proxy: TokenProxyFormula = "note_events"
    phrase_fallback: PhraseFallbackMode = "bars"
    # When sections mode is requested with other modes, skip trivial full-score unsectioned.
    skip_trivial_unsectioned: bool = True


class DatasetNearDupConfig(StrictModel):
    enabled: bool = True
    onset_grid_ticks: int = Field(default=120, ge=1, le=9600)
    sequence_jaccard_threshold: float = Field(default=0.92, ge=0.0, le=1.0)


class DatasetEligibilityPolicy(StrictModel):
    train_statuses: list[ProvenanceStatus] = Field(
        default_factory=lambda: list(TRAIN_ELIGIBLE_STATUSES)
    )
    include_non_trainable_in_eval: bool = False
    # Unsafe override: allow unknown/restricted into train (default off; logs ERROR when used).
    allow_unsafe_train_pollution: bool = False


class DatasetNormalizationConfig(StrictModel):
    target_ppq: int = Field(default=480, ge=24, le=9600)
    collapse_dup_notes: bool = True
    embed_composition_in_item: bool = True
    store_cas_blob: bool = True


class DatasetPipelineConfig(StrictModel):
    """``dataset.pipeline.v1`` operator config."""

    schema_version: Literal["dataset.pipeline.v1"] = DATASET_PIPELINE_SCHEMA
    dataset_name: str = Field(..., min_length=1, max_length=128)
    sources: list[DatasetPipelineSource] = Field(..., min_length=1)
    output_root: str | None = None
    segmentation: DatasetSegmentationConfig = Field(default_factory=DatasetSegmentationConfig)
    split_seed: int = Field(default=20260921, ge=0)
    train_ratio: float = Field(default=0.8, ge=0.0, le=1.0)
    val_ratio: float = Field(default=0.1, ge=0.0, le=1.0)
    test_ratio: float = Field(default=0.1, ge=0.0, le=1.0)
    normalization: DatasetNormalizationConfig = Field(
        default_factory=DatasetNormalizationConfig
    )
    near_dup: DatasetNearDupConfig = Field(default_factory=DatasetNearDupConfig)
    eligibility: DatasetEligibilityPolicy = Field(default_factory=DatasetEligibilityPolicy)
    retain_sources: bool = False
    default_provenance: ProvenanceStatus | None = None

    @model_validator(mode="after")
    def _ratios_sum_approx_one(self) -> DatasetPipelineConfig:
        total = self.train_ratio + self.val_ratio + self.test_ratio
        if abs(total - 1.0) > 1e-6:
            logger.debug(
                "Pipeline config validation failed",
                extra={"field_names": ["train_ratio", "val_ratio", "test_ratio"]},
            )
            raise ValueError("train_ratio + val_ratio + test_ratio must equal 1.0")
        return self


class DatasetItemV1(StrictModel):
    schema_version: Literal["dataset.item.v1"] = DATASET_ITEM_SCHEMA
    item_id: str = Field(..., min_length=1)
    source_id: str = Field(..., min_length=1)
    dataset_name: str = Field(..., min_length=1)
    provenance: DatasetProvenance
    train_eligible: bool
    # Optional sidecar override (Part H). Not folded into dataset_version_id digests.
    use_policy: Literal["training_allowed", "reference_only", "no_training"] | None = None
    labels: DatasetLabels = Field(default_factory=DatasetLabels)
    instruments: list[DatasetInstrumentSummary] = Field(default_factory=list)
    composition: CompositionV2 | None = None
    composition_blob_hash: str | None = None
    content_hash: str | None = None
    source_bytes_hash: str | None = None
    ingest_report: DatasetIngestIssueSummary = Field(
        default_factory=DatasetIngestIssueSummary
    )
    cluster_id: str | None = None
    exact_duplicate_of: str | None = None
    near_duplicate_of: str | None = None
    is_canonical: bool = True

    @model_validator(mode="after")
    def _require_composition_or_blob(self) -> DatasetItemV1:
        if self.composition is None and not self.composition_blob_hash:
            logger.debug(
                "Item validation failed",
                extra={"field_names": ["composition", "composition_blob_hash"]},
            )
            raise ValueError("item requires composition or composition_blob_hash")
        return self


class DatasetExampleV1(StrictModel):
    schema_version: Literal["dataset.example.v1"] = DATASET_EXAMPLE_SCHEMA
    example_id: str = Field(..., min_length=1)
    parent_item_id: str = Field(..., min_length=1)
    segmentation_mode: SegmentationMode
    start_tick: int = Field(..., ge=0)
    end_tick: int = Field(..., gt=0)
    composition: CompositionV2
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _window_order(self) -> DatasetExampleV1:
        if self.end_tick <= self.start_tick:
            logger.debug(
                "Example validation failed",
                extra={"field_names": ["start_tick", "end_tick"]},
            )
            raise ValueError("end_tick must be greater than start_tick")
        return self


class DatasetManifestCounts(StrictModel):
    sources: int = 0
    items: int = 0
    examples: int = 0
    train_eligible_items: int = 0
    excluded_items: int = 0
    clusters: int = 0
    train_examples: int = 0
    validation_examples: int = 0
    test_examples: int = 0


class DatasetManifestDigests(StrictModel):
    config_digest: str
    item_content_digests_sha256: str
    version_payload_sha256: str


class DatasetManifestV1(StrictModel):
    schema_version: Literal["dataset.manifest.v1"] = DATASET_MANIFEST_SCHEMA
    dataset_name: str
    dataset_version_id: str
    pipeline_version: str
    created_at: datetime
    counts: DatasetManifestCounts
    digests: DatasetManifestDigests
    schema_versions: dict[str, str] = Field(default_factory=dict)
    split_seed: int
    fingerprint_profile: str

    @classmethod
    def build_logged(cls, **kwargs: Any) -> DatasetManifestV1:
        manifest = cls(**kwargs)
        logger.info(
            "Dataset manifest model built",
            extra={
                "dataset_name": manifest.dataset_name,
                "version_id_prefix": manifest.dataset_version_id[:12],
                "pipeline_version": manifest.pipeline_version,
                "item_count": manifest.counts.items,
                "example_count": manifest.counts.examples,
            },
        )
        return manifest


class DatasetStatsSection(StrictModel):
    file_count: int = 0
    item_count: int = 0
    example_count: int = 0
    duration_hours: float = 0.0
    total_bars: int = 0
    note_count: int = 0
    instrument_program_distribution: dict[str, int] = Field(default_factory=dict)
    key_distribution: dict[str, int] = Field(default_factory=dict)
    time_signature_distribution: dict[str, int] = Field(default_factory=dict)
    example_note_length_histogram: dict[str, int] = Field(default_factory=dict)
    example_tick_length_histogram: dict[str, int] = Field(default_factory=dict)
    provenance_status_counts: dict[str, int] = Field(default_factory=dict)
    train_eligible_count: int = 0
    excluded_count: int = 0
    duplicate_cluster_count: int = 0
    exact_duplicate_count: int = 0
    near_duplicate_count: int = 0


class DatasetStatsV1(StrictModel):
    schema_version: Literal["dataset.stats.v1"] = DATASET_STATS_SCHEMA
    dataset_name: str
    dataset_version_id: str
    inventory: DatasetStatsSection
    eligible: DatasetStatsSection


class SplitJsonlRow(StrictModel):
    example_id: str
    parent_item_id: str
    cluster_id: str
    path: str
    split: SplitName
