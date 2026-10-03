"""Non-playable documents for Model Lab experiments.

These models never carry note events, shell/argv fields, or free-form absolute
weight paths. This module does not import FastAPI, torch, stores, or
``ai_agents``.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

logger = logging.getLogger(__name__)

EXPERIMENT_SCHEMA = "model.lab.experiment.v1"
CREATE_SCHEMA = "model.lab.create.v1"
METRICS_SCHEMA = "model.lab.metrics.v1"
COMPARE_SCHEMA = "model.lab.compare.v1"
REGISTER_SCHEMA = "model.lab.register.v1"
STATUS_SCHEMA = "model.lab.status.v1"

EXPERIMENT_ID_PATTERN = re.compile(r"^mtlab_[0-9a-f]{16}$")
DISPLAY_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,63}$")
REGISTRY_MODEL_ID_PATTERN = re.compile(r"^lab:mtlab_[0-9a-f]{16}$")
HEX_PREFIX_PATTERN = re.compile(r"^[0-9a-f]{8,64}$")

FORBIDDEN_NOTE_KEYS = frozenset(
    {
        "events",
        "notes",
        "pitch",
        "pitches",
        "midi_events",
        "composition",
        "composition_json",
    }
)
FORBIDDEN_SHELL_KEYS = frozenset({"command", "argv", "shell", "subprocess"})
FORBIDDEN_PATH_KEYS = frozenset(
    {
        "checkpoint_path",
        "weight_path",
        "weights_path",
        "model_path",
        "absolute_path",
        "checkpoint_abs_path",
    }
)

ModelLabEngine = Literal["fake", "torch"]
ModelLabStatus = Literal["queued", "running", "complete", "failed", "stopped", "deleted"]
TokenizerPresetId = Literal["core", "core_harmony"]
ArchitecturePresetId = Literal["tiny_lab", "small_lab"]

LAB_STATUSES = frozenset(
    {"queued", "running", "complete", "failed", "stopped", "deleted"}
)
TOKENIZER_PRESET_IDS = frozenset({"core", "core_harmony"})
ARCHITECTURE_PRESET_IDS = frozenset({"tiny_lab", "small_lab"})


class ModelLabError(Exception):
    """Domain error mapped to a structured HTTP detail."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int = 422,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message[:200]
        self.http_status = http_status
        self.details = details or {}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _log_schema_rejection(field_name: str, code: str) -> None:
    logger.debug(
        "Model Lab schema rejected",
        extra={"field_name": field_name, "code": code},
    )


def scan_forbidden_keys(payload: Any, forbidden: frozenset[str]) -> list[str]:
    """Return forbidden key names found anywhere in ``payload``."""
    found: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_text = str(key)
            if key_text in forbidden:
                found.append(key_text)
            found.extend(scan_forbidden_keys(value, forbidden))
    elif isinstance(payload, list):
        for item in payload:
            found.extend(scan_forbidden_keys(item, forbidden))
    return found


def scan_embedded_note_keys(payload: Any) -> list[str]:
    return scan_forbidden_keys(payload, FORBIDDEN_NOTE_KEYS)


def reject_model_lab_payload(payload: Any) -> None:
    """Refuse shell/argv, note material, and free-form absolute weight path keys."""
    shell = scan_forbidden_keys(payload, FORBIDDEN_SHELL_KEYS)
    if shell:
        field_name = shell[0]
        _log_schema_rejection(field_name, "model_lab_payload_refused")
        raise ModelLabError(
            "model_lab_payload_refused",
            "Model Lab requests cannot embed shell or argv fields.",
            http_status=422,
            details={"field_name": field_name},
        )
    path_keys = scan_forbidden_keys(payload, FORBIDDEN_PATH_KEYS)
    if path_keys:
        field_name = path_keys[0]
        _log_schema_rejection(field_name, "model_lab_payload_refused")
        raise ModelLabError(
            "model_lab_payload_refused",
            "Model Lab requests cannot embed free-form weight paths.",
            http_status=422,
            details={"field_name": field_name},
        )
    notes = scan_embedded_note_keys(payload)
    if notes:
        field_name = notes[0]
        _log_schema_rejection(field_name, "embedded_note_material")
        raise ModelLabError(
            "embedded_note_material",
            "Model Lab documents cannot embed note events.",
            http_status=422,
            details={"field_name": field_name},
        )


