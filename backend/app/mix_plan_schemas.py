"""Strict mix-plan DTOs — non-destructive parameter documents (never PCM).

``mix.plan.v1`` describes gain, pan, EQ, compression, send, filter, and
bounded automation over neural stems. Applying a plan writes a new mix
revision. Source stem WAVs are never rewritten. Distinct from the ephemeral
Tone.js mixer and from ``composition.v2``.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

logger = logging.getLogger(__name__)

MIX_PLAN_SCHEMA_VERSION: Literal["mix.plan.v1"] = "mix.plan.v1"
MIX_INTENT_SCHEMA_VERSION: Literal["mix.intent.v1"] = "mix.intent.v1"
MIX_MASTER_TARGET_SCHEMA_VERSION: Literal["mix.master_target.v1"] = "mix.master_target.v1"

MIX_PLAN_OP_MAX = 64
MIX_PLAN_AUTOMATION_MAX = 32
MIX_PLAN_ID_MAX = 80
MIX_PLAN_MESSAGE_MAX = 400
MIX_PLAN_FINGERPRINT_MAX = 128

MixPlanOpType = Literal[
    "gain",
    "pan",
    "eq",
    "compressor",
    "send",
    "filter",
    "automation",
]
MixPlanBus = Literal["stem", "master"]
MixPlanReasonKind = Literal["intent", "observation", "master_target"]
MixPlanMasterTargetId = Literal["dynamic", "streaming", "cinematic", "demo"]
MixPlanIntentCode = Literal[
    "reduce_dominance",
    "more_space",
    "wider_climax",
    "reduce_lf_masking",
    "observation_suggestion",
]
MixPlanDspBackend = Literal["stdlib", "numpy_scipy", "fake"]
MixPlanPolarity = Literal["decrease", "increase", "widen", "carve"]

STEM_ROLES = frozenset({"piano", "bass", "strings", "drums", "vocals", "other"})

MIX_PLAN_INTENT_UNRECOGNIZED = "mix_plan_intent_unrecognized"
MIX_PLAN_ROLE_UNAVAILABLE = "mix_plan_role_unavailable"
MIX_PLAN_STEM_SET_INCOMPLETE = "mix_plan_stem_set_incomplete"
MIX_PLAN_NOT_FOUND = "mix_plan_not_found"
MIX_PLAN_PREVIEW_STALE = "mix_plan_preview_stale"
MIX_PLAN_STEM_CHANGED = "mix_plan_stem_changed"
MIX_PLAN_UNDO_EMPTY = "mix_plan_undo_empty"
MIX_PLAN_QUOTA_EXCEEDED = "mix_plan_quota_exceeded"
MIX_PLAN_INPUT_TOO_LARGE = "mix_plan_input_too_large"
MIX_PLAN_TIMEOUT = "mix_plan_timeout"
MIX_PLAN_INVALID_REQUEST = "mix_plan_invalid_request"
MIX_PLAN_INTERNAL_ERROR = "mix_plan_internal_error"
MIX_PLAN_INTENT_LLM_UNAVAILABLE = "intent_llm_unavailable"
MIX_PLAN_MASTER_TARGET_UNVERIFIED = "master_target_unverified"
MIX_PLAN_SECTION_UNSPECIFIED = "section_unspecified"
MIX_PLAN_ANALYSIS_REPORT_ABSENT = "analysis_report_absent"
MIX_PLAN_STEREO_UNSIGNED = "stereo_imbalance_unsigned"
MIX_PLAN_REVERB_ID: Literal["algorithmic_room"] = "algorithmic_room"

MIX_PLAN_ERROR_HTTP: dict[str, int] = {
    MIX_PLAN_INTENT_UNRECOGNIZED: 422,
    MIX_PLAN_ROLE_UNAVAILABLE: 422,
    MIX_PLAN_STEM_SET_INCOMPLETE: 422,
    MIX_PLAN_NOT_FOUND: 404,
    MIX_PLAN_PREVIEW_STALE: 409,
    MIX_PLAN_STEM_CHANGED: 409,
    MIX_PLAN_UNDO_EMPTY: 409,
    MIX_PLAN_QUOTA_EXCEEDED: 422,
    MIX_PLAN_INPUT_TOO_LARGE: 422,
    MIX_PLAN_TIMEOUT: 504,
    MIX_PLAN_INVALID_REQUEST: 422,
    MIX_PLAN_INTERNAL_ERROR: 500,
    MIX_PLAN_INTENT_LLM_UNAVAILABLE: 200,
    MIX_PLAN_MASTER_TARGET_UNVERIFIED: 200,
    MIX_PLAN_SECTION_UNSPECIFIED: 200,
    MIX_PLAN_ANALYSIS_REPORT_ABSENT: 200,
}


class MixPlanError(Exception):
    """Domain error for mix plans — sanitized client detail only."""

    code: str = MIX_PLAN_INTERNAL_ERROR
    http_status: int = 500

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        http_status: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code
        if http_status is not None:
            self.http_status = http_status
        else:
            self.http_status = MIX_PLAN_ERROR_HTTP.get(self.code, self.http_status)
        self.details = details or {}
        logger.warning(
            "Mix plan domain error",
            extra={"code": self.code, "detail_keys": sorted(self.details.keys())},
        )


def map_mix_plan_error_to_http(exc: MixPlanError) -> tuple[int, dict[str, Any]]:
    detail: dict[str, Any] = {
        "code": exc.code,
        "message": str(exc)[:240],
    }
    if exc.details:
        safe = {
            key: value
            for key, value in exc.details.items()
            if isinstance(value, (str, int, float, bool)) or value is None
        }
        if safe:
            detail["details"] = safe
    return exc.http_status, detail


class MixPlanAutomationPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    t_seconds: float = Field(ge=0.0, le=3600.0)
    value: float = Field(ge=-24.0, le=24.0)


class MixPlanOp(BaseModel):
    """One bounded mix operation. Numbers are inspectable parameters, not PCM."""

    model_config = ConfigDict(extra="forbid")

    op_id: str = Field(min_length=1, max_length=MIX_PLAN_ID_MAX)
    type: MixPlanOpType
    bus: MixPlanBus = "stem"
    stem_ids: list[str] = Field(default_factory=list, max_length=32)
    stem_roles: list[str] = Field(default_factory=list, max_length=16)
    source_track_ids: list[str] = Field(default_factory=list, max_length=32)
    unit: str = Field(min_length=1, max_length=16)
    before: float
    after: float
    freq_hz: float | None = Field(default=None, ge=0.0, le=20000.0)
    q: float | None = Field(default=None, ge=0.1, le=12.0)
    automation: list[MixPlanAutomationPoint] = Field(
        default_factory=list, max_length=MIX_PLAN_AUTOMATION_MAX
    )
    start_seconds: float | None = Field(default=None, ge=0.0)
    end_seconds: float | None = Field(default=None, ge=0.0)
    start_bar: int | None = Field(default=None, ge=1)
    end_bar: int | None = Field(default=None, ge=1)
    reason_kind: MixPlanReasonKind
    reason_code: str = Field(min_length=1, max_length=80)
    reverb_id: Literal["algorithmic_room"] | None = None

    @field_validator("stem_ids", "stem_roles", "source_track_ids", mode="before")
    @classmethod
    def _trim_ids(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("expected list")
        return [str(item).strip()[:MIX_PLAN_ID_MAX] for item in value if str(item).strip()]

    @model_validator(mode="after")
    def _bounds(self) -> MixPlanOp:
        low, high = _after_bounds(self.type)
        if self.after < low or self.after > high or self.before < low or self.before > high:
            logger.debug(
                "Mix plan op rejected by bounds",
                extra={"op_type": self.type, "unit": self.unit},
            )
            raise ValueError(f"{self.type} value out of range")
        if self.type == "automation" and not self.automation:
            raise ValueError("automation op requires points")
        if self.type == "send" and self.reverb_id is None:
            self.reverb_id = "algorithmic_room"
        return self


def _after_bounds(op_type: str) -> tuple[float, float]:
    if op_type in {"gain", "eq", "automation"}:
        return -24.0, 12.0
    if op_type == "pan":
        return -1.0, 1.0
    if op_type == "send":
        return 0.0, 1.0
    if op_type == "compressor":
        return 1.0, 8.0
    if op_type == "filter":
        return 0.0, 20000.0
    return -24.0, 24.0


class MixPlanChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op_id: str
    type: MixPlanOpType
    target: str = Field(max_length=160)
    unit: str
    before: float
    after: float
    reason: str = Field(max_length=120)


class MixPlanWarning(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(max_length=80)
    message: str = Field(max_length=MIX_PLAN_MESSAGE_MAX)


class MixPlanStemPin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stem_id: str = Field(min_length=1, max_length=MIX_PLAN_ID_MAX)
    stem_role: str = Field(min_length=1, max_length=32)
    sha256_prefix: str = Field(min_length=8, max_length=64)
    byte_size: int = Field(ge=0)


class MixIntentV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["mix.intent.v1"] = MIX_INTENT_SCHEMA_VERSION
    intent_code: MixPlanIntentCode
    roles: list[str] = Field(default_factory=list, max_length=8)
    polarity: MixPlanPolarity


class MixMasterTargetV1(BaseModel):
    """Production preset/goal. ``guarantee`` is always false."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["mix.master_target.v1"] = MIX_MASTER_TARGET_SCHEMA_VERSION
    target_id: MixPlanMasterTargetId
    label: str
    goal: str = Field(max_length=MIX_PLAN_MESSAGE_MAX)
    loudness_goal_lufs: float | None = None
    guarantee: Literal[False] = False


