"""Strict co-performance DTOs — ephemeral session / predict / chunk only.

Terminology (locked):
- live.session.v1 — FE session snapshot echo; never persisted in PROJECT_DB
- live.accompaniment.predict.request.v1 — bounded cold-path HTTP body
- live.accompaniment.chunk.v1 — ephemeral events until explicit Commit

Forbidden in predict requests: composition, tracks, events[], motifs note
payloads, raw MIDI dumps. Harmony context is metadata only — never invents
playable score notes. See plan v4-co-performance-realtime-engine.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.live_performance_settings import (
    LivePerformanceSettings,
    load_live_performance_settings,
)

logger = logging.getLogger(__name__)

LIVE_SESSION_SCHEMA: Literal["live.session.v1"] = "live.session.v1"
LIVE_PREDICT_REQUEST_SCHEMA: Literal["live.accompaniment.predict.request.v1"] = (
    "live.accompaniment.predict.request.v1"
)
LIVE_ACCOMPANIMENT_CHUNK_SCHEMA: Literal["live.accompaniment.chunk.v1"] = (
    "live.accompaniment.chunk.v1"
)

LiveSessionPhase = Literal[
    "idle",
    "arming",
    "running",
    "degraded",
    "stopping",
    "cancelled",
]

LiveChunkSource = Literal["local_pattern", "fake", "symbolic", "llm"]

# Degradation / lifecycle warning codes (session UI + logs — counts only).
LIVE_DEGRADED_PATTERN_CONTINUE = "live_degraded_pattern_continue"
LIVE_HARMONY_EMPTY = "live_harmony_empty"
LIVE_ENGINE_UNAVAILABLE = "live_engine_unavailable"
LIVE_MIDI_PHASE_EXCLUSION = "live_midi_phase_exclusion"

# Domain → HTTP error codes
LIVE_HORIZON_INVALID = "live_horizon_invalid"
LIVE_FEATURES_TOO_LARGE = "live_features_too_large"
LIVE_SESSION_CONTEXT_INVALID = "live_session_context_invalid"
LIVE_PREDICT_TIMEOUT = "live_predict_timeout"
LIVE_EVENTS_FORBIDDEN_IN_REQUEST = "live_events_forbidden_in_request"
LIVE_PREDICT_INTERNAL = "live_predict_internal"
LIVE_PREDICT_UNAVAILABLE = "live_predict_unavailable"

LIVE_ERROR_HTTP: dict[str, int] = {
    LIVE_HORIZON_INVALID: 422,
    LIVE_FEATURES_TOO_LARGE: 422,
    LIVE_SESSION_CONTEXT_INVALID: 422,
    LIVE_PREDICT_TIMEOUT: 422,
    LIVE_EVENTS_FORBIDDEN_IN_REQUEST: 422,
    LIVE_PREDICT_INTERNAL: 502,
    LIVE_PREDICT_UNAVAILABLE: 503,
}

# Reject playable / oversized / identity payloads at any nest level in predict.
FORBIDDEN_LIVE_PREDICT_KEYS: frozenset[str] = frozenset(
    {
        "tracks",
        "musicxml",
        "midi",
        "wav",
        "analysis",
        "analysis_report",
        "composition",
        "music",
        "events",
        "note_events",
        "embedding",
        "vector",
        "motifs",
        "raw_midi",
        "midi_dump",
        "edited_music_json",
        "music_json",
    }
)

LATENCY_MARK_KEYS: tuple[str, ...] = (
    "midi_input",
    "analysis",
    "generation",
    "scheduling",
)


class LivePerformanceError(Exception):
    """Domain error for live predict / session validation — sanitized detail only."""

    code: str = "live_performance_error"
    http_status: int = 422

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
            self.http_status = LIVE_ERROR_HTTP.get(self.code, self.http_status)
        self.details = details or {}
        logger.warning(
            "Live performance domain error",
            extra={"code": self.code, "detail_keys": sorted(self.details.keys())},
        )


def map_live_performance_error_to_http(
    exc: LivePerformanceError,
) -> tuple[int, dict[str, Any]]:
    """Return (status, sanitized detail dict) for HTTPException."""
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


def _reject_forbidden_keys(payload: Any, *, path: str = "") -> None:
    """Raise ``ValueError`` if nested dict keys include forbidden playable/dump fields."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_s = str(key)
            child = f"{path}.{key_s}" if path else key_s
            if key_s.lower() in FORBIDDEN_LIVE_PREDICT_KEYS:
                logger.debug(
                    "Live predict schema reject",
                    extra={"code": LIVE_EVENTS_FORBIDDEN_IN_REQUEST, "path": child},
                )
                raise ValueError(
                    f"{LIVE_EVENTS_FORBIDDEN_IN_REQUEST}: forbidden key at {child[:120]}"
                )
            _reject_forbidden_keys(value, path=child)
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            _reject_forbidden_keys(item, path=f"{path}[{index}]")