def map_model_lab_error_to_http(exc: ModelLabError) -> tuple[int, dict[str, Any]]:
    """Map domain errors to ``(status, detail)`` for HTTPException."""
    detail: dict[str, Any] = {"code": exc.code, "message": exc.message}
    safe: dict[str, Any] = {}
    for key, value in exc.details.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe[key] = value
        elif isinstance(value, list) and all(isinstance(item, str) for item in value):
            safe[key] = value[:16]
    if safe:
        detail["details"] = safe
    return int(exc.http_status), detail


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _reject_unsafe_payload(cls, value: Any) -> Any:
        if isinstance(value, dict):
            try:
                reject_model_lab_payload(value)
            except ModelLabError as exc:
                raise ValueError(exc.code) from exc
        return value


class ModelLabTokenizerExpectationV1(_Strict):
    """Tokenizer expectation summary stored on the experiment index."""

    expected_tokenizer_version: str = Field(..., min_length=1, max_length=64)
    vocab_hash_prefix: str = Field(..., min_length=8, max_length=64)

    @field_validator("vocab_hash_prefix")
    @classmethod
    def _hash_prefix(cls, value: str) -> str:
        text = value.strip().lower()
        if HEX_PREFIX_PATTERN.fullmatch(text) is None:
            _log_schema_rejection("vocab_hash_prefix", "model_lab_tokenizer_hash")
            raise ValueError("vocab_hash_prefix must be lowercase hex")
        return text[:16]


class ModelLabRuntimeSummaryV1(_Strict):
    """Resource summary — never a shell transcript."""

    wall_ms: int = Field(default=0, ge=0)
    device: str = Field(default="cpu", min_length=1, max_length=32)
    peak_mem_mb: float | None = Field(default=None, ge=0.0)
    tokens_per_sec: float | None = Field(default=None, ge=0.0)


class ModelLabCheckpointRefV1(_Strict):
    """Pointer to a step-named checkpoint under the experiment FS."""

    step: int = Field(..., ge=0)
    basename: str = Field(..., min_length=1, max_length=128)
    card_present: bool = False
    weights_present: bool = False

    @field_validator("basename")
    @classmethod
    def _basename_only(cls, value: str) -> str:
        text = value.strip()
        if not text or "/" in text or "\\" in text or text.startswith("."):
            _log_schema_rejection("basename", "model_lab_checkpoint_basename")
            raise ValueError("basename must be a relative file name")
        return text


