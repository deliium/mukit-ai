"""Non-playable ``film.score.plan.v1`` and the preview request.

The plan is inspectable musical structure. Playable notes stay on
``composition.v2`` ``tracks[].events[]``. Cue instructions and labels are not
fields on these models.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Literal, NoReturn

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.composition_schemas import KEY_PATTERN, normalize_time_signature

logger = logging.getLogger(__name__)

FILM_SCORE_PLAN_SCHEMA_VERSION: Literal["film.score.plan.v1"] = "film.score.plan.v1"
FILM_SCORE_PREVIEW_SCHEMA_VERSION: Literal["film.score.preview.v1"] = "film.score.preview.v1"

FILM_CUE_KINDS: tuple[str, ...] = (
    "music_start",
    "music_stop",
    "hit_point",
    "reveal",
    "cut",
    "action",
    "dialogue",
    "emotional_cue",
    "user_defined",
)
FILM_IMPORTANCE: tuple[str, ...] = ("low", "medium", "high", "critical")
FILM_HIT_STATUSES: tuple[str, ...] = (
    "aligned",
    "soft",
    "unsatisfiable",
    "boundary",
    "silence",
)
FILM_PROFILE_STRENGTHS: tuple[str, ...] = ("off", "light", "normal", "strong")
FILM_AGENT_IDS: tuple[str, ...] = (
    "creative_director",
    "structure_form",
    "harmony",
    "melody_motif",
    "arrangement",
    "orchestration",
    "performance_expression",
    "production",
    "critic",
)
FILM_WARNING_CODES: tuple[str, ...] = (
    "hit_unsatisfiable",
    "film_motif_absent",
    "harmony_plan_empty",
    "film_origin_not_updated",
)

FORBIDDEN_PLAYABLE_TOP_LEVEL: frozenset[str] = frozenset(
    {
        "tracks",
        "events",
        "notes",
        "musicxml",
        "midi",
        "wav",
        "composition",
        "music",
        "note_events",
        "analysis",
    }
)

FILM_SCORE_ERROR_MESSAGES: dict[str, str] = {
    "film_score_invalid": "Film score document failed validation.",
    "film_duration_invalid": "Music window is shorter than one bar at the minimum tempo.",
    "film_frame_rate_required": "Film scoring requires a closed frame rate.",
    "film_asset_missing": "This project has no video asset.",
    "film_motif_missing": "A requested motif id is not on the source composition.",
    "film_instrument_unknown": "An instrument id is not in the arrangement catalog.",
    "film_score_conflict": "Film score commit does not match the preview.",
    "film_score_replace_required": "The composition already has notes. Set replace_existing to commit.",
    "film_agent_failed": "A film-score agent call failed.",
}

_HTTP_STATUS: dict[str, int] = {
    "film_asset_missing": 404,
    "film_score_conflict": 409,
    "film_score_replace_required": 409,
    "film_agent_failed": 503,
}

FilmScoreErrorCode = Literal[
    "film_score_invalid",
    "film_duration_invalid",
    "film_frame_rate_required",
    "film_asset_missing",
    "film_motif_missing",
    "film_instrument_unknown",
    "film_score_conflict",
    "film_score_replace_required",
    "film_agent_failed",
]


class FilmScoreError(Exception):
    """Domain error mapped to HTTP by the film-score router."""

    def __init__(self, code: str, message: str | None = None, *, preview: object | None = None) -> None:
        self.code = code
        self.message = message or FILM_SCORE_ERROR_MESSAGES.get(code, code)
        self.http_status = _HTTP_STATUS.get(code, 422)
        self.preview = preview
        super().__init__(self.message)


def log_film_schema_rejection(model: str, code: str) -> None:
    """DEBUG a schema rejection. The brief is never included."""
    logger.debug(
        "film score model rejected model=%s error_code=%s",
        model,
        code,
    )


def _reject(model: str, code: str = "film_score_invalid") -> NoReturn:
    log_film_schema_rejection(model, code)
    raise ValueError(code)


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _reject_playable(model: str, data: Any) -> None:
    if not isinstance(data, dict):
        return
    forbidden = sorted(FORBIDDEN_PLAYABLE_TOP_LEVEL.intersection(data.keys()))
    if forbidden:
        logger.debug(
            "film score model rejected model=%s error_code=film_score_invalid field_count=%s",
            model,
            len(forbidden),
        )
        raise ValueError("film_score_invalid")


class FilmCueSnapshot(BaseModel):
    """Musical fields copied from one stored hit. Instruction and label stay behind."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^hit_[0-9a-f]{8}$")
    kind: Literal[
        "music_start",
        "music_stop",
        "hit_point",
        "reveal",
        "cut",
        "action",
        "dialogue",
        "emotional_cue",
        "user_defined",
    ]
    importance: Literal["low", "medium", "high", "critical"]
    video_seconds: float = Field(ge=0)
    tolerance_frames: int = Field(ge=0, le=240)

    @field_validator("kind", "importance", mode="before")
    @classmethod
    def _known_cue_field(cls, value: object) -> object:
        if value not in FILM_CUE_KINDS and value not in FILM_IMPORTANCE:
            _reject(cls.__name__)
        return value

    @field_validator("video_seconds")
    @classmethod
    def _finite_seconds(cls, value: float) -> float:
        if isinstance(value, bool) or not _finite(value) or float(value) < 0:
            _reject(cls.__name__)
        return float(value)

    @field_validator("tolerance_frames")
    @classmethod
    def _tolerance(cls, value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 240:
            _reject(cls.__name__)
        return value


class FilmScorePreviewRequest(BaseModel):
    """Explicit preview body. Opening the Agents tab does not send this."""

    model_config = ConfigDict(extra="forbid")

    brief: str = Field(min_length=1, max_length=2000)
    profile_id: str | None = Field(default=None, min_length=1, max_length=80)
    profile_strength: Literal["off", "light", "normal", "strong"] = "off"
    motif_ids: list[str] = Field(default_factory=list, max_length=16)
    instruments: list[str] = Field(min_length=1, max_length=16)
    key: str | None = None
    time_signature: str | None = None
    opening_tempo: int | None = Field(default=None, ge=40, le=240)
    tempo_min: int | None = Field(default=None, ge=40, le=240)
    tempo_max: int | None = Field(default=None, ge=40, le=240)
    target_duration_seconds: float | None = None
    replace_existing: bool = False

    @model_validator(mode="before")
    @classmethod
    def _reject_playable_and_brief(cls, data: Any) -> Any:
        _reject_playable(cls.__name__, data)
        if isinstance(data, dict):
            brief = data.get("brief")
            if isinstance(brief, str) and (len(brief) < 1 or len(brief) > 2000):
                _reject(cls.__name__)
        return data

    @field_validator("profile_strength", mode="before")
    @classmethod
    def _strength(cls, value: object) -> object:
        if value is None:
            return "off"
        if value not in FILM_PROFILE_STRENGTHS:
            _reject(cls.__name__)
        return value

    @field_validator("motif_ids")
    @classmethod
    def _motif_ids(cls, value: list[str]) -> list[str]:
        if len(value) > 16:
            _reject(cls.__name__)
        cleaned: list[str] = []
        for item in value:
            text = str(item).strip()
            if len(text) < 1 or len(text) > 120:
                _reject(cls.__name__)
            cleaned.append(text)
        return cleaned

    @field_validator("instruments")
    @classmethod
    def _instruments(cls, value: list[str]) -> list[str]:
        if len(value) < 1 or len(value) > 16:
            _reject(cls.__name__)
        cleaned: list[str] = []
        for item in value:
            if not isinstance(item, str):
                _reject(cls.__name__)
            text = item.strip()
            if not text:
                _reject(cls.__name__)
            cleaned.append(text)
        return cleaned

    @field_validator("key")
    @classmethod
    def _key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        key = " ".join(value.strip().split())
        if not KEY_PATTERN.match(key):
            _reject(cls.__name__)
        return key

    @field_validator("time_signature")
    @classmethod
    def _meter(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return normalize_time_signature(value, model_name=cls.__name__)
        except ValueError:
            _reject(cls.__name__)

    @field_validator("opening_tempo", "tempo_min", "tempo_max")
    @classmethod
    def _tempo_int(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < 40 or value > 240:
            _reject(cls.__name__)
        return value

    @field_validator("target_duration_seconds")
    @classmethod
    def _duration(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if not _finite(value) or float(value) <= 0 or float(value) > 3600:
            _reject(cls.__name__)
        return float(value)

    @model_validator(mode="after")
    def _tempo_band(self) -> FilmScorePreviewRequest:
        if self.tempo_min is None and self.tempo_max is None:
            if self.opening_tempo is None:
                object.__setattr__(self, "tempo_min", 96)
                object.__setattr__(self, "tempo_max", 132)
            else:
                object.__setattr__(self, "tempo_min", max(40, self.opening_tempo - 24))
                object.__setattr__(self, "tempo_max", min(240, self.opening_tempo + 24))
        elif self.tempo_min is None or self.tempo_max is None:
            _reject(self.__class__.__name__)
        if self.tempo_min is not None and self.tempo_max is not None and self.tempo_min > self.tempo_max:
            _reject(self.__class__.__name__)
        return self


class FilmSyncOrigin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    video_origin_seconds: float = Field(ge=0)
    musical_origin_tick: Literal[0] = 0

    @field_validator("video_origin_seconds")
    @classmethod
    def _finite_origin(cls, value: float) -> float:
        if not _finite(value) or float(value) < 0:
            _reject(cls.__name__)
        return float(value)


class FilmScoreSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=40)
    label: str = Field(min_length=1, max_length=80)
    start_bar: int = Field(ge=1)
    bar_count: int = Field(ge=1, le=16)
    density: Literal["sparse", "moderate"]
    tempo_bpm: int = Field(ge=40, le=240)
    start_video_seconds: float = Field(ge=0)
    end_video_seconds: float = Field(gt=0)


class FilmTempoChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tick: int = Field(gt=0)
    bpm: int = Field(ge=40, le=240)
    section_id: str = Field(min_length=1, max_length=40)
    reason_code: Literal["phrase_fit"]


class FilmTempoStrategy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy: Literal["section_boundary"] = "section_boundary"
    changes: list[FilmTempoChange] = Field(default_factory=list, max_length=8)


class FilmHarmonicArcEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_bar: int = Field(ge=1)
    end_bar: int = Field(ge=1)
    key: str
    function: str = Field(min_length=1, max_length=80)

    @field_validator("key")
    @classmethod
    def _key(cls, value: str) -> str:
        key = " ".join(value.strip().split())
        if not KEY_PATTERN.match(key):
            _reject(cls.__name__)
        return key


class FilmMotifAppearance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    motif_id: str = Field(min_length=1, max_length=120)
    section_id: str = Field(min_length=1, max_length=40)


class FilmHitAlignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cue_id: str = Field(pattern=r"^hit_[0-9a-f]{8}$")
    kind: Literal[
        "music_start",
        "music_stop",
        "hit_point",
        "reveal",
        "cut",
        "action",
        "dialogue",
        "emotional_cue",
        "user_defined",
    ]
    importance: Literal["low", "medium", "high", "critical"]
    status: Literal["aligned", "soft", "unsatisfiable", "boundary", "silence"]
    target_bar: int | None = Field(default=None, ge=1)
    delta_seconds: float
    tempo_change_added: bool = False

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value: object) -> object:
        if value not in FILM_HIT_STATUSES:
            _reject(cls.__name__)
        return value

    @field_validator("delta_seconds")
    @classmethod
    def _delta(cls, value: float) -> float:
        if not _finite(value):
            _reject(cls.__name__)
        return float(value)


class FilmDensityRegion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cue_id: str = Field(pattern=r"^hit_[0-9a-f]{8}$")
    start_bar: int = Field(ge=1)
    end_bar: int = Field(ge=1)
    density: Literal["sparse"] = "sparse"


class FilmScorePlanV1(BaseModel):
    """Inspectable film score plan. ``committed`` is always false."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["film.score.plan.v1"] = FILM_SCORE_PLAN_SCHEMA_VERSION
    project_id: str = Field(min_length=1, max_length=128)
    source_fingerprint: str = Field(min_length=1, max_length=128)
    scoring_document_revision: int = Field(ge=0)
    target_duration_seconds: float = Field(gt=0, le=3600)
    music_start_seconds: float = Field(ge=0)
    music_end_seconds: float = Field(gt=0)
    sync_origin: FilmSyncOrigin
    time_signature: str
    key: str
    root_tempo: int = Field(ge=40, le=240)
    tempo_min: int = Field(ge=40, le=240)
    tempo_max: int = Field(ge=40, le=240)
    sections: list[FilmScoreSection] = Field(min_length=1, max_length=32)
    tempo_strategy: FilmTempoStrategy
    harmonic_arc: list[FilmHarmonicArcEntry] = Field(default_factory=list, max_length=32)
    motif_appearances: list[FilmMotifAppearance] = Field(default_factory=list, max_length=16)
    hit_alignments: list[FilmHitAlignment]
    density_regions: list[FilmDensityRegion] = Field(default_factory=list, max_length=32)
    agent_sequence: list[str] = Field(default_factory=list, max_length=9)
    warnings: list[str] = Field(default_factory=list, max_length=32)
    committed: Literal[False] = False

    @model_validator(mode="before")
    @classmethod
    def _reject_shape(cls, data: Any) -> Any:
        _reject_playable(cls.__name__, data)
        if not isinstance(data, dict):
            return data
        sections = data.get("sections")
        if isinstance(sections, list) and len(sections) > 32:
            _reject(cls.__name__)
        strategy = data.get("tempo_strategy")
        if isinstance(strategy, dict):
            changes = strategy.get("changes")
            if isinstance(changes, list) and len(changes) > 8:
                _reject(cls.__name__)
        if data.get("committed") not in (None, False):
            _reject(cls.__name__)
        return data

    @field_validator("time_signature")
    @classmethod
    def _meter(cls, value: str) -> str:
        try:
            return normalize_time_signature(value, model_name=cls.__name__)
        except ValueError:
            _reject(cls.__name__)

    @field_validator("key")
    @classmethod
    def _key(cls, value: str) -> str:
        key = " ".join(value.strip().split())
        if not KEY_PATTERN.match(key):
            _reject(cls.__name__)
        return key

    @field_validator("agent_sequence")
    @classmethod
    def _agents(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in value:
            if item not in FILM_AGENT_IDS:
                _reject(cls.__name__)
            cleaned.append(item)
        return cleaned

    @field_validator("warnings")
    @classmethod
    def _warnings(cls, value: list[str]) -> list[str]:
        if len(value) > 32:
            _reject(cls.__name__)
        cleaned: list[str] = []
        for item in value:
            code = str(item).strip()
            if not code or len(code) > 64 or " " in code:
                _reject(cls.__name__)
            cleaned.append(code)
        return cleaned

    @model_validator(mode="after")
    def _band_and_origin(self) -> FilmScorePlanV1:
        if self.tempo_min > self.tempo_max:
            _reject(self.__class__.__name__)
        if abs(self.sync_origin.video_origin_seconds - self.music_start_seconds) > 1e-9:
            _reject(self.__class__.__name__)
        if self.music_end_seconds < self.music_start_seconds:
            _reject(self.__class__.__name__)
        return self


class FilmScorePreviewResponse(BaseModel):
    """Session preview. Commit is a later explicit write."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["film.score.preview.v1"] = FILM_SCORE_PREVIEW_SCHEMA_VERSION
    plan: FilmScorePlanV1
    candidate: dict[str, Any] | None = None
    candidate_fingerprint: str | None = None
    artifact_log: list[dict[str, Any]] = Field(default_factory=list)
    artifact_role_map: dict[str, Any]
    committed: Literal[False] = False
    recommendation: Literal["approve", "revise"] | None = None

    @model_validator(mode="before")
    @classmethod
    def _reject_playable(cls, data: Any) -> Any:
        _reject_playable(cls.__name__, data)
        return data


class FilmScoreCommitRequest(BaseModel):
    """Explicit commit. Cue rows are not part of this body."""

    model_config = ConfigDict(extra="forbid")

    candidate: dict[str, Any]
    candidate_fingerprint: str = Field(min_length=16, max_length=128)
    replace_existing: bool = False
    expected_document_revision: int = Field(ge=0)
    artifact_log: list[dict[str, Any]] = Field(default_factory=list)
    artifact_role_map: dict[str, Any]
    branch_id: str = Field(min_length=1, max_length=80)
    expected_active_branch_id: str = Field(min_length=1, max_length=80)
    expected_working_version: int = Field(ge=0)
    expected_head_revision_id: str = Field(min_length=1, max_length=80)
    expected_source_fingerprint: str = Field(min_length=16, max_length=128)

    @model_validator(mode="before")
    @classmethod
    def _reject_playable_wrapper(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("tracks") is not None and "candidate" not in data:
            _reject(cls.__name__)
        return data


def film_score_error_from_validation(exc: ValidationError, *, model: str) -> FilmScoreError:
    """Map a schema failure to ``film_score_invalid`` without echoing field values."""
    log_film_schema_rejection(model, "film_score_invalid")
    return FilmScoreError("film_score_invalid")
