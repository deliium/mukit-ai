"""Public documents for the external adaptive engine session.

``adaptive.engine.session.v1`` is a projection of playback. It is not
``adaptive.score.v2`` or ``composition.v5`` and it does not carry note events.
"""

from __future__ import annotations

import logging
import re
from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, ValidationError, field_validator, model_validator
from pydantic import BaseModel

from app.adaptive_musical_context_schemas import (
    AdaptiveContextMappingV1,
    parse_adaptive_context_mapping,
)
from app.adaptive_score_schemas import _ID_RE, _reject_bool, log_adaptive_schema_failure

logger = logging.getLogger(__name__)

ADAPTIVE_ENGINE_SESSION_SCHEMA: Literal["adaptive.engine.session.v1"] = "adaptive.engine.session.v1"
ADAPTIVE_ENGINE_ACK_SCHEMA: Literal["adaptive.engine.ack.v1"] = "adaptive.engine.ack.v1"
ADAPTIVE_ENGINE_ERROR_SCHEMA: Literal["adaptive.engine.error.v1"] = "adaptive.engine.error.v1"

_SESSION_ID_RE = re.compile(r"^aeng_[0-9a-f]{8}$")
_REQUEST_ID_RE = re.compile(r"^req_[a-z0-9]{1,32}$")
_CUE_NAME_RE = re.compile(r"^[a-z0-9_-]+$")

ENGINE_ERROR_CODES: frozenset[str] = frozenset(
    {
        "engine_unauthorized",
        "engine_payload_invalid",
        "engine_session_missing",
        "engine_session_exists",
        "engine_session_limit",
        "engine_score_missing",
        "engine_score_invalid",
        "engine_revision_conflict",
        "engine_context_unmapped",
        "engine_context_busy",
        "engine_context_invalid",
        "engine_stinger_unwired",
        "engine_event_unknown",
        "engine_rate_limited",
        "engine_superseded",
        "engine_playback_stopped",
        "engine_clock_not_owned",
        "adaptive_engine_continuous_disabled",
    }
)

ENGINE_DETAIL_CODES: frozenset[str] = frozenset({"engine_state_rejected"})
ENGINE_WARNING_CODES: frozenset[str] = frozenset(
    {
        "engine_context_busy",
        "engine_queue_full",
        "engine_clock_not_owned",
    }
)

ENGINE_ERROR_MESSAGES: dict[str, str] = {
    "engine_unauthorized": "The engine request was not authorized.",
    "engine_payload_invalid": "The engine request body is invalid.",
    "engine_session_missing": "No engine session exists for that id.",
    "engine_session_exists": "An engine session already exists for this score.",
    "engine_session_limit": "The process engine session limit is reached.",
    "engine_score_missing": "The project or adaptive score was not found.",
    "engine_score_invalid": "The adaptive score cannot start an engine session.",
    "engine_revision_conflict": "The adaptive score revision does not match the engine session.",
    "engine_context_unmapped": "This engine session has no musical-context mapping.",
    "engine_context_busy": "A musical-context session is already running.",
    "engine_context_invalid": "The context sample or mapping is invalid.",
    "engine_stinger_unwired": "No transition from the current state fires that stinger.",
    "engine_event_unknown": "The cue name is not on this engine session.",
    "engine_rate_limited": "The engine command rate is exceeded.",
    "engine_superseded": "A newer engine command replaced this request.",
    "engine_playback_stopped": "Adaptive playback is not running for this session.",
    "engine_clock_not_owned": "This engine session does not advance the playback clock.",
    "adaptive_engine_continuous_disabled": "Adaptive engine continuous maintain is disabled.",
}

ENGINE_ERROR_STATUS: dict[str, int] = {
    "engine_unauthorized": 401,
    "engine_payload_invalid": 422,
    "engine_session_missing": 404,
    "engine_session_exists": 409,
    "engine_session_limit": 429,
    "engine_score_missing": 404,
    "engine_score_invalid": 422,
    "engine_revision_conflict": 409,
    "engine_context_unmapped": 409,
    "engine_context_busy": 409,
    "engine_context_invalid": 422,
    "engine_stinger_unwired": 422,
    "engine_event_unknown": 422,
    "engine_rate_limited": 429,
    "engine_superseded": 409,
    "engine_playback_stopped": 409,
    "engine_clock_not_owned": 409,
    "adaptive_engine_continuous_disabled": 422,
}