class ModelLabExperimentV1(_Strict):
    """``model.lab.experiment.v1`` index + API view."""

    schema_version: Literal["model.lab.experiment.v1"] = EXPERIMENT_SCHEMA
    id: str
    display_name: str
    status: ModelLabStatus
    dataset_version_id: str = Field(..., min_length=1, max_length=128)
    dataset_name: str = Field(default="", max_length=128)
    tokenizer_expectation: ModelLabTokenizerExpectationV1
    architecture_digest_prefix: str = Field(..., min_length=8, max_length=64)
    train_digest_prefix: str = Field(..., min_length=8, max_length=64)
    seed: int = Field(..., ge=0)
    checkpoint_refs: list[ModelLabCheckpointRefV1] = Field(default_factory=list)
    runtime: ModelLabRuntimeSummaryV1 = Field(default_factory=ModelLabRuntimeSummaryV1)
    evaluation_version: str | None = None
    listening_set_digest: str | None = None
    registered_checkpoint_step: int | None = Field(default=None, ge=0)
    registry_model_id: str | None = None
    engine: ModelLabEngine
    owner_actor_id: str | None = Field(default=None, max_length=64)
    error_code: str | None = Field(default=None, max_length=64)
    created_at: str
    updated_at: str

    @field_validator("id")
    @classmethod
    def _id_shape(cls, value: str) -> str:
        if EXPERIMENT_ID_PATTERN.fullmatch(value) is None:
            _log_schema_rejection("id", "model_lab_id_shape")
            raise ValueError("id must be mtlab_ + 16 hex")
        return value

    @field_validator("display_name")
    @classmethod
    def _display_name(cls, value: str) -> str:
        if DISPLAY_NAME_PATTERN.fullmatch(value) is None:
            _log_schema_rejection("display_name", "model_lab_display_name")
            raise ValueError("display_name must match Lab name pattern")
        return value

    @field_validator("architecture_digest_prefix", "train_digest_prefix")
    @classmethod
    def _digest_prefix(cls, value: str) -> str:
        text = value.strip().lower()
        if HEX_PREFIX_PATTERN.fullmatch(text) is None:
            _log_schema_rejection("digest_prefix", "model_lab_digest")
            raise ValueError("digest prefix must be lowercase hex")
        return text[:16]

    @field_validator("registry_model_id")
    @classmethod
    def _registry_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if REGISTRY_MODEL_ID_PATTERN.fullmatch(value) is None:
            _log_schema_rejection("registry_model_id", "model_lab_registry_id")
            raise ValueError("registry_model_id must be lab:{id}")
        return value


class ModelLabTrainKnobsV1(_Strict):
    """Bounded train knobs editable in the Lab wizard."""

    steps: int = Field(default=8, ge=1, le=512)
    batch_size: int = Field(default=2, ge=1, le=64)
    lr: float = Field(default=3e-4, gt=0.0, le=1.0)
    device: Literal["cpu", "cuda", "rocm", "mps"] = "cpu"
    log_every: int = Field(default=1, ge=1, le=1024)
    checkpoint_interval: int = Field(default=0, ge=0, le=512)


class ModelLabCreateV1(_Strict):
    """``model.lab.create.v1`` POST body."""

    schema_version: Literal["model.lab.create.v1"] = CREATE_SCHEMA
    display_name: str
    dataset_version_id: str = Field(..., min_length=1, max_length=128)
    tokenizer_preset: TokenizerPresetId = "core"
    architecture_preset: ArchitecturePresetId = "tiny_lab"
    seed: int = Field(default=42, ge=0)
    train: ModelLabTrainKnobsV1 = Field(default_factory=ModelLabTrainKnobsV1)
    eval_enabled: bool = False
    listening_enabled: bool = False
    n_layers: int | None = Field(default=None, ge=1, le=8)
    d_model: int | None = Field(default=None, ge=16, le=256)
    max_seq_len: int | None = Field(default=None, ge=8, le=512)

    @field_validator("display_name")
    @classmethod
    def _display_name(cls, value: str) -> str:
        text = value.strip()
        if not text or DISPLAY_NAME_PATTERN.fullmatch(text) is None:
            _log_schema_rejection("display_name", "model_lab_display_name")
            raise ValueError("display_name must be non-empty and match Lab name pattern")
        return text

    @field_validator("steps", check_fields=False)
    @classmethod
    def _unused(cls, value: Any) -> Any:
        return value

    @model_validator(mode="after")
    def _hard_caps(self) -> ModelLabCreateV1:
        if self.train.steps > 512:
            _log_schema_rejection("train.steps", "model_lab_steps_refused")
            raise ValueError("train.steps exceeds Lab hard max")
        if self.train.batch_size > 64:
            _log_schema_rejection("train.batch_size", "model_lab_batch_refused")
            raise ValueError("train.batch_size exceeds Lab hard max")
        return self


class ModelLabMetricRowV1(_Strict):
    step: int = Field(..., ge=0)
    loss: float | None = None
    val_loss: float | None = None
    token_accuracy: float | None = None
    lr: float | None = None
    tokens_per_sec: float | None = None
    mem_mb: float | None = None


