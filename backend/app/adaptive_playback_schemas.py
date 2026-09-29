"""Session snapshot for adaptive playback. Not stored on the score."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, ValidationError, field_validator

from app.adaptive_score_schemas import (
    ADAPTIVE_SCORE_ERROR_CODES,
    AdaptiveRealizationKind,
    AdaptiveScoreError,
    AdaptiveTransitionQuantization,
    AdaptiveTransitionRuntimeV1,
    AdaptiveTransitionScheduleWarningV1,
    _ID_RE,
    _Strict,
    _reject_bool,
    log_adaptive_schema_failure,
)

ADAPTIVE_PLAYBACK_RUNTIME_SCHEMA: Literal["adaptive.playback.runtime.v1"] = (
    "adaptive.playback.runtime.v1"
)

PLAYBACK_WARNING_CODES: frozenset[str] = frozenset(
    {
        "playback_queue_full",
        "advance_ignored",
        "observe_ignored",
        "observation_behind",
        "observation_clamped",
        "document_revision_conflict",
        "phrase_span_unchecked",
    }
)

AdaptivePlaybackMode = Literal["simulation", "live"]
AdaptivePlaybackTransport = Literal["playing", "held", "stopped"]
AdaptivePlaybackPhase = Literal["bed", "phrase", "stinger"]
AdaptivePlaybackLastEvent = Literal[
    "started",
    "advanced",
    "loop_wrapped",
    "state_committed",
    "phrase_started",
    "phrase_finished",
    "stinger_started",
    "stinger_finished",
    "intensity_changed",
    "request_rejected",
    "request_queued",
    "held",
    "stopped",
]


class AdaptivePlaybackLoopV1(_Strict):
    enabled: bool = False
    start_bar: int | None = Field(default=None, ge=1)
    end_bar: int | None = Field(default=None, ge=1)
    start_tick: int = Field(default=0, ge=0)
    end_tick: int = Field(default=0, ge=0)

    @field_validator("start_tick", "end_tick", mode="before")
    @classmethod
    def tick_int(cls, value: object) -> object:
        _reject_bool(value, model=cls.__name__, field="start_tick")
        return value


class AdaptivePlaybackPendingV1(_Strict):
    transition_id: str
    to_state_id: str
    boundary_tick: int = Field(ge=0)
    quantization: AdaptiveTransitionQuantization
    realization_kind: AdaptiveRealizationKind

    @field_validator("boundary_tick", mode="before")
    @classmethod
    def boundary_int(cls, value: object) -> object:
        _reject_bool(value, model=cls.__name__, field="boundary_tick")
        return value


class AdaptivePlaybackQueueItemV1(_Strict):
    to_state_id: str
    transition_id: str | None = None


class AdaptivePlaybackPhraseV1(_Strict):
    start_tick: int = Field(ge=0)
    end_tick: int = Field(ge=0)

    @field_validator("start_tick", "end_tick", mode="before")
    @classmethod
    def tick_int(cls, value: object) -> object:
        _reject_bool(value, model=cls.__name__, field="start_tick")
        return value


class AdaptivePlaybackTrackGainV1(_Strict):
    track_id: str
    target_gain: int = Field(ge=0, le=1)
    fade_end_tick: int = Field(ge=0)

    @field_validator("target_gain", "fade_end_tick", mode="before")
    @classmethod
    def gain_int(cls, value: object) -> object:
        _reject_bool(value, model=cls.__name__, field="target_gain")
        return value


class AdaptivePlaybackInstructionsV1(_Strict):
    seek_tick: int | None = Field(default=None, ge=0)
    loop: AdaptivePlaybackLoopV1
    track_gains: list[AdaptivePlaybackTrackGainV1] = Field(default_factory=list, max_length=64)
    stop: bool = False
    fade_start_tick: int | None = Field(default=None, ge=0)

    @field_validator("seek_tick", "fade_start_tick", mode="before")
    @classmethod
    def optional_tick(cls, value: object) -> object:
        if value is not None:
            _reject_bool(value, model=cls.__name__, field="seek_tick")
        return value


class AdaptivePlaybackTelemetryV1(_Strict):
    step_count: int = Field(ge=0)
    rejected_request_count: int = Field(ge=0)
    last_event: AdaptivePlaybackLastEvent

    @field_validator("step_count", "rejected_request_count", mode="before")
    @classmethod
    def counter_int(cls, value: object) -> object:
        _reject_bool(value, model=cls.__name__, field="step_count")
        return value


class AdaptivePlaybackRuntimeV1(_Strict):
    schema_version: Literal["adaptive.playback.runtime.v1"] = ADAPTIVE_PLAYBACK_RUNTIME_SCHEMA
    playback_id: str = Field(pattern=r"^pbr_[0-9a-f]{8}$")
    mode: AdaptivePlaybackMode
    transport: AdaptivePlaybackTransport
    runtime_state_id: str
    position_tick: int = Field(ge=0)
    bar: int = Field(ge=1)
    beat: int = Field(ge=1)
    intensity: float = Field(ge=0, le=1)
    loop: AdaptivePlaybackLoopV1
    active_layer_ids: list[str] = Field(default_factory=list, max_length=32)
    pending_transition: AdaptivePlaybackPendingV1 | None = None
    queue: list[AdaptivePlaybackQueueItemV1] = Field(default_factory=list, max_length=4)
    horizon_end_tick: int = Field(ge=0)
    arm_boundary: bool = False
    phase: AdaptivePlaybackPhase = "bed"
    phrase: AdaptivePlaybackPhraseV1 | None = None
    active_stinger_id: str | None = None
    instructions: AdaptivePlaybackInstructionsV1
    warnings: list[AdaptiveTransitionScheduleWarningV1] = Field(default_factory=list, max_length=8)
    telemetry: AdaptivePlaybackTelemetryV1
    document_revision: int = Field(ge=1)

    @field_validator("position_tick", "bar", "beat", "horizon_end_tick", mode="before")
    @classmethod
    def clock_int(cls, value: object) -> object:
        _reject_bool(value, model=cls.__name__, field="position_tick")
        return value

    @field_validator("intensity", mode="before")
    @classmethod
    def intensity_number(cls, value: object) -> object:
        _reject_bool(value, model=cls.__name__, field="intensity")
        return value


class AdaptivePlaybackStartRequest(_Strict):
    expected_document_revision: int = Field(ge=1)
    mode: AdaptivePlaybackMode

    @field_validator("expected_document_revision", mode="before")
    @classmethod
    def revision_int(cls, value: object) -> object:
        _reject_bool(value, model=cls.__name__, field="expected_document_revision")
        return value


class AdaptivePlaybackAdvanceCommand(_Strict):
    op: Literal["advance"]
    advance_ticks: int = Field(ge=0)

    @field_validator("advance_ticks", mode="before")
    @classmethod
    def ticks_int(cls, value: object) -> object:
        _reject_bool(value, model=cls.__name__, field="advance_ticks")
        return value


class AdaptivePlaybackObserveCommand(_Strict):
    op: Literal["observe"]
    position_tick: int = Field(ge=0)

    @field_validator("position_tick", mode="before")
    @classmethod
    def ticks_int(cls, value: object) -> object:
        _reject_bool(value, model=cls.__name__, field="position_tick")
        return value


class AdaptivePlaybackRequestStateCommand(_Strict):
    op: Literal["request_state"]
    to_state_id: str
    transition_id: str | None = None

    @field_validator("to_state_id")
    @classmethod
    def state_token(cls, value: str) -> str:
        if not _ID_RE.fullmatch(value):
            log_adaptive_schema_failure(cls.__name__, "to_state_id", "adaptive_score_invalid")
            raise ValueError("state id must be a short token")
        return value

    @field_validator("transition_id")
    @classmethod
    def transition_token(cls, value: str | None) -> str | None:
        if value is not None and not _ID_RE.fullmatch(value):
            log_adaptive_schema_failure(cls.__name__, "transition_id", "adaptive_score_invalid")
            raise ValueError("transition id must be a short token")
        return value


class AdaptivePlaybackSetIntensityCommand(_Strict):
    op: Literal["set_intensity"]
    intensity: float = Field(ge=0, le=1)

    @field_validator("intensity", mode="before")
    @classmethod
    def intensity_number(cls, value: object) -> object:
        _reject_bool(value, model=cls.__name__, field="intensity")
        return value


class AdaptivePlaybackSetFlagsCommand(_Strict):
    op: Literal["set_flags"]
    flags: dict[str, bool] = Field(default_factory=dict)

    @field_validator("flags")
    @classmethod
    def flag_map(cls, value: dict[str, bool]) -> dict[str, bool]:
        AdaptiveTransitionRuntimeV1(flags=value)
        return value


class AdaptivePlaybackStopCommand(_Strict):
    op: Literal["stop"]


AdaptivePlaybackCommand = Annotated[
    AdaptivePlaybackAdvanceCommand
    | AdaptivePlaybackObserveCommand
    | AdaptivePlaybackRequestStateCommand
    | AdaptivePlaybackSetIntensityCommand
    | AdaptivePlaybackSetFlagsCommand
    | AdaptivePlaybackStopCommand,
    Field(discriminator="op"),
]


def _validation_field(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "body"
    loc = errors[0].get("loc") or ()
    if not loc:
        return "body"
    return str(loc[-1])


def _parse(data: dict, model: type, label: str):
    if not isinstance(data, dict):
        log_adaptive_schema_failure(label, "body", "adaptive_score_invalid")
        raise AdaptiveScoreError(
            "adaptive_score_invalid",
            f"{label} must be an object",
            http_status=422,
        )
    try:
        if model is AdaptivePlaybackCommand:
            return TypeAdapter(AdaptivePlaybackCommand).validate_python(data)
        return model.model_validate(data)
    except ValidationError as exc:
        field = _validation_field(exc)
        log_adaptive_schema_failure(label, field, "adaptive_score_invalid")
        raise AdaptiveScoreError(
            "adaptive_score_invalid",
            ADAPTIVE_SCORE_ERROR_CODES["adaptive_score_invalid"],
            http_status=422,
            details={"field": field},
        ) from exc


def parse_adaptive_playback_start(data: dict) -> AdaptivePlaybackStartRequest:
    """Validate a start body. Does not log the payload."""
    parsed = _parse(data, AdaptivePlaybackStartRequest, "AdaptivePlaybackStartRequest")
    assert isinstance(parsed, AdaptivePlaybackStartRequest)
    return parsed


def parse_adaptive_playback_command(data: dict) -> AdaptivePlaybackCommand:
    """Validate one playback command. Does not log the payload."""
    parsed = _parse(data, AdaptivePlaybackCommand, "AdaptivePlaybackCommand")
    return parsed