def assert_no_forbidden_predict_keys(payload: Any) -> None:
    """Raise ``LivePerformanceError`` when predict payload nests playable dumps."""
    try:
        _reject_forbidden_keys(payload)
    except ValueError as exc:
        message = str(exc)
        path = ""
        if ": forbidden key at " in message:
            path = message.split(": forbidden key at ", 1)[-1]
        raise LivePerformanceError(
            "Predict request must not include composition, events, or MIDI dumps",
            code=LIVE_EVENTS_FORBIDDEN_IN_REQUEST,
            details={"path": path[:120]} if path else None,
        ) from exc


# --- Nested DTOs -----------------------------------------------------------------


class LiveClockSnapshot(BaseModel):
    """Beat/bar clock sample derived from Transport + timeline."""

    model_config = ConfigDict(extra="forbid")

    tick: int = Field(ge=0)
    bar: int = Field(ge=1)
    beat: int = Field(ge=1)
    tempo: float | None = Field(default=None, gt=0, le=400)


class LiveActiveHarmony(BaseModel):
    """Read-only harmony span context — never playable notes."""

    model_config = ConfigDict(extra="forbid")

    symbol: str | None = Field(default=None, max_length=64)
    start_tick: int | None = Field(default=None, ge=0)
    duration_ticks: int | None = Field(default=None, ge=0)


class LiveHorizon(BaseModel):
    """Prediction horizon — bars and/or ms ahead of playhead.

    Field bounds are the absolute schema ceiling; call
    ``assert_horizon_within_settings`` for LIVE_* env caps (domain 422).
    """

    model_config = ConfigDict(extra="forbid")

    bars: float = Field(ge=0.25, le=8)
    ms: float = Field(ge=50, le=60_000)


def assert_horizon_within_settings(
    horizon: LiveHorizon,
    settings: LivePerformanceSettings | None = None,
) -> None:
    """Raise ``LivePerformanceError`` when horizon exceeds LIVE_* caps."""
    cfg = settings if settings is not None else load_live_performance_settings()
    if horizon.bars < cfg.horizon_bars_min or horizon.bars > cfg.horizon_bars_max:
        raise LivePerformanceError(
            "Horizon bars outside configured bounds",
            code=LIVE_HORIZON_INVALID,
            details={
                "bars": horizon.bars,
                "min": cfg.horizon_bars_min,
                "max": cfg.horizon_bars_max,
            },
        )
    if horizon.ms < cfg.horizon_ms_min or horizon.ms > cfg.horizon_ms_max:
        raise LivePerformanceError(
            "Horizon ms outside configured bounds",
            code=LIVE_HORIZON_INVALID,
            details={
                "ms": horizon.ms,
                "min": cfg.horizon_ms_min,
                "max": cfg.horizon_ms_max,
            },
        )


class LiveLatencyMs(BaseModel):
    """Aggregated latency marks — ms only, no payloads."""

    model_config = ConfigDict(extra="forbid")

    midi_input: float | None = Field(default=None, ge=0)
    analysis: float | None = Field(default=None, ge=0)
    generation: float | None = Field(default=None, ge=0)
    scheduling: float | None = Field(default=None, ge=0)


class LiveDegradationState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active: bool = False
    code: str | None = Field(default=None, max_length=80)
    count: int = Field(default=0, ge=0)


class LiveTransportSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    playing: bool = False
    tick: int = Field(default=0, ge=0)
    bar: int = Field(default=1, ge=1)
    beat: int = Field(default=1, ge=1)


