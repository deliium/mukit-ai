"""Strict adaptive runtime continuation documents.

``adaptive.runtime.continuation.v1`` is a session snapshot. It is not stored
on the score and it has no note events. ``adaptive.runtime.context.v1`` is
long-term musical memory inside that snapshot. ``adaptive.runtime.buffer.v1``
holds the session note buffer in process memory. None of these documents is
``composition.v5`` or ``adaptive.score.v2``.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from app.adaptive_runtime_continuation_settings import (
    load_adaptive_runtime_continuation_settings,
)
from app.adaptive_runtime_music_state_schemas import AdaptiveRuntimeMusicStateV1
from app.adaptive_score_schemas import (
    ADAPTIVE_SCORE_ERROR_CODES,
    AdaptiveScoreError,
    AdaptiveTransitionScheduleWarningV1,
    _ID_RE,
    _reject_bool,
    log_adaptive_schema_failure,
)
from app.composition_schemas import CompositionV2NoteEvent

logger = logging.getLogger(__name__)

CONTINUATION_SCHEMA: Literal["adaptive.runtime.continuation.v1"] = (
    "adaptive.runtime.continuation.v1"
)
CONTEXT_SCHEMA: Literal["adaptive.runtime.context.v1"] = "adaptive.runtime.context.v1"
BUFFER_SCHEMA: Literal["adaptive.runtime.buffer.v1"] = "adaptive.runtime.buffer.v1"

CONTINUATION_SCHEMA_MESSAGES: dict[str, str] = {
    CONTINUATION_SCHEMA: "Body must be adaptive.runtime.continuation.v1.",
    CONTEXT_SCHEMA: "Body must be adaptive.runtime.context.v1.",
    BUFFER_SCHEMA: "Body must be adaptive.runtime.buffer.v1.",
}

CONTINUATION_NOT_RUNNING = "continuation_not_running"

ContinuationMode = Literal[
    "continuation",
    "variation",
    "intensity_adaptation",
    "harmonic_continuation",
    "state_specific",
]
ContinuationFallbackKind = Literal["reuse_loop", "motif_variation", "accompaniment"]
ContinuationSource = Literal["none", "fallback", "model"]
ContinuationJobStatus = Literal["idle", "pending", "applied", "discarded", "failed"]

_CONTINUATION_ID_RE = re.compile(r"^arcn_[0-9a-f]{8}$")
_JOB_ID_RE = re.compile(r"^arcj_[0-9a-f]{8}$")

_SNAPSHOT_FORBIDDEN_KEYS = frozenset(
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

_CONTEXT_FORBIDDEN_KEYS = frozenset(
    {
        "events",
        "notes",
        "pitch",
        "pitches",
        "midi_events",
    }
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def continuation_not_running_error() -> AdaptiveScoreError:
    """HTTP 404 when maintain or buffer is asked before a session exists."""
    return AdaptiveScoreError(
        CONTINUATION_NOT_RUNNING,
        "Adaptive continuation is not running for this score.",
        http_status=404,
    )


class AdaptiveRuntimeContextV1(_Strict):
    """Long-term musical memory. No event arrays and no pitch lists."""

    schema_version: Literal["adaptive.runtime.context.v1"] = CONTEXT_SCHEMA
    theme_ids: list[str] = Field(default_factory=list, max_length=16)
    harmony_tail: str | None = Field(default=None, max_length=32)
    harmony_chord_count: int = Field(default=0, ge=0, le=32)
    recent_start_bar: int | None = Field(default=None, ge=1)
    recent_end_bar: int | None = Field(default=None, ge=1)
    repetition_count: int = Field(default=0, ge=0, le=8)
    energy: list[float] = Field(default_factory=list, max_length=8)

    @field_validator(
        "harmony_chord_count",
        "recent_start_bar",
        "recent_end_bar",
        "repetition_count",
        mode="before",
    )
    @classmethod
    def reject_bool_counts(cls, value: object) -> object:
        if value is None:
            return value
        _reject_bool(value, model=cls.__name__, field="repetition_count")
        return value

    @field_validator("theme_ids")
    @classmethod
    def unique_theme_ids(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in value:
            token = item.strip()
            if not token or len(token) > 120:
                log_adaptive_schema_failure(cls.__name__, "theme_ids", "adaptive_score_invalid")
                raise ValueError("theme_ids entries must be 1..120 characters")
            cleaned.append(token)
        if len(cleaned) != len(set(cleaned)):
            log_adaptive_schema_failure(cls.__name__, "theme_ids", "adaptive_score_invalid")
            raise ValueError("theme_ids must be unique")
        return cleaned

    @field_validator("harmony_tail")
    @classmethod
    def harmony_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            log_adaptive_schema_failure(cls.__name__, "harmony_tail", "adaptive_score_invalid")
            raise ValueError("harmony_tail must not be empty")
        return cleaned

    @field_validator("energy", mode="before")
    @classmethod
    def energy_numbers(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        if len(value) > 8:
            log_adaptive_schema_failure(cls.__name__, "energy", "adaptive_score_too_large")
            raise ValueError("cap_exceeded")
        for item in value:
            _reject_bool(item, model=cls.__name__, field="energy")
            if isinstance(item, (int, float)) and not isinstance(item, bool):
                if item < 0 or item > 1:
                    log_adaptive_schema_failure(cls.__name__, "energy", "adaptive_score_invalid")
                    raise ValueError("energy values must be between 0 and 1")
        return value

    @model_validator(mode="after")
    def recent_window(self) -> AdaptiveRuntimeContextV1:
        start = self.recent_start_bar
        end = self.recent_end_bar
        if (start is None) != (end is None):
            log_adaptive_schema_failure(self.__class__.__name__, "recent_end_bar", "adaptive_score_invalid")
            raise ValueError("recent bars must both be set or both be null")
        if start is not None and end is not None and end < start:
            log_adaptive_schema_failure(self.__class__.__name__, "recent_end_bar", "adaptive_score_invalid")
            raise ValueError("recent_end_bar must be at least recent_start_bar")
        return self


class AdaptiveRuntimeContinuationTelemetryV1(_Strict):
    arm_count: int = Field(default=0, ge=0)
    model_apply_count: int = Field(default=0, ge=0)
    late_discard_count: int = Field(default=0, ge=0)
    failure_count: int = Field(default=0, ge=0)
    fallback_count: int = Field(default=0, ge=0)

    @field_validator(
        "arm_count",
        "model_apply_count",
        "late_discard_count",
        "failure_count",
        "fallback_count",
        mode="before",
    )
    @classmethod
    def reject_bool_counters(cls, value: object) -> object:
        _reject_bool(value, model=cls.__name__, field="arm_count")
        return value


class AdaptiveRuntimeContinuationV1(_Strict):
    """Session snapshot. Note events belong on the buffer, never here."""

    schema_version: Literal["adaptive.runtime.continuation.v1"] = CONTINUATION_SCHEMA
    continuation_id: str
    job_id: str | None = None
    mode: ContinuationMode
    anchor_bar: int = Field(ge=1)
    reserved_start_bar: int = Field(ge=1)
    reserved_end_bar: int = Field(ge=1)
    target_start_bar: int | None = Field(default=None, ge=1)
    target_end_bar: int | None = Field(default=None, ge=1)
    deadline_tick: int = Field(ge=0)
    fallback_kind: ContinuationFallbackKind
    source: ContinuationSource
    job_status: ContinuationJobStatus
    applicable: bool
    audible: bool
    runtime_state_id: str
    intensity: float = Field(ge=0, le=1)
    context: AdaptiveRuntimeContextV1
    music_state: AdaptiveRuntimeMusicStateV1 | None = None
    continuous: bool = False
    warnings: list[AdaptiveTransitionScheduleWarningV1] = Field(
        default_factory=list,
        max_length=8,
    )
    telemetry: AdaptiveRuntimeContinuationTelemetryV1 = Field(
        default_factory=AdaptiveRuntimeContinuationTelemetryV1
    )
    document_revision: int = Field(ge=1)

    @field_validator(
        "anchor_bar",
        "reserved_start_bar",
        "reserved_end_bar",
        "target_start_bar",
        "target_end_bar",
        "deadline_tick",
        "document_revision",
        mode="before",
    )
    @classmethod
    def reject_bool_bars(cls, value: object) -> object:
        if value is None:
            return value
        _reject_bool(value, model=cls.__name__, field="anchor_bar")
        return value

    @field_validator("intensity", mode="before")
    @classmethod
    def reject_bool_intensity(cls, value: object) -> object:
        _reject_bool(value, model=cls.__name__, field="intensity")
        return value

    @field_validator("continuation_id")
    @classmethod
    def continuation_token(cls, value: str) -> str:
        if not _CONTINUATION_ID_RE.fullmatch(value):
            log_adaptive_schema_failure(cls.__name__, "continuation_id", "adaptive_score_invalid")
            raise ValueError("continuation_id must match ^arcn_[0-9a-f]{8}$")
        return value

    @field_validator("job_id")
    @classmethod
    def job_token(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _JOB_ID_RE.fullmatch(value):
            log_adaptive_schema_failure(cls.__name__, "job_id", "adaptive_score_invalid")
            raise ValueError("job_id must match ^arcj_[0-9a-f]{8}$")
        return value

    @field_validator("runtime_state_id")
    @classmethod
    def state_token(cls, value: str) -> str:
        if not _ID_RE.fullmatch(value):
            log_adaptive_schema_failure(cls.__name__, "runtime_state_id", "adaptive_score_invalid")
            raise ValueError("runtime_state_id must be a short token")
        return value

    @model_validator(mode="after")
    def windows(self) -> AdaptiveRuntimeContinuationV1:
        if self.reserved_end_bar < self.reserved_start_bar:
            log_adaptive_schema_failure(
                self.__class__.__name__,
                "reserved_end_bar",
                "adaptive_score_invalid",
            )
            raise ValueError("reserved_end_bar must be at least reserved_start_bar")
        start = self.target_start_bar
        end = self.target_end_bar
        if (start is None) != (end is None):
            log_adaptive_schema_failure(
                self.__class__.__name__,
                "target_end_bar",
                "adaptive_score_invalid",
            )
            raise ValueError("target bars must both be set or both be null")
        if start is not None and end is not None and end < start:
            log_adaptive_schema_failure(
                self.__class__.__name__,
                "target_end_bar",
                "adaptive_score_invalid",
            )
            raise ValueError("target_end_bar must be at least target_start_bar")
        return self


class AdaptiveRuntimeBufferHarmonyV1(_Strict):
    """Copied chord label. Not a pitch list."""

    start_tick: int = Field(ge=0)
    duration_ticks: int = Field(gt=0)
    chord: str = Field(min_length=1, max_length=32)

    @field_validator("start_tick", "duration_ticks", mode="before")
    @classmethod
    def reject_bool_span(cls, value: object) -> object:
        _reject_bool(value, model=cls.__name__, field="start_tick")
        return value

    @field_validator("chord")
    @classmethod
    def chord_label(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            log_adaptive_schema_failure(cls.__name__, "chord", "adaptive_score_invalid")
            raise ValueError("chord must not be empty")
        return cleaned


class AdaptiveRuntimeBufferV1(_Strict):
    """Session note buffer. The continuation snapshot does not embed this document."""

    schema_version: Literal["adaptive.runtime.buffer.v1"] = BUFFER_SCHEMA
    continuation_id: str
    job_id: str
    source: ContinuationSource
    fallback_kind: ContinuationFallbackKind
    events: list[CompositionV2NoteEvent] = Field(default_factory=list)
    harmony: list[AdaptiveRuntimeBufferHarmonyV1] = Field(default_factory=list, max_length=8)

    @field_validator("continuation_id")
    @classmethod
    def continuation_token(cls, value: str) -> str:
        if not _CONTINUATION_ID_RE.fullmatch(value):
            log_adaptive_schema_failure(cls.__name__, "continuation_id", "adaptive_score_invalid")
            raise ValueError("continuation_id must match ^arcn_[0-9a-f]{8}$")
        return value

    @field_validator("job_id")
    @classmethod
    def job_token(cls, value: str) -> str:
        if not _JOB_ID_RE.fullmatch(value):
            log_adaptive_schema_failure(cls.__name__, "job_id", "adaptive_score_invalid")
            raise ValueError("job_id must match ^arcj_[0-9a-f]{8}$")
        return value

    @field_validator("events", mode="before")
    @classmethod
    def bound_events(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        cap = load_adaptive_runtime_continuation_settings().max_buffer_events
        if len(value) > cap:
            log_adaptive_schema_failure(cls.__name__, "events", "adaptive_score_too_large")
            raise ValueError("cap_exceeded")
        for item in value:
            if isinstance(item, dict):
                for key in ("start_tick", "duration_ticks", "velocity"):
                    if key in item:
                        _reject_bool(item[key], model=cls.__name__, field=key)
        return value


class AdaptiveRuntimeContinuationStartRequest(_Strict):
    """Start body. There is no schema_version field on this request."""

    expected_document_revision: int = Field(ge=1)
    mode: ContinuationMode
    continuous: bool = False

    @field_validator("expected_document_revision", mode="before")
    @classmethod
    def reject_bool_revision(cls, value: object) -> object:
        _reject_bool(value, model=cls.__name__, field="expected_document_revision")
        return value

    @field_validator("continuous", mode="before")
    @classmethod
    def reject_non_bool_continuous(cls, value: object) -> object:
        if isinstance(value, bool) or value is None:
            return False if value is None else value
        # Reject truthy strings / ints — latch must be an explicit boolean.
        log_adaptive_schema_failure(cls.__name__, "continuous", "adaptive_score_invalid")
        raise ValueError("continuous must be a boolean")


def _validation_field(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "body"
    loc = errors[0].get("loc") or ()
    for part in reversed(loc):
        if isinstance(part, str):
            return part
    return "body"


def _message_token(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return ""
    return str(errors[0].get("msg") or "")


def _raise_validation(label: str, exc: ValidationError) -> None:
    token = _message_token(exc)
    field = _validation_field(exc)
    if "cap_exceeded" in token or "adaptive_score_too_large" in token:
        code = "adaptive_score_too_large"
        message = ADAPTIVE_SCORE_ERROR_CODES["adaptive_score_too_large"]
    else:
        code = "adaptive_score_invalid"
        message = ADAPTIVE_SCORE_ERROR_CODES["adaptive_score_invalid"]
    log_adaptive_schema_failure(label, field, code)
    raise AdaptiveScoreError(
        code,
        message,
        http_status=422,
        details={"field": field},
    ) from exc


def _require_object(data: object, label: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        log_adaptive_schema_failure(label, "body", "adaptive_score_invalid")
        raise AdaptiveScoreError(
            "adaptive_score_invalid",
            f"{label} must be an object",
            http_status=422,
            details={"field": "body"},
        )
    return data


def _require_schema(data: dict[str, Any], expected: str, label: str) -> None:
    version = data.get("schema_version")
    if version != expected:
        log_adaptive_schema_failure(label, "schema_version", "unsupported_schema_version")
        raise AdaptiveScoreError(
            "unsupported_schema_version",
            CONTINUATION_SCHEMA_MESSAGES[expected],
            http_status=422,
            details={"field": "schema_version"},
        )


def _reject_forbidden_keys(
    data: dict[str, Any],
    keys: frozenset[str],
    label: str,
) -> None:
    for key in data:
        if str(key) in keys:
            log_adaptive_schema_failure(label, str(key), "embedded_note_material")
            raise AdaptiveScoreError(
                "embedded_note_material",
                ADAPTIVE_SCORE_ERROR_CODES["embedded_note_material"],
                http_status=422,
                details={"field": str(key)},
            )


def parse_adaptive_runtime_context(data: object) -> AdaptiveRuntimeContextV1:
    """Validate memory. Does not log harmony labels or theme material."""
    body = _require_object(data, "AdaptiveRuntimeContextV1")
    _require_schema(body, CONTEXT_SCHEMA, "AdaptiveRuntimeContextV1")
    _reject_forbidden_keys(body, _CONTEXT_FORBIDDEN_KEYS, "AdaptiveRuntimeContextV1")
    try:
        return AdaptiveRuntimeContextV1.model_validate(body)
    except ValidationError as exc:
        _raise_validation("AdaptiveRuntimeContextV1", exc)
    raise AssertionError("adaptive runtime context")


def parse_adaptive_runtime_continuation(data: object) -> AdaptiveRuntimeContinuationV1:
    """Validate a snapshot. An event list is forbidden on this document."""
    body = _require_object(data, "AdaptiveRuntimeContinuationV1")
    _require_schema(body, CONTINUATION_SCHEMA, "AdaptiveRuntimeContinuationV1")
    _reject_forbidden_keys(body, _SNAPSHOT_FORBIDDEN_KEYS, "AdaptiveRuntimeContinuationV1")
    context = body.get("context")
    if isinstance(context, dict):
        _reject_forbidden_keys(context, _CONTEXT_FORBIDDEN_KEYS, "AdaptiveRuntimeContextV1")
    music_state = body.get("music_state")
    if music_state is not None:
        if not isinstance(music_state, dict):
            log_adaptive_schema_failure(
                "AdaptiveRuntimeContinuationV1",
                "music_state",
                "adaptive_score_invalid",
            )
            raise AdaptiveScoreError(
                "adaptive_score_invalid",
                ADAPTIVE_SCORE_ERROR_CODES["adaptive_score_invalid"],
                http_status=422,
                details={"field": "music_state"},
            )
        from app.adaptive_runtime_music_state_schemas import (
            parse_adaptive_runtime_music_state,
        )

        # Re-validate nested MusicState so forbidden note keys surface as
        # embedded_note_material rather than a generic invalid body.
        body = {**body, "music_state": parse_adaptive_runtime_music_state(music_state)}
    try:
        return AdaptiveRuntimeContinuationV1.model_validate(body)
    except ValidationError as exc:
        _raise_validation("AdaptiveRuntimeContinuationV1", exc)
    raise AssertionError("adaptive runtime continuation")


def parse_adaptive_runtime_buffer(data: object) -> AdaptiveRuntimeBufferV1:
    """Validate the session buffer. Does not log events or pitches."""
    body = _require_object(data, "AdaptiveRuntimeBufferV1")
    _require_schema(body, BUFFER_SCHEMA, "AdaptiveRuntimeBufferV1")
    try:
        return AdaptiveRuntimeBufferV1.model_validate(body)
    except ValidationError as exc:
        _raise_validation("AdaptiveRuntimeBufferV1", exc)
    raise AssertionError("adaptive runtime buffer")


def parse_adaptive_runtime_continuation_start(
    data: object,
) -> AdaptiveRuntimeContinuationStartRequest:
    """Validate start. A future schema_version field is 422 and is not logged as a body."""
    body = _require_object(data, "AdaptiveRuntimeContinuationStartRequest")
    if "schema_version" in body:
        log_adaptive_schema_failure(
            "AdaptiveRuntimeContinuationStartRequest",
            "schema_version",
            "unsupported_schema_version",
        )
        raise AdaptiveScoreError(
            "unsupported_schema_version",
            "Continuation start does not accept schema_version.",
            http_status=422,
            details={"field": "schema_version"},
        )
    try:
        return AdaptiveRuntimeContinuationStartRequest.model_validate(body)
    except ValidationError as exc:
        _raise_validation("AdaptiveRuntimeContinuationStartRequest", exc)
    raise AssertionError("adaptive runtime continuation start")
