"""Strict ``adaptive.runtime.music_state.v1`` compressed long-term memory.

MusicState is session-only. It never stores note events, pitch lists, or a
composition. Legacy ``adaptive.runtime.context.v1`` fields are projected from
it for one-release compatibility.
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

from app.adaptive_score_schemas import (
    ADAPTIVE_SCORE_ERROR_CODES,
    AdaptiveScoreError,
    _ID_RE,
    _reject_bool,
    log_adaptive_schema_failure,
)

logger = logging.getLogger(__name__)

MUSIC_STATE_SCHEMA: Literal["adaptive.runtime.music_state.v1"] = (
    "adaptive.runtime.music_state.v1"
)

MUSIC_STATE_CAP_THEME_IDS = 16
MUSIC_STATE_CAP_MOTIF_USAGE = 32
MUSIC_STATE_CAP_HARMONY_TRAJECTORY = 16
MUSIC_STATE_CAP_REPETITION_HISTORY = 16
MUSIC_STATE_CAP_ENERGY = 8
MUSIC_STATE_CAP_TENSION = 8
MUSIC_STATE_CAP_ORCHESTRATION = 16

MusicStateGuardFlag = Literal[
    "repetition_pressure",
    "theme_drift",
    "harmonic_dead_end",
]

_DIGEST16_RE = re.compile(r"^[0-9a-f]{16}$")

_MUSIC_STATE_FORBIDDEN_KEYS = frozenset(
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


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AdaptiveRuntimeMotifUsageV1(_Strict):
    """Motif use digest. Not a pitch list."""

    motif_id: str = Field(min_length=1, max_length=120)
    use_count: int = Field(ge=0, le=1_000_000)
    last_virtual_bar: int = Field(ge=1)

    @field_validator("use_count", "last_virtual_bar", mode="before")
    @classmethod
    def reject_bool_counts(cls, value: object) -> object:
        _reject_bool(value, model=cls.__name__, field="use_count")
        return value

    @field_validator("motif_id")
    @classmethod
    def motif_token(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            log_adaptive_schema_failure(cls.__name__, "motif_id", "adaptive_score_invalid")
            raise ValueError("motif_id must not be empty")
        return cleaned


class AdaptiveRuntimeRepetitionEntryV1(_Strict):
    """Window digest count. Digests are opaque hex, never logged at INFO."""

    digest16: str
    count: int = Field(ge=0, le=1_000_000)

    @field_validator("count", mode="before")
    @classmethod
    def reject_bool_count(cls, value: object) -> object:
        _reject_bool(value, model=cls.__name__, field="count")
        return value

    @field_validator("digest16")
    @classmethod
    def digest_hex(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if not _DIGEST16_RE.fullmatch(cleaned):
            log_adaptive_schema_failure(cls.__name__, "digest16", "adaptive_score_invalid")
            raise ValueError("digest16 must be 16 lowercase hex characters")
        return cleaned


class AdaptiveRuntimeMusicStateV1(_Strict):
    """Compressed long-term MusicState. No note events and no pitch lists."""

    schema_version: Literal["adaptive.runtime.music_state.v1"] = MUSIC_STATE_SCHEMA
    active_theme_ids: list[str] = Field(default_factory=list, max_length=MUSIC_STATE_CAP_THEME_IDS)
    motif_usage: list[AdaptiveRuntimeMotifUsageV1] = Field(
        default_factory=list,
        max_length=MUSIC_STATE_CAP_MOTIF_USAGE,
    )
    harmony_trajectory: list[str] = Field(
        default_factory=list,
        max_length=MUSIC_STATE_CAP_HARMONY_TRAJECTORY,
    )
    repetition_history: list[AdaptiveRuntimeRepetitionEntryV1] = Field(
        default_factory=list,
        max_length=MUSIC_STATE_CAP_REPETITION_HISTORY,
    )
    energy: list[float] = Field(default_factory=list, max_length=MUSIC_STATE_CAP_ENERGY)
    tension: list[float] = Field(default_factory=list, max_length=MUSIC_STATE_CAP_TENSION)
    orchestration_history: list[str] = Field(
        default_factory=list,
        max_length=MUSIC_STATE_CAP_ORCHESTRATION,
    )
    runtime_state_id: str | None = None
    summary_digest: str | None = None
    virtual_bar: int = Field(default=1, ge=1)
    guard_flags: list[MusicStateGuardFlag] = Field(default_factory=list, max_length=8)

    @field_validator("virtual_bar", mode="before")
    @classmethod
    def reject_bool_virtual_bar(cls, value: object) -> object:
        _reject_bool(value, model=cls.__name__, field="virtual_bar")
        return value

    @field_validator("active_theme_ids", mode="before")
    @classmethod
    def theme_cap(cls, value: object) -> object:
        if isinstance(value, list) and len(value) > MUSIC_STATE_CAP_THEME_IDS:
            log_adaptive_schema_failure(
                cls.__name__,
                "active_theme_ids",
                "adaptive_score_too_large",
            )
            raise ValueError("cap_exceeded")
        return value

    @field_validator("active_theme_ids")
    @classmethod
    def unique_theme_ids(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in value:
            token = item.strip()
            if not token or len(token) > 120:
                log_adaptive_schema_failure(
                    cls.__name__,
                    "active_theme_ids",
                    "adaptive_score_invalid",
                )
                raise ValueError("active_theme_ids entries must be 1..120 characters")
            cleaned.append(token)
        if len(cleaned) != len(set(cleaned)):
            log_adaptive_schema_failure(
                cls.__name__,
                "active_theme_ids",
                "adaptive_score_invalid",
            )
            raise ValueError("active_theme_ids must be unique")
        return cleaned

    @field_validator("harmony_trajectory", mode="before")
    @classmethod
    def harmony_cap(cls, value: object) -> object:
        if isinstance(value, list) and len(value) > MUSIC_STATE_CAP_HARMONY_TRAJECTORY:
            log_adaptive_schema_failure(
                cls.__name__,
                "harmony_trajectory",
                "adaptive_score_too_large",
            )
            raise ValueError("cap_exceeded")
        return value

    @field_validator("harmony_trajectory")
    @classmethod
    def harmony_labels(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in value:
            token = item.strip()
            if not token or len(token) > 32:
                log_adaptive_schema_failure(
                    cls.__name__,
                    "harmony_trajectory",
                    "adaptive_score_invalid",
                )
                raise ValueError("harmony_trajectory labels must be 1..32 characters")
            cleaned.append(token)
        return cleaned

    @field_validator("orchestration_history", mode="before")
    @classmethod
    def orchestration_cap(cls, value: object) -> object:
        if isinstance(value, list) and len(value) > MUSIC_STATE_CAP_ORCHESTRATION:
            log_adaptive_schema_failure(
                cls.__name__,
                "orchestration_history",
                "adaptive_score_too_large",
            )
            raise ValueError("cap_exceeded")
        return value

    @field_validator("orchestration_history")
    @classmethod
    def orchestration_digests(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in value:
            token = item.strip().lower()
            if not _DIGEST16_RE.fullmatch(token):
                log_adaptive_schema_failure(
                    cls.__name__,
                    "orchestration_history",
                    "adaptive_score_invalid",
                )
                raise ValueError("orchestration digests must be 16 lowercase hex characters")
            cleaned.append(token)
        return cleaned

    @field_validator("energy", "tension", mode="before")
    @classmethod
    def unit_interval_rings(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        if len(value) > MUSIC_STATE_CAP_ENERGY:
            log_adaptive_schema_failure(cls.__name__, "energy", "adaptive_score_too_large")
            raise ValueError("cap_exceeded")
        for item in value:
            _reject_bool(item, model=cls.__name__, field="energy")
            if isinstance(item, (int, float)) and not isinstance(item, bool):
                if item < 0 or item > 1:
                    log_adaptive_schema_failure(
                        cls.__name__,
                        "energy",
                        "adaptive_score_invalid",
                    )
                    raise ValueError("ring values must be between 0 and 1")
        return value

    @field_validator("motif_usage", mode="before")
    @classmethod
    def motif_cap(cls, value: object) -> object:
        if isinstance(value, list) and len(value) > MUSIC_STATE_CAP_MOTIF_USAGE:
            log_adaptive_schema_failure(cls.__name__, "motif_usage", "adaptive_score_too_large")
            raise ValueError("cap_exceeded")
        return value

    @field_validator("repetition_history", mode="before")
    @classmethod
    def repetition_cap(cls, value: object) -> object:
        if isinstance(value, list) and len(value) > MUSIC_STATE_CAP_REPETITION_HISTORY:
            log_adaptive_schema_failure(
                cls.__name__,
                "repetition_history",
                "adaptive_score_too_large",
            )
            raise ValueError("cap_exceeded")
        return value

    @field_validator("runtime_state_id")
    @classmethod
    def state_token(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _ID_RE.fullmatch(value):
            log_adaptive_schema_failure(
                cls.__name__,
                "runtime_state_id",
                "adaptive_score_invalid",
            )
            raise ValueError("runtime_state_id must be a short token")
        return value

    @field_validator("summary_digest")
    @classmethod
    def summary_hex(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().lower()
        if not _DIGEST16_RE.fullmatch(cleaned):
            log_adaptive_schema_failure(
                cls.__name__,
                "summary_digest",
                "adaptive_score_invalid",
            )
            raise ValueError("summary_digest must be 16 lowercase hex characters")
        return cleaned

    @field_validator("guard_flags")
    @classmethod
    def unique_guard_flags(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            log_adaptive_schema_failure(cls.__name__, "guard_flags", "adaptive_score_invalid")
            raise ValueError("guard_flags must be unique")
        return value

    @model_validator(mode="after")
    def closed_guard_subset(self) -> AdaptiveRuntimeMusicStateV1:
        allowed = {"repetition_pressure", "theme_drift", "harmonic_dead_end"}
        for flag in self.guard_flags:
            if flag not in allowed:
                log_adaptive_schema_failure(
                    self.__class__.__name__,
                    "guard_flags",
                    "adaptive_score_invalid",
                )
                raise ValueError("guard_flags must be a closed subset")
        return self


def empty_music_state(*, virtual_bar: int = 1) -> AdaptiveRuntimeMusicStateV1:
    """Minimal MusicState for a new continuous session."""
    return AdaptiveRuntimeMusicStateV1(virtual_bar=virtual_bar)


def project_legacy_runtime_context(music_state: AdaptiveRuntimeMusicStateV1):
    """Project MusicState into legacy ``adaptive.runtime.context.v1`` fields.

    Does not nest MusicState rings inside context. Never logs harmony labels.
    Import of ``AdaptiveRuntimeContextV1`` stays local to avoid a cycle with
    continuation schemas that embed MusicState as a sibling.
    """
    from app.adaptive_runtime_continuation_schemas import AdaptiveRuntimeContextV1

    logger.debug(
        "Projecting MusicState to legacy runtime context",
        extra={
            "theme_count": len(music_state.active_theme_ids),
            "harmony_count": len(music_state.harmony_trajectory),
            "virtual_bar": music_state.virtual_bar,
            "guard_flag_count": len(music_state.guard_flags),
        },
    )
    harmony_tail = (
        music_state.harmony_trajectory[-1] if music_state.harmony_trajectory else None
    )
    repetition_count = 0
    if music_state.repetition_history:
        repetition_count = min(8, max(entry.count for entry in music_state.repetition_history))
    recent_start: int | None = None
    recent_end: int | None = None
    if music_state.virtual_bar >= 1:
        recent_end = music_state.virtual_bar
        recent_start = max(1, music_state.virtual_bar)
    return AdaptiveRuntimeContextV1(
        theme_ids=list(music_state.active_theme_ids[:MUSIC_STATE_CAP_THEME_IDS]),
        harmony_tail=harmony_tail,
        harmony_chord_count=min(32, len(music_state.harmony_trajectory)),
        recent_start_bar=recent_start,
        recent_end_bar=recent_end,
        repetition_count=repetition_count,
        energy=list(music_state.energy[:MUSIC_STATE_CAP_ENERGY]),
    )


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


def _reject_forbidden_keys(data: dict[str, Any], label: str) -> None:
    for key in data:
        if str(key) in _MUSIC_STATE_FORBIDDEN_KEYS:
            log_adaptive_schema_failure(label, str(key), "embedded_note_material")
            raise AdaptiveScoreError(
                "embedded_note_material",
                ADAPTIVE_SCORE_ERROR_CODES["embedded_note_material"],
                http_status=422,
                details={"field": str(key)},
            )


def parse_adaptive_runtime_music_state(data: object) -> AdaptiveRuntimeMusicStateV1:
    """Validate MusicState. Does not log harmony labels or digests at INFO."""
    body = _require_object(data, "AdaptiveRuntimeMusicStateV1")
    version = body.get("schema_version")
    if version != MUSIC_STATE_SCHEMA:
        log_adaptive_schema_failure(
            "AdaptiveRuntimeMusicStateV1",
            "schema_version",
            "unsupported_schema_version",
        )
        raise AdaptiveScoreError(
            "unsupported_schema_version",
            "Body must be adaptive.runtime.music_state.v1.",
            http_status=422,
            details={"field": "schema_version"},
        )
    _reject_forbidden_keys(body, "AdaptiveRuntimeMusicStateV1")
    try:
        return AdaptiveRuntimeMusicStateV1.model_validate(body)
    except ValidationError as exc:
        _raise_validation("AdaptiveRuntimeMusicStateV1", exc)
    raise AssertionError("adaptive runtime music state")
