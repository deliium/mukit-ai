"""Strict Pydantic contracts for the symbolic Music Transformer.

Architecture choice (locked)
----------------------------
Practical GPT-style **pre-norm** decoder-only Transformer LM:
token embedding + learned (or sinusoidal) absolute positions, stacked
pre-norm MHA+MLP blocks, final LayerNorm, linear LM head (optionally
weight-tied). Causal attention only. No relative-attention research
variants in v1.

Conditioning is tokenizer COND_* / structure prefix tokens — no parallel
modality tower. Generation always ends in tokenizer repair → decode →
Composition V2 validation (never invent notes from logits alone).
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.tokenizer.schemas import TokenizerModelExpectationV1, TokenizerProfile


logger = logging.getLogger(__name__)

MT_CONFIG_SCHEMA = "music_transformer.config.v1"
MT_TRAIN_CONFIG_SCHEMA = "music_transformer.train_config.v1"
MT_SAMPLE_CONFIG_SCHEMA = "music_transformer.sample_config.v1"
MT_CHECKPOINT_SCHEMA = "music_transformer.checkpoint.v1"
MT_GENERATE_REPORT_SCHEMA = "music_transformer.generate_report.v1"

PosEncodingKind = Literal["learned", "sinusoidal"]
NormKind = Literal["pre"]
StopReason = Literal["eos", "max_new_tokens", "rejected"]
GenerateStatus = Literal["ok", "repaired", "rejected"]
OptimizerKind = Literal["adamw"]
SchedulerKind = Literal["none", "cosine", "linear_warmup"]
PrecisionKind = Literal["fp32", "amp_fp16", "amp_bf16"]
PrecisionFallback = Literal["", "fp32"]
EarlyStopMetric = Literal["val_loss"]
EarlyStopMode = Literal["min", "max"]

MusicTransformerIssueCode = Literal[
    "invalid_config",
    "torch_unavailable",
    "device_unavailable",
    "checkpoint_missing",
    "checkpoint_corrupt",
    "tokenizer_version_mismatch",
    "vocab_hash_mismatch",
    "empty_prompt",
    "missing_bos",
    "decode_rejected",
    "integrity_failed",
    "no_notes",
    "train_data_empty",
    "sequence_truncated",
    "experiment_exists",
    "experiment_missing",
    "resume_mismatch",
    "precision_unsupported",
    "val_empty",
    "scheduler_mismatch",
    "optimizer_missing",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MusicTransformerConfigV1(StrictModel):
    """``music_transformer.config.v1`` — architecture hyperparameters."""

    schema_version: Literal["music_transformer.config.v1"] = MT_CONFIG_SCHEMA
    n_layers: int = Field(default=4, ge=1, le=64)
    d_model: int = Field(default=256, ge=16, le=8192)
    n_heads: int = Field(default=4, ge=1, le=128)
    d_ff: int | None = Field(default=None, ge=16, le=32768)
    ff_mult: int = Field(default=4, ge=1, le=16)
    dropout: float = Field(default=0.1, ge=0.0, le=0.9)
    max_seq_len: int = Field(default=512, ge=8, le=16384)
    vocab_size: int = Field(default=1075, ge=8, le=100_000)
    tie_embeddings: bool = True
    norm: NormKind = "pre"
    pos_encoding: PosEncodingKind = "learned"
    pad_id: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _validate_geometry(self) -> MusicTransformerConfigV1:
        if self.d_model % self.n_heads != 0:
            logger.debug(
                "Config validation failed",
                extra={"field_names": ["d_model", "n_heads"]},
            )
            raise ValueError("d_model must be divisible by n_heads")
        if self.d_ff is None:
            object.__setattr__(self, "d_ff", self.d_model * self.ff_mult)
        assert self.d_ff is not None
        if self.pad_id != 0:
            logger.debug(
                "Config validation failed",
                extra={"field_names": ["pad_id"]},
            )
            raise ValueError("pad_id must be 0 (tokenizer PAD)")
        return self

    def resolved_d_ff(self) -> int:
        return int(self.d_ff if self.d_ff is not None else self.d_model * self.ff_mult)

    def config_digest(self) -> str:
        payload = self.model_dump(mode="json")
        material = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
        logger.info(
            "Music Transformer config digest computed",
            extra={"config_digest_prefix": digest[:12], "n_layers": self.n_layers},
        )
        return digest


class EarlyStoppingConfigV1(StrictModel):
    """Optional early stop on validation loss (disabled by default for CI)."""

    enabled: bool = False
    metric: EarlyStopMetric = "val_loss"
    patience: int = Field(default=5, ge=1, le=10_000)
    min_delta: float = Field(default=0.0, ge=0.0)
    mode: EarlyStopMode = "min"


class MusicTransformerTrainConfigV1(StrictModel):
    """``music_transformer.train_config.v1`` — offline train knobs.

    Backward compatible with minimal fixtures (``tiny_train.json``): new fields
    have safe defaults so existing configs still validate.
    """

    schema_version: Literal["music_transformer.train_config.v1"] = MT_TRAIN_CONFIG_SCHEMA
    lr: float = Field(default=3e-4, gt=0.0, le=1.0)
    batch_size: int = Field(default=4, ge=1, le=1024)
    steps: int = Field(default=100, ge=1, le=10_000_000)
    epochs: int | None = Field(default=None, ge=1, le=10_000)
    seed: int = Field(default=42, ge=0)
    max_seq_len: int = Field(default=512, ge=8, le=16384)
    grad_clip: float | None = Field(default=1.0, ge=0.0)
    log_every: int = Field(default=10, ge=1)
    dataset_dir: str | None = None
    inputs_glob: str | None = None
    architecture: MusicTransformerConfigV1 | None = None
    # Expanded experiment trainer fields
    dataset_version_id: str | None = None
    tokenizer_version: str | None = None
    tokenizer_vocab_hash: str | None = None
    optimizer: OptimizerKind = "adamw"
    weight_decay: float = Field(default=0.01, ge=0.0, le=1.0)
    scheduler: SchedulerKind = "none"
    warmup_steps: int = Field(default=0, ge=0, le=1_000_000)
    precision: PrecisionKind = "fp32"
    precision_fallback: PrecisionFallback = ""
    grad_accum_steps: int = Field(default=1, ge=1, le=1024)
    checkpoint_interval: int = Field(default=0, ge=0, le=10_000_000)
    eval_interval: int = Field(default=0, ge=0, le=10_000_000)
    early_stopping: EarlyStoppingConfigV1 = Field(default_factory=EarlyStoppingConfigV1)
    val_fraction: float | None = Field(default=None, ge=0.0, le=0.5)
    val_inputs_glob: str | None = None

    def config_digest(self) -> str:
        payload = self.model_dump(mode="json")
        digest = hashlib.sha256(canonical_json_dumps(payload).encode("utf-8")).hexdigest()
        logger.info(
            "Train config digest computed",
            extra={"config_digest_prefix": digest[:12], "steps": self.steps},
        )
        return digest


class MusicTransformerSampleConfigV1(StrictModel):
    """``music_transformer.sample_config.v1`` — sampling + constraint flags."""

    schema_version: Literal["music_transformer.sample_config.v1"] = MT_SAMPLE_CONFIG_SCHEMA
    temperature: float = Field(default=1.0, ge=0.0, le=5.0)
    top_k: int | None = Field(default=None, ge=0)
    top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    greedy: bool = False
    max_new_tokens: int = Field(default=128, ge=1, le=8192)
    ban_pad: bool = True
    require_bos: bool = True
    family_transition_hook: bool = True
    on_invalid: Literal["repair", "reject"] = "repair"


class MusicTransformerTrainingCardV1(StrictModel):
    seed: int
    steps: int = 0
    epochs: int | None = None
    batch_size: int = 1
    lr: float = 0.0
    max_seq_len: int = 512
    device: str = "cpu"
    git_commit: str | None = None
    project_version: str | None = None
    final_loss: float | None = None
    # Resume / experiment metadata
    experiment_id: str | None = None
    global_step: int = 0
    epoch: int = 0
    optimizer: OptimizerKind = "adamw"
    scheduler: SchedulerKind = "none"
    precision: PrecisionKind = "fp32"
    grad_accum_steps: int = 1
    weight_decay: float | None = None
    warmup_steps: int | None = None


class MusicTransformerCheckpointCardV1(StrictModel):
    """``music_transformer.checkpoint.v1`` — sidecar / embedded card."""

    schema_version: Literal["music_transformer.checkpoint.v1"] = MT_CHECKPOINT_SCHEMA
    package_version: str = "music_transformer.v1"
    architecture: MusicTransformerConfigV1
    architecture_digest: str
    tokenizer: TokenizerModelExpectationV1
    dataset_version_id: str | None = None
    dataset_name: str | None = None
    training: MusicTransformerTrainingCardV1
    created_at: datetime | None = None
    tokenizer_profile: TokenizerProfile | None = None


class MusicTransformerGenerateReportV1(StrictModel):
    schema_version: Literal["music_transformer.generate_report.v1"] = MT_GENERATE_REPORT_SCHEMA
    status: GenerateStatus
    stop_reason: StopReason
    issue_codes: list[MusicTransformerIssueCode] = Field(default_factory=list)
    prompt_tokens: int = 0
    generated_tokens: int = 0
    notes_out: int = 0
    bar_count: int | None = None
    tokenizer_version: str | None = None
    vocab_hash_prefix: str | None = None
    repair_result: str | None = None
    conditioning: dict[str, Any] = Field(default_factory=dict)


def tiny_test_config(*, vocab_size: int) -> MusicTransformerConfigV1:
    """Micro architecture for CPU CI / fixtures."""
    return MusicTransformerConfigV1(
        n_layers=2,
        d_model=64,
        n_heads=2,
        d_ff=128,
        ff_mult=2,
        dropout=0.0,
        max_seq_len=128,
        vocab_size=vocab_size,
        tie_embeddings=True,
        norm="pre",
        pos_encoding="learned",
        pad_id=0,
    )


def canonical_json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