EngineClockOwner = Literal["engine", "existing"]
EngineTransport = Literal["playing", "held", "stopped"]
EnginePhase = Literal["bed", "phrase", "stinger"]
EngineDisposition = Literal["committed", "queued", "rejected"]
EngineAckDisposition = Literal["committed", "queued", "rejected", "finished"]
EngineAckKind = Literal["state", "intensity", "stinger", "cue", "context"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AdaptiveEngineError(Exception):
    """Public engine failure. ``details`` never carries a score field path."""

    def __init__(
        self,
        code: str,
        *,
        session_id: str | None = None,
        retry_after_ms: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        if code not in ENGINE_ERROR_CODES:
            raise ValueError("unknown engine error code")
        message = ENGINE_ERROR_MESSAGES[code]
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = ENGINE_ERROR_STATUS[code]
        self.session_id = session_id
        self.retry_after_ms = retry_after_ms
        self.details = details or {}

    def to_model(self) -> AdaptiveEngineErrorV1:
        return AdaptiveEngineErrorV1(
            code=self.code,  # type: ignore[arg-type]
            message=self.message,
            session_id=self.session_id,
            retry_after_ms=self.retry_after_ms,
            details=self.details or None,
        )


class AdaptiveEngineErrorV1(_Strict):
    schema_version: Literal["adaptive.engine.error.v1"] = ADAPTIVE_ENGINE_ERROR_SCHEMA
    code: Literal[
        "engine_unauthorized",
        "engine_payload_invalid",
        "engine_session_missing",
        "engine_session_exists",
        "engine_session_limit",
        "engine_score_missing",
        "engine_score_invalid",
        "engine_revision_conflict",
        "engine_context_unmapped",
        "engine_context_busy",
        "engine_context_invalid",
        "engine_stinger_unwired",
        "engine_event_unknown",
        "engine_rate_limited",
        "engine_superseded",
        "engine_playback_stopped",
        "engine_clock_not_owned",
        "adaptive_engine_continuous_disabled",
    ]
    message: str = Field(min_length=1, max_length=200)
    session_id: str | None = None
    retry_after_ms: int | None = Field(default=None, ge=0)
    details: dict[str, Any] | None = None

    @field_validator("retry_after_ms", mode="before")
    @classmethod
    def retry_int(cls, value: object) -> object:
        if value is not None:
            _reject_bool(value, model=cls.__name__, field="retry_after_ms")
        return value


class AdaptiveEngineTelemetryV1(_Strict):
    command_count: int = Field(default=0, ge=0)
    rejected_count: int = Field(default=0, ge=0)
    coalesced_count: int = Field(default=0, ge=0)
    dropped_context_count: int = Field(default=0, ge=0)
    ack_count: int = Field(default=0, ge=0)

    @field_validator(
        "command_count",
        "rejected_count",
        "coalesced_count",
        "dropped_context_count",
        "ack_count",
        mode="before",
    )
    @classmethod
    def counters_int(cls, value: object) -> object:
        _reject_bool(value, model=cls.__name__, field="command_count")
        return value


class AdaptiveEngineSessionV1(_Strict):
    schema_version: Literal["adaptive.engine.session.v1"] = ADAPTIVE_ENGINE_SESSION_SCHEMA
    session_id: str = Field(pattern=r"^aeng_[0-9a-f]{8}$")
    project_id: str
    score_id: str
    clock_owner: EngineClockOwner
    transport: EngineTransport
    runtime_state_id: str
    bar: int = Field(ge=1)
    beat: int = Field(ge=1)
    intensity: float = Field(ge=0, le=1)
    phase: EnginePhase
    active_stinger_id: str | None = None
    pending_transition_id: str | None = None
    pending_to_state_id: str | None = None
    warnings: list[str] = Field(default_factory=list, max_length=8)
    document_revision: int = Field(ge=1)
    context_attached: bool
    continuous_enabled: bool = False
    engine_continuous_enabled: bool = False
    telemetry: AdaptiveEngineTelemetryV1

    @field_validator("bar", "beat", "document_revision", mode="before")
    @classmethod
    def clock_int(cls, value: object) -> object:
        _reject_bool(value, model=cls.__name__, field="bar")
        return value

    @field_validator("intensity", mode="before")
    @classmethod
    def intensity_number(cls, value: object) -> object:
        _reject_bool(value, model=cls.__name__, field="intensity")
        return value

    @field_validator("warnings")
    @classmethod
    def public_warnings(cls, value: list[str]) -> list[str]:
        for item in value:
            if item not in ENGINE_WARNING_CODES:
                log_adaptive_schema_failure(cls.__name__, "warnings", "engine_payload_invalid")
                raise ValueError("warnings must be public engine codes")
        return value

    @model_validator(mode="after")
    def pending_pair(self) -> AdaptiveEngineSessionV1:
        if (self.pending_transition_id is None) != (self.pending_to_state_id is None):
            log_adaptive_schema_failure(self.__class__.__name__, "pending_transition_id", "engine_payload_invalid")
            raise ValueError("pending transition fields are paired")
        return self


class AdaptiveEngineCueV1(_Strict):
    name: str = Field(min_length=1, max_length=64)
    to_state_id: str
    transition_id: str | None = None

    @field_validator("name")
    @classmethod
    def cue_name(cls, value: str) -> str:
        if not _CUE_NAME_RE.fullmatch(value):
            log_adaptive_schema_failure(cls.__name__, "name", "engine_payload_invalid")
            raise ValueError("cue name must be a short token")
        return value

    @field_validator("to_state_id")
    @classmethod
    def state_token(cls, value: str) -> str:
        if not _ID_RE.fullmatch(value):
            log_adaptive_schema_failure(cls.__name__, "to_state_id", "engine_payload_invalid")
            raise ValueError("to_state_id must be a score state id")
        return value

    @field_validator("transition_id")
    @classmethod
    def transition_token(cls, value: str | None) -> str | None:
        if value is not None and not _ID_RE.fullmatch(value):
            log_adaptive_schema_failure(cls.__name__, "transition_id", "engine_payload_invalid")
            raise ValueError("transition_id must be a score transition id")
        return value


class AdaptiveEngineStartV1(_Strict):
    project_id: str = Field(min_length=1, max_length=64)
    score_id: str = Field(min_length=1, max_length=64)
    expected_document_revision: int = Field(ge=1)
    mapping: AdaptiveContextMappingV1 | None = None
    cues: list[AdaptiveEngineCueV1] = Field(default_factory=list, max_length=32)

    @field_validator("expected_document_revision", mode="before")
    @classmethod
    def revision_int(cls, value: object) -> object:
        _reject_bool(value, model=cls.__name__, field="expected_document_revision")
        return value

    @field_validator("mapping", mode="before")
    @classmethod
    def parse_mapping(cls, value: object) -> object:
        if value is None:
            return None
        try:
            return parse_adaptive_context_mapping(value)
        except Exception as exc:
            log_adaptive_schema_failure(cls.__name__, "mapping", "engine_context_invalid")
            raise ValueError("mapping is invalid") from exc

    @model_validator(mode="after")
    def unique_cues(self) -> AdaptiveEngineStartV1:
        names = [cue.name for cue in self.cues]
        if len(names) != len(set(names)):
            log_adaptive_schema_failure(self.__class__.__name__, "cues", "engine_payload_invalid")
            raise ValueError("cue names must be unique")
        return self


def _request_id(value: str | None, *, model: str) -> str | None:
    if value is None:
        return None
    if not _REQUEST_ID_RE.fullmatch(value):
        log_adaptive_schema_failure(model, "request_id", "engine_payload_invalid")
        raise ValueError("request_id must match the engine request pattern")
    return value


class AdaptiveEngineStateCommandV1(_Strict):
    to_state_id: str
    transition_id: str | None = None
    request_id: str | None = None

    @field_validator("to_state_id")
    @classmethod
    def state_token(cls, value: str) -> str:
        if not _ID_RE.fullmatch(value):
            log_adaptive_schema_failure(cls.__name__, "to_state_id", "engine_payload_invalid")
            raise ValueError("to_state_id must be a score state id")
        return value

    @field_validator("transition_id")
    @classmethod
    def transition_token(cls, value: str | None) -> str | None:
        if value is not None and not _ID_RE.fullmatch(value):
            log_adaptive_schema_failure(cls.__name__, "transition_id", "engine_payload_invalid")
            raise ValueError("transition_id must be a score transition id")
        return value

    @field_validator("request_id")
    @classmethod
    def request_token(cls, value: str | None) -> str | None:
        return _request_id(value, model=cls.__name__)


class AdaptiveEngineIntensityCommandV1(_Strict):
    intensity: float = Field(ge=0, le=1)
    request_id: str | None = None

    @field_validator("intensity", mode="before")
    @classmethod
    def intensity_number(cls, value: object) -> object:
        _reject_bool(value, model=cls.__name__, field="intensity")
        return value

    @field_validator("request_id")
    @classmethod
    def request_token(cls, value: str | None) -> str | None:
        return _request_id(value, model=cls.__name__)


class AdaptiveEngineStingerEventV1(_Strict):
    kind: Literal["stinger"]
    stinger_id: str
    transition_id: str | None = None
    request_id: str | None = None

    @field_validator("stinger_id")
    @classmethod
    def stinger_token(cls, value: str) -> str:
        if not _ID_RE.fullmatch(value):
            log_adaptive_schema_failure(cls.__name__, "stinger_id", "engine_payload_invalid")
            raise ValueError("stinger_id must be a short token")
        return value

    @field_validator("transition_id")
    @classmethod
    def transition_token(cls, value: str | None) -> str | None:
        if value is not None and not _ID_RE.fullmatch(value):
            log_adaptive_schema_failure(cls.__name__, "transition_id", "engine_payload_invalid")
            raise ValueError("transition_id must be a score transition id")
        return value

    @field_validator("request_id")
    @classmethod
    def request_token(cls, value: str | None) -> str | None:
        return _request_id(value, model=cls.__name__)


class AdaptiveEngineCueEventV1(_Strict):
    kind: Literal["cue"]
    name: str = Field(min_length=1, max_length=64)
    request_id: str | None = None

    @field_validator("name")
    @classmethod
    def cue_name(cls, value: str) -> str:
        if not _CUE_NAME_RE.fullmatch(value):
            log_adaptive_schema_failure(cls.__name__, "name", "engine_payload_invalid")
            raise ValueError("cue name must be a short token")
        return value

    @field_validator("request_id")
    @classmethod
    def request_token(cls, value: str | None) -> str | None:
        return _request_id(value, model=cls.__name__)


AdaptiveEngineEventV1 = Annotated[
    AdaptiveEngineStingerEventV1 | AdaptiveEngineCueEventV1,
    Field(discriminator="kind"),
]


class AdaptiveEngineCommandResultV1(_Strict):
    session: AdaptiveEngineSessionV1
    disposition: EngineDisposition
    request_id: str | None = None
    coalesced: bool
    applied: bool
    retry_after_ms: int | None = Field(default=None, ge=0)

    @field_validator("retry_after_ms", mode="before")
    @classmethod
    def retry_int(cls, value: object) -> object:
        if value is not None:
            _reject_bool(value, model=cls.__name__, field="retry_after_ms")
        return value


class AdaptiveEngineAckV1(_Strict):
    schema_version: Literal["adaptive.engine.ack.v1"] = ADAPTIVE_ENGINE_ACK_SCHEMA
    session_id: str = Field(pattern=r"^aeng_[0-9a-f]{8}$")
    request_id: str | None = None
    kind: EngineAckKind
    disposition: EngineAckDisposition
    runtime_state_id: str
    detail_code: str | None = None

    @field_validator("detail_code")
    @classmethod
    def detail_token(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value not in ENGINE_ERROR_CODES and value not in ENGINE_DETAIL_CODES:
            log_adaptive_schema_failure(cls.__name__, "detail_code", "engine_payload_invalid")
            raise ValueError("detail_code must be a public engine code")
        return value


def engine_error_detail(exc: AdaptiveEngineError) -> dict[str, Any]:
    """HTTP ``detail`` object. No stack and no secret."""
    return exc.to_model().model_dump(mode="json")


def validation_field(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "body"
    loc = errors[0].get("loc") or ()
    if not loc:
        return "body"
    return str(loc[-1])