class MixPlanObservationRef(BaseModel):
    """Slim observation pointer. ``suggested_action`` prose is not accepted."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=80)
    stem_ids: list[str] = Field(default_factory=list, max_length=32)
    stem_roles: list[str] = Field(default_factory=list, max_length=16)
    source_track_ids: list[str] = Field(default_factory=list, max_length=32)
    freq_hz_low: float | None = Field(default=None, ge=0.0, le=96000.0)
    freq_hz_high: float | None = Field(default=None, ge=0.0, le=96000.0)
    start_bar: int | None = Field(default=None, ge=1)
    end_bar: int | None = Field(default=None, ge=1)
    start_seconds: float | None = Field(default=None, ge=0.0)
    end_seconds: float | None = Field(default=None, ge=0.0)
    measurement_value: float | None = Field(default=None, ge=-120.0, le=120.0)


class MixPlanV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["mix.plan.v1"] = MIX_PLAN_SCHEMA_VERSION
    stem_set_id: str = Field(min_length=1, max_length=MIX_PLAN_ID_MAX)
    project_id: str = Field(min_length=1, max_length=64)
    master_target: MixPlanMasterTargetId
    guarantee: Literal[False] = False
    intent_code: MixPlanIntentCode
    dsp_backend: MixPlanDspBackend = "fake"
    source_stem_set_fingerprint: str = Field(default="", max_length=MIX_PLAN_FINGERPRINT_MAX)
    stem_pins: list[MixPlanStemPin] = Field(default_factory=list, max_length=32)
    ops: list[MixPlanOp] = Field(default_factory=list, max_length=MIX_PLAN_OP_MAX)
    changes: list[MixPlanChange] = Field(default_factory=list, max_length=MIX_PLAN_OP_MAX)
    warnings: list[MixPlanWarning] = Field(default_factory=list, max_length=16)
    mutates_stems: Literal[False] = False
    preview_truncated: bool = False


class MixPlanMasterOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_id: MixPlanMasterTargetId
    guarantee: Literal[False] = False
    sample_peak_dbfs: float | None = None
    lufs_approx: float | None = None
    loudness_goal_lufs: float | None = None


class MixPlanPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(min_length=1, max_length=64)
    stem_set_id: str = Field(min_length=1, max_length=MIX_PLAN_ID_MAX)
    phrase: str | None = Field(default=None, max_length=400)
    report_id: str | None = Field(default=None, max_length=MIX_PLAN_ID_MAX)
    observations: list[MixPlanObservationRef] | None = Field(default=None, max_length=32)
    observation_codes: list[str] | None = Field(default=None, max_length=16)
    master_target: MixPlanMasterTargetId = "dynamic"
    include_audio_preview: bool = False
    include_llm_intent: bool = False

    @field_validator("phrase", mode="before")
    @classmethod
    def _blank_phrase(cls, value: Any) -> Any:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class MixPlanPreviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preview_id: str
    digest: str
    plan: MixPlanV1
    changes: list[MixPlanChange]
    warnings: list[MixPlanWarning]
    preview_truncated: bool = False
    dry_audio: bool = False
    processed_audio: bool = False
    master_outcome: MixPlanMasterOutcome


class MixPlanApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preview_id: str = Field(min_length=1, max_length=MIX_PLAN_ID_MAX)
    digest: str = Field(min_length=16, max_length=128)
    plan: MixPlanV1


class MixPlanRevisionMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    project_id: str
    stem_set_id: str
    parent_revision_id: str | None = None
    master_target: MixPlanMasterTargetId
    dsp_backend: MixPlanDspBackend
    byte_size: int
    sha256_prefix: str
    created_at: str
    is_head: bool
    source_stem_set_fingerprint: str
    plan_relpath: str
    mix_relpath: str


class MixPlanApplyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: MixPlanRevisionMeta
    head_revision_id: str
    master_outcome: MixPlanMasterOutcome


class MixPlanRevisionListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[MixPlanRevisionMeta]


class MixPlanRevisionGetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: MixPlanRevisionMeta
    plan: MixPlanV1


class MixPlanUndoResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    head_revision_id: str | None
    revision: MixPlanRevisionMeta | None = None
