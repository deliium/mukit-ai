"""Non-playable documents for a personal symbolic composer adapter.

These models never carry note events. A payload that contains note material
is ``embedded_note_material``. This module does not import FastAPI, torch,
stores, video, film, or agent modules.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.dataset.schemas import DatasetProvenance

logger = logging.getLogger(__name__)

TRAINING_MANIFEST_SCHEMA = "personal.training_manifest.v1"
DATASET_SNAPSHOT_SCHEMA = "personal.dataset_snapshot.v1"
ADAPTER_CONFIG_SCHEMA = "personal.adapter_config.v1"
TRAINING_JOB_SCHEMA = "personal.training_job.v1"
EVAL_SCHEMA = "personal.eval.v1"
FAKE_ADAPTER_SCHEMA = "personal.adapter.fake.v1"
ADAPTER_SCHEMA = "personal.adapter.v1"
TRAINING_STEP_SCHEMA = "personal.training_step.v1"

ADAPTER_ID_PATTERN = re.compile(r"^pcomp_[0-9a-f]{16}$")
DISPLAY_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,63}$")
HEX64_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SNAPSHOT_FILE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,80}\.json$")
PROJECT_ID_PATTERN = re.compile(r"^.{1,64}$")

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

BASE_MODEL_IDS = frozenset(
    {"fake:symbolic-tiny", "music_transformer.tiny.v1", "music_transformer"}
)
JOB_STATUSES = frozenset({"running", "stopped", "complete", "failed", "deleted"})
TARGET_MODULES = ("qkv", "out_proj")

PersonalEngine = Literal["fake", "torch"]
PersonalJobStatus = Literal["running", "stopped", "complete", "failed", "deleted"]


class PersonalComposerError(Exception):
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
        "Personal composer schema rejected",
        extra={"field_name": field_name, "code": code},
    )


def scan_embedded_note_keys(payload: Any) -> list[str]:
    """Return forbidden key names found anywhere in ``payload``."""
    found: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_text = str(key)
            if key_text in FORBIDDEN_NOTE_KEYS:
                found.append(key_text)
            found.extend(scan_embedded_note_keys(value))
    elif isinstance(payload, list):
        for item in payload:
            found.extend(scan_embedded_note_keys(item))
    return found


def reject_embedded_note_material(payload: Any) -> None:
    """Raise when a request body embeds note events."""
    found = scan_embedded_note_keys(payload)
    if not found:
        return
    field_name = found[0]
    _log_schema_rejection(field_name, "embedded_note_material")
    raise PersonalComposerError(
        "embedded_note_material",
        "Personal composer documents cannot embed note events.",
        http_status=422,
        details={"field_name": field_name},
    )


def map_personal_composer_error_to_http(exc: PersonalComposerError) -> tuple[int, dict[str, Any]]:
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
    def _reject_note_material(cls, value: Any) -> Any:
        if isinstance(value, dict):
            found = scan_embedded_note_keys(value)
            if found:
                _log_schema_rejection(found[0], "embedded_note_material")
                raise ValueError("embedded_note_material")
        return value


class PersonalAdapterConfigV1(_Strict):
    """LoRA shape. Method is ``lora`` only, and the base stays frozen."""

    schema_version: Literal["personal.adapter_config.v1"] = ADAPTER_CONFIG_SCHEMA
    method: Literal["lora"] = "lora"
    rank: int = Field(default=4, ge=1, le=16)
    alpha: int = Field(default=8, ge=1, le=64)
    dropout: float = Field(default=0, ge=0, le=0.2)
    target_modules: list[Literal["qkv", "out_proj"]] = Field(
        default_factory=lambda: ["qkv", "out_proj"]
    )
    freeze_base: Literal[True] = True
    max_steps: int = Field(default=1, ge=1, le=50)

    @field_validator("target_modules")
    @classmethod
    def _freeze_targets(cls, value: list[str]) -> list[str]:
        if tuple(value) != TARGET_MODULES:
            _log_schema_rejection("target_modules", "personal_adapter_targets")
            raise ValueError("target_modules must be qkv then out_proj")
        return ["qkv", "out_proj"]

    @field_validator("rank")
    @classmethod
    def _log_rank(cls, value: int) -> int:
        return value


class PersonalDatasetSnapshotItemV1(_Strict):
    project_id: str = Field(..., min_length=1, max_length=64)
    composition_fingerprint: str
    relative_path: str

    @field_validator("composition_fingerprint")
    @classmethod
    def _fingerprint(cls, value: str) -> str:
        if HEX64_PATTERN.fullmatch(value) is None:
            _log_schema_rejection("composition_fingerprint", "personal_snapshot_fingerprint")
            raise ValueError("composition_fingerprint must be 64 hex characters")
        return value

    @field_validator("relative_path")
    @classmethod
    def _relative_path(cls, value: str) -> str:
        if SNAPSHOT_FILE_PATTERN.fullmatch(value) is None:
            _log_schema_rejection("relative_path", "personal_snapshot_path")
            raise ValueError("relative_path must be a snapshot json file name")
        return value


class PersonalDatasetSnapshotV1(_Strict):
    schema_version: Literal["personal.dataset_snapshot.v1"] = DATASET_SNAPSHOT_SCHEMA
    adapter_id: str
    snapshot_version: str
    items: list[PersonalDatasetSnapshotItemV1] = Field(..., min_length=1, max_length=8)

    @field_validator("adapter_id")
    @classmethod
    def _adapter_id(cls, value: str) -> str:
        if ADAPTER_ID_PATTERN.fullmatch(value) is None:
            _log_schema_rejection("adapter_id", "personal_adapter_id")
            raise ValueError("adapter_id must be pcomp_ plus 16 hex characters")
        return value

    @field_validator("snapshot_version")
    @classmethod
    def _version(cls, value: str) -> str:
        if HEX64_PATTERN.fullmatch(value) is None:
            _log_schema_rejection("snapshot_version", "personal_snapshot_version")
            raise ValueError("snapshot_version must be 64 hex characters")
        return value


class PersonalTrainingManifestV1(_Strict):
    schema_version: Literal["personal.training_manifest.v1"] = TRAINING_MANIFEST_SCHEMA
    adapter_id: str
    display_name: str
    registry_model_id: str
    project_ids: list[str] = Field(..., min_length=1, max_length=8)
    rights: dict[str, DatasetProvenance]
    snapshot_version: str
    base_model_id: str
    base_checkpoint_basename: str | None = None
    adapter_config: PersonalAdapterConfigV1
    engine: PersonalEngine
    created_at: str

    @field_validator("adapter_id")
    @classmethod
    def _adapter_id(cls, value: str) -> str:
        if ADAPTER_ID_PATTERN.fullmatch(value) is None:
            _log_schema_rejection("adapter_id", "personal_adapter_id")
            raise ValueError("adapter_id must be pcomp_ plus 16 hex characters")
        return value

    @field_validator("display_name")
    @classmethod
    def _display_name(cls, value: str) -> str:
        if DISPLAY_NAME_PATTERN.fullmatch(value) is None:
            _log_schema_rejection("display_name", "personal_name_invalid")
            raise ValueError("personal_name_invalid")
        return value

    @field_validator("project_ids")
    @classmethod
    def _project_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            _log_schema_rejection("project_ids", "personal_projects_required")
            raise ValueError("project_ids must be unique")
        for project_id in value:
            if PROJECT_ID_PATTERN.fullmatch(project_id) is None:
                _log_schema_rejection("project_ids", "personal_projects_required")
                raise ValueError("project id length must be 1..64")
        return value

    @field_validator("snapshot_version")
    @classmethod
    def _snapshot_version(cls, value: str) -> str:
        if HEX64_PATTERN.fullmatch(value) is None:
            _log_schema_rejection("snapshot_version", "personal_snapshot_version")
            raise ValueError("snapshot_version must be 64 hex characters")
        return value

    @field_validator("base_model_id")
    @classmethod
    def _base_model_id(cls, value: str) -> str:
        if value not in BASE_MODEL_IDS:
            _log_schema_rejection("base_model_id", "personal_base_model")
            raise ValueError("base_model_id is not a personal composer base")
        return value

    @field_validator("base_checkpoint_basename")
    @classmethod
    def _basename(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value or "/" in value or "\\" in value or value in {".", ".."}:
            _log_schema_rejection("base_checkpoint_basename", "personal_base_checkpoint")
            raise ValueError("base_checkpoint_basename must be a file name")
        return value

    @model_validator(mode="after")
    def _cross_check(self) -> PersonalTrainingManifestV1:
        expected = f"personal:{self.adapter_id}"
        if self.registry_model_id != expected:
            _log_schema_rejection("registry_model_id", "personal_registry_id")
            raise ValueError("registry_model_id must be personal: plus the adapter id")
        if set(self.rights) != set(self.project_ids):
            _log_schema_rejection("rights", "personal_rights_refused")
            raise ValueError("rights must name each selected project once")
        return self


class PersonalTrainingJobV1(_Strict):
    schema_version: Literal["personal.training_job.v1"] = TRAINING_JOB_SCHEMA
    adapter_id: str
    display_name: str
    status: PersonalJobStatus
    registry_model_id: str
    engine: PersonalEngine
    base_model_id: str
    snapshot_version: str
    step: int = Field(..., ge=0, le=50)
    max_steps: int = Field(..., ge=1, le=50)
    error_code: str | None = None
    manifest: PersonalTrainingManifestV1
    created_at: str
    updated_at: str
    owner_actor_id: str | None = None

    @field_validator("adapter_id")
    @classmethod
    def _adapter_id(cls, value: str) -> str:
        if ADAPTER_ID_PATTERN.fullmatch(value) is None:
            _log_schema_rejection("adapter_id", "personal_adapter_id")
            raise ValueError("adapter_id must be pcomp_ plus 16 hex characters")
        return value

    @field_validator("snapshot_version")
    @classmethod
    def _snapshot_version(cls, value: str) -> str:
        if HEX64_PATTERN.fullmatch(value) is None:
            _log_schema_rejection("snapshot_version", "personal_snapshot_version")
            raise ValueError("snapshot_version must be 64 hex characters")
        return value


class PersonalEvalV1(_Strict):
    schema_version: Literal["personal.eval.v1"] = EVAL_SCHEMA
    adapter_id: str
    engine: PersonalEngine
    step: int = Field(..., ge=0, le=50)
    loss: float | None = None
    token_accuracy: float | None = None

    @field_validator("adapter_id")
    @classmethod
    def _adapter_id(cls, value: str) -> str:
        if ADAPTER_ID_PATTERN.fullmatch(value) is None:
            _log_schema_rejection("adapter_id", "personal_adapter_id")
            raise ValueError("adapter_id must be pcomp_ plus 16 hex characters")
        return value


class PersonalFakeAdapterV1(_Strict):
    schema_version: Literal["personal.adapter.fake.v1"] = FAKE_ADAPTER_SCHEMA
    adapter_id: str
    base_model_id: Literal["fake:symbolic-tiny"] = "fake:symbolic-tiny"
    snapshot_version: str
    adapter_config: PersonalAdapterConfigV1
    step: int = Field(..., ge=0, le=50)


def canonical_personal_json(payload: dict[str, Any]) -> str:
    """Stable JSON for snapshot versioning. Not a composition document."""
    import json

    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