class LiveSessionV1(BaseModel):
    """FE / optional BE echo of live.session.v1 — never PROJECT_DB."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["live.session.v1"] = LIVE_SESSION_SCHEMA
    session_id: str = Field(min_length=1, max_length=64)
    phase: LiveSessionPhase = "idle"
    transport: LiveTransportSnapshot = Field(default_factory=LiveTransportSnapshot)
    horizon: LiveHorizon | None = None
    active_harmony: LiveActiveHarmony = Field(default_factory=LiveActiveHarmony)
    latency_ms: LiveLatencyMs = Field(default_factory=LiveLatencyMs)
    degradation: LiveDegradationState = Field(default_factory=LiveDegradationState)


class LivePredictFeatures(BaseModel):
    """Bounded numeric/categorical summary only — no event arrays."""

    model_config = ConfigDict(extra="allow")

    density: float | None = Field(default=None, ge=0, le=1)
    recent_note_count: int | None = Field(default=None, ge=0, le=512)
    pitch_class_histogram: list[float] | None = Field(default=None, max_length=12)
    velocity_mean: float | None = Field(default=None, ge=0, le=127)
    category: str | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def _forbid_nested_playable(self) -> LivePredictFeatures:
        raw = self.model_dump(exclude_none=True)
        _reject_forbidden_keys(raw)
        return self


def assert_features_within_size(
    features: LivePredictFeatures | dict[str, Any],
    settings: LivePerformanceSettings | None = None,
) -> None:
    """Raise when serialized features exceed LIVE_PREDICT_MAX_FEATURES_BYTES."""
    cfg = settings if settings is not None else load_live_performance_settings()
    raw = (
        features.model_dump(exclude_none=True)
        if isinstance(features, LivePredictFeatures)
        else dict(features)
    )
    assert_no_forbidden_predict_keys(raw)
    encoded = json.dumps(raw, separators=(",", ":"), default=str)
    size = len(encoded.encode("utf-8"))
    if size > cfg.max_features_bytes:
        logger.debug(
            "Live predict schema reject",
            extra={"code": LIVE_FEATURES_TOO_LARGE, "bytes": size},
        )
        raise LivePerformanceError(
            "Features payload exceeds LIVE_PREDICT_MAX_FEATURES_BYTES",
            code=LIVE_FEATURES_TOO_LARGE,
            details={"max_bytes": cfg.max_features_bytes, "bytes": size},
        )


class LiveAccompanimentPredictRequestV1(BaseModel):
    """Cold-path predict body — composition/events forbidden."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["live.accompaniment.predict.request.v1"] = (
        LIVE_PREDICT_REQUEST_SCHEMA
    )
    session_id: str = Field(min_length=1, max_length=64)
    request_id: str = Field(min_length=1, max_length=64)
    clock: LiveClockSnapshot
    active_harmony: LiveActiveHarmony = Field(default_factory=LiveActiveHarmony)
    features: LivePredictFeatures = Field(default_factory=LivePredictFeatures)
    horizon: LiveHorizon

    @model_validator(mode="before")
    @classmethod
    def _forbid_playable_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            _reject_forbidden_keys(data)
        return data

    @model_validator(mode="after")
    def _require_ids(self) -> LiveAccompanimentPredictRequestV1:
        if not self.session_id.strip() or not self.request_id.strip():
            raise ValueError(f"{LIVE_SESSION_CONTEXT_INVALID}: session_id and request_id required")
        logger.debug(
            "Live predict request schema validate",
            extra={"ok": True, "session_id_len": len(self.session_id)},
        )
        return self


def validate_predict_request_domain(
    request: LiveAccompanimentPredictRequestV1,
    settings: LivePerformanceSettings | None = None,
) -> None:
    """Apply LIVE_* caps and forbidden-key domain rules after Pydantic parse."""
    cfg = settings if settings is not None else load_live_performance_settings()
    assert_horizon_within_settings(request.horizon, cfg)
    assert_features_within_size(request.features, cfg)
    assert_no_forbidden_predict_keys(request.model_dump(mode="python"))
    if not request.session_id.strip() or not request.request_id.strip():
        raise LivePerformanceError(
            "session_id and request_id are required",
            code=LIVE_SESSION_CONTEXT_INVALID,
        )


class LiveAccompanimentEvent(BaseModel):
    """Ephemeral chunk event — not a V2 track event id until Commit."""

    model_config = ConfigDict(extra="forbid")

    pitch: int = Field(ge=0, le=127)
    start_tick: int = Field(ge=0)
    duration_ticks: int = Field(ge=1)
    velocity: int = Field(default=80, ge=1, le=127)
    track_role: str | None = Field(default=None, max_length=32)


class LiveAccompanimentChunkV1(BaseModel):
    """Predict response / buffer insert — never stored in SQLite."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["live.accompaniment.chunk.v1"] = LIVE_ACCOMPANIMENT_CHUNK_SCHEMA
    request_id: str = Field(min_length=1, max_length=64)
    session_id: str = Field(min_length=1, max_length=64)
    start_tick: int = Field(ge=0)
    events: list[LiveAccompanimentEvent] = Field(default_factory=list)
    source: LiveChunkSource = "fake"
    generated_at_ms: float = Field(ge=0)
    latency_ms: LiveLatencyMs | None = None

    @field_validator("events")
    @classmethod
    def _cap_events(cls, value: list[LiveAccompanimentEvent]) -> list[LiveAccompanimentEvent]:
        settings = load_live_performance_settings()
        if len(value) > settings.max_events_per_chunk:
            raise ValueError(
                f"chunk events {len(value)} exceed max {settings.max_events_per_chunk}"
            )
        return value


def assert_chunk_event_count(
    events: list[Any],
    settings: LivePerformanceSettings | None = None,
) -> None:
    """Raise ``LivePerformanceError`` when chunk event count exceeds cap."""
    cfg = settings if settings is not None else load_live_performance_settings()
    if len(events) > cfg.max_events_per_chunk:
        raise LivePerformanceError(
            "Chunk event count exceeds LIVE_ACCOMP_MAX_EVENTS_PER_CHUNK",
            code=LIVE_SESSION_CONTEXT_INVALID,
            details={"count": len(events), "max": cfg.max_events_per_chunk},
        )