class ModelLabMetricsV1(_Strict):
    """``model.lab.metrics.v1`` projection of metrics.jsonl + summary."""

    schema_version: Literal["model.lab.metrics.v1"] = METRICS_SCHEMA
    experiment_id: str
    rows: list[ModelLabMetricRowV1] = Field(default_factory=list)
    final_loss: float | None = None
    final_val_loss: float | None = None
    musical_quality_claim: Literal[False] = False

    @field_validator("experiment_id")
    @classmethod
    def _id_shape(cls, value: str) -> str:
        if EXPERIMENT_ID_PATTERN.fullmatch(value) is None:
            _log_schema_rejection("experiment_id", "model_lab_id_shape")
            raise ValueError("experiment_id must be mtlab_ + 16 hex")
        return value


class ModelLabCompareSideV1(_Strict):
    experiment_id: str
    seed: int = Field(..., ge=0)
    final_loss: float | None = None
    final_val_loss: float | None = None
    tokenizer_version: str | None = None
    architecture_digest_prefix: str | None = None
    evaluation_version: str | None = None
    listening_set_digest: str | None = None


class ModelLabCompareV1(_Strict):
    """``model.lab.compare.v1`` multi-run compare — no quality winner."""

    schema_version: Literal["model.lab.compare.v1"] = COMPARE_SCHEMA
    experiment_ids: list[str] = Field(..., min_length=2, max_length=8)
    sides: list[ModelLabCompareSideV1] = Field(..., min_length=2, max_length=8)
    tokenizer_equal: bool = False
    architecture_equal: bool = False
    metric_deltas: dict[str, float] = Field(default_factory=dict)
    musical_quality_claim: Literal[False] = False

    @field_validator("experiment_ids")
    @classmethod
    def _ids(cls, value: list[str]) -> list[str]:
        for item in value:
            if EXPERIMENT_ID_PATTERN.fullmatch(item) is None:
                _log_schema_rejection("experiment_ids", "model_lab_id_shape")
                raise ValueError("experiment_ids must be mtlab_ + 16 hex")
        return value


class ModelLabRegisterV1(_Strict):
    """``model.lab.register.v1`` promote one checkpoint into ``/ai/models``."""

    schema_version: Literal["model.lab.register.v1"] = REGISTER_SCHEMA
    checkpoint_step: int = Field(..., ge=0)
    display_name: str | None = Field(default=None, max_length=64)

    @field_validator("display_name")
    @classmethod
    def _optional_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not text or DISPLAY_NAME_PATTERN.fullmatch(text) is None:
            _log_schema_rejection("display_name", "model_lab_display_name")
            raise ValueError("display_name must match Lab name pattern")
        return text


class ModelLabStatusV1(_Strict):
    """``model.lab.status.v1`` — always returned by GET /model-lab/status."""

    schema_version: Literal["model.lab.status.v1"] = STATUS_SCHEMA
    enabled: bool
    fake: bool
    max_steps: int
    max_batch: int
    max_concurrent: int
    max_compare: int
    allow_accelerator: bool
    retention: int
    root_basename: str


class ModelLabDatasetCatalogEntryV1(_Strict):
    """Read-only catalog row for one dataset version under ``DATASET_ROOT``."""

    dataset_name: str
    dataset_version_id: str
    item_count: int = Field(default=0, ge=0)
    example_count: int = Field(default=0, ge=0)
    train_eligible_items: int = Field(default=0, ge=0)
    has_rights_index: bool = False
    lab_fixture: bool = False


class ModelLabTokenizerPresetV1(_Strict):
    preset_id: TokenizerPresetId
    profile: TokenizerPresetId
    emit_harmony: bool
    tokenizer_version: str


class ModelLabArchitecturePresetV1(_Strict):
    preset_id: ArchitecturePresetId
    n_layers: int
    d_model: int
    n_heads: int
    d_ff: int
    max_seq_len: int
    dropout: float


class ModelLabPresetsV1(_Strict):
    tokenizer: list[ModelLabTokenizerPresetV1]
    architecture: list[ModelLabArchitecturePresetV1]
