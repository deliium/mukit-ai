"""Experiment / eval / listening / metrics / compare contracts.

These schemas sit above ``music_transformer.train_config.v1`` and never claim
that symbolic metrics equal musical quality
(``musical_quality_claim`` is always ``false``).
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from app.music_transformer.schemas import (
    MusicTransformerConfigV1,
    MusicTransformerSampleConfigV1,
    MusicTransformerTrainConfigV1,
    StrictModel,
    canonical_json_dumps,
)
from app.tokenizer.schemas import TokenizerModelExpectationV1


logger = logging.getLogger(__name__)

MT_EXPERIMENT_SCHEMA = "music_transformer.experiment.v1"
MT_EVAL_CONFIG_SCHEMA = "music_transformer.eval_config.v1"
MT_LISTENING_CONFIG_SCHEMA = "music_transformer.listening_config.v1"
MT_LISTENING_SET_SCHEMA = "music_transformer.listening_set.v1"
MT_METRICS_ROW_SCHEMA = "music_transformer.metrics_row.v1"
MT_METRICS_SUMMARY_SCHEMA = "music_transformer.metrics_summary.v1"
MT_EVAL_REPORT_SCHEMA = "music_transformer.eval_report.v1"
MT_COMPARE_REPORT_SCHEMA = "music_transformer.compare_report.v1"

SymbolicMetricName = Literal[
    "valid_token_rate",
    "valid_composition_decode_rate",
    "pitch_class_distribution",
    "note_density_distribution",
    "rhythmic_distribution",
    "repetition",
    "interval_distribution",
    "instrument_range_violations",
    "tonal_consistency",
]

MetricStatus = Literal["ok", "unavailable", "skipped", "error"]
EvalMode = Literal["generate_from_prefix", "teacher_forced"]


class MusicTransformerEvalConfigV1(StrictModel):
    """``music_transformer.eval_config.v1`` — symbolic eval knobs (not quality)."""

    schema_version: Literal["music_transformer.eval_config.v1"] = MT_EVAL_CONFIG_SCHEMA
    max_samples: int = Field(default=8, ge=1, le=10_000)
    metrics: list[SymbolicMetricName] = Field(
        default_factory=lambda: [
            "valid_token_rate",
            "valid_composition_decode_rate",
            "pitch_class_distribution",
            "note_density_distribution",
            "rhythmic_distribution",
            "repetition",
            "interval_distribution",
            "instrument_range_violations",
            "tonal_consistency",
        ]
    )
    mode: EvalMode = "generate_from_prefix"
    max_new_tokens: int = Field(default=64, ge=1, le=8192)
    greedy: bool = True
    on_invalid: Literal["repair", "reject"] = "repair"
    enabled: bool = True

    def config_digest(self) -> str:
        payload = self.model_dump(mode="json")
        digest = hashlib.sha256(canonical_json_dumps(payload).encode("utf-8")).hexdigest()
        logger.debug(
            "Eval config digest",
            extra={"config_digest_prefix": digest[:12]},
        )
        return digest


class MusicTransformerListeningPromptV1(StrictModel):
    id: str = Field(min_length=1, max_length=128)
    seed: int = Field(default=0, ge=0)
    conditioning: dict[str, Any] = Field(default_factory=dict)
    prefix_path: str | None = None
    sample_overrides: dict[str, Any] = Field(default_factory=dict)


class MusicTransformerListeningSetV1(StrictModel):
    """``music_transformer.listening_set.v1`` — fixed prompts for human compare."""

    schema_version: Literal["music_transformer.listening_set.v1"] = MT_LISTENING_SET_SCHEMA
    prompts: list[MusicTransformerListeningPromptV1] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_ids(self) -> MusicTransformerListeningSetV1:
        ids = [p.id for p in self.prompts]
        if len(ids) != len(set(ids)):
            logger.debug(
                "Listening set validation failed",
                extra={"field_names": ["prompts"]},
            )
            raise ValueError("listening prompt ids must be unique")
        return self


class MusicTransformerListeningConfigV1(StrictModel):
    """``music_transformer.listening_config.v1``."""

    schema_version: Literal["music_transformer.listening_config.v1"] = MT_LISTENING_CONFIG_SCHEMA
    set_path: str | None = None
    enabled: bool = False
    sample: MusicTransformerSampleConfigV1 | None = None
    run_on_train_end: bool = True

    def config_digest(self) -> str:
        payload = self.model_dump(mode="json")
        digest = hashlib.sha256(canonical_json_dumps(payload).encode("utf-8")).hexdigest()
        logger.debug(
            "Listening config digest",
            extra={"config_digest_prefix": digest[:12]},
        )
        return digest


class MusicTransformerExperimentV1(StrictModel):
    """``music_transformer.experiment.v1`` — frozen run contract."""

    schema_version: Literal["music_transformer.experiment.v1"] = MT_EXPERIMENT_SCHEMA
    experiment_id: str = Field(min_length=1, max_length=200)
    output_root: str | None = None
    seed: int = Field(default=42, ge=0)
    device: str = "cpu"
    architecture: MusicTransformerConfigV1
    train: MusicTransformerTrainConfigV1
    eval: MusicTransformerEvalConfigV1 = Field(default_factory=MusicTransformerEvalConfigV1)
    listening: MusicTransformerListeningConfigV1 = Field(
        default_factory=MusicTransformerListeningConfigV1
    )
    dataset_version_id: str | None = None
    dataset_name: str | None = None
    tokenizer_expectation: TokenizerModelExpectationV1 | None = None
    created_at: datetime | None = None
    git_commit: str | None = None
    project_version: str | None = None

    @model_validator(mode="after")
    def _align_seeds(self) -> MusicTransformerExperimentV1:
        # experiment seed is authoritative when train.seed differs
        if self.train.seed != self.seed:
            logger.debug(
                "Aligning train.seed to experiment seed",
                extra={"field_names": ["seed", "train.seed"]},
            )
            object.__setattr__(
                self,
                "train",
                self.train.model_copy(update={"seed": self.seed}),
            )
        return self

    def config_digest(self) -> str:
        """Stable digest excluding wall-clock / git / project version."""
        payload = self.model_dump(mode="json")
        for key in ("created_at", "git_commit", "project_version"):
            payload.pop(key, None)
        digest = hashlib.sha256(canonical_json_dumps(payload).encode("utf-8")).hexdigest()
        logger.info(
            "Experiment config digest computed",
            extra={
                "experiment_id": self.experiment_id,
                "config_digest_prefix": digest[:12],
            },
        )
        return digest


class MusicTransformerMetricsRowV1(StrictModel):
    """``music_transformer.metrics_row.v1`` — one JSONL train/eval snapshot."""

    schema_version: Literal["music_transformer.metrics_row.v1"] = MT_METRICS_ROW_SCHEMA
    experiment_id: str
    global_step: int = Field(ge=0)
    epoch: int | None = None
    split: Literal["train", "val", "eval"] = "train"
    loss: float | None = None
    val_loss: float | None = None
    token_accuracy: float | None = None
    lr: float | None = None
    tokens_per_sec: float | None = None
    mem_mb: float | None = None
    notes: dict[str, Any] = Field(default_factory=dict)


class MusicTransformerMetricsSummaryV1(StrictModel):
    schema_version: Literal["music_transformer.metrics_summary.v1"] = MT_METRICS_SUMMARY_SCHEMA
    experiment_id: str
    final_step: int = 0
    final_train_loss: float | None = None
    best_val_loss: float | None = None
    best_step: int | None = None
    total_tokens: int = 0
    wall_time_sec: float | None = None
    early_stopped: bool = False
    row_count: int = 0


class MetricValueV1(StrictModel):
    name: SymbolicMetricName
    status: MetricStatus = "ok"
    value: float | dict[str, Any] | int | None = None
    detail: str | None = None


class MusicTransformerEvalReportV1(StrictModel):
    """``music_transformer.eval_report.v1`` — symbolic diagnostics only."""

    schema_version: Literal["music_transformer.eval_report.v1"] = MT_EVAL_REPORT_SCHEMA
    experiment_id: str | None = None
    global_step: int | None = None
    sample_count: int = 0
    musical_quality_claim: Literal[False] = False
    metrics: list[MetricValueV1] = Field(default_factory=list)
    disclaimer: str = (
        "Symbolic metrics are validity/distributional diagnostics only; "
        "they are not musical quality scores."
    )


class CompareDeltaV1(StrictModel):
    key: str
    a: float | int | str | None = None
    b: float | int | str | None = None
    delta: float | None = None


class MusicTransformerCompareReportV1(StrictModel):
    """``music_transformer.compare_report.v1`` — numeric deltas, no quality winner."""

    schema_version: Literal["music_transformer.compare_report.v1"] = MT_COMPARE_REPORT_SCHEMA
    a_id: str
    b_id: str
    configs_equal: bool = False
    seeds_equal: bool = False
    architecture_digests_equal: bool = False
    tokenizer_versions_equal: bool = False
    config_digest_a: str | None = None
    config_digest_b: str | None = None
    metric_deltas: list[CompareDeltaV1] = Field(default_factory=list)
    eval_deltas: list[CompareDeltaV1] = Field(default_factory=list)
    listening: list[CompareDeltaV1] = Field(default_factory=list)
    musical_quality_claim: Literal[False] = False
    disclaimer: str = (
        "Compare reports numeric/reproducibility deltas only; "
        "they do not rank musical quality."
    )
