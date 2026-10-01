"""Non-playable ``film.score.adaptation.v1`` and the preview request.

The proposal is an inspectable repair plan. Playable notes stay on
``composition.v2`` ``tracks[].events[]``. Cue instructions and labels are not
fields on these models. This module does not import video schemas.
"""

from __future__ import annotations

import logging
import math
from typing import Annotated, Any, Literal, NoReturn, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.film_score_schemas import FORBIDDEN_PLAYABLE_TOP_LEVEL, FilmCueSnapshot

logger = logging.getLogger(__name__)

FILM_ADAPT_SCHEMA_VERSION: Literal["film.score.adaptation.v1"] = "film.score.adaptation.v1"
FILM_ADAPT_PREVIEW_SCHEMA_VERSION: Literal["film.score.adaptation.preview.v1"] = (
    "film.score.adaptation.preview.v1"
)

FILM_ADAPT_STRATEGIES: tuple[str, ...] = (
    "unchanged",
    "local_tempo",
    "transition_shorten",
    "transition_extend",
    "phrase_contract",
    "phrase_extend",
    "bar_remove",
    "bar_insert",
    "silence_insert",
    "targeted_regenerate",
)
FILM_ADAPT_REASONS: tuple[str, ...] = (
    "picture_shorten",
    "picture_extend",
    "hit_move",
    "silent_gap",
)
FILM_ADAPT_CHANGES: tuple[str, ...] = ("unchanged", "moved", "added", "removed")
FILM_ADAPT_STATUSES: tuple[str, ...] = ("aligned", "soft", "unsatisfiable", "boundary")
FILM_ADAPT_WARNING_CODES: tuple[str, ...] = (
    "hit_unsatisfiable",
    "climax_region_edited",
    "motif_occurrence_trimmed",
    "melody_protected",
    "tempo_restored",
    "note_truncated",
    "film_origin_unchanged",
)
FILM_ADAPT_KINDS: tuple[str, ...] = ("delete_span", "insert_span", "move_hit")

FILM_ADAPT_ERROR_MESSAGES: dict[str, str] = {
    "film_adapt_invalid": "Film score adaptation failed validation.",
    "film_adapt_score_empty": "The working score has no note events.",
    "film_adapt_span_too_large": "The picture edit covers too much of the score to repair locally.",
    "film_adapt_frame_rate_required": "Film adaptation requires a closed frame rate.",
    "film_adapt_asset_missing": "This project has no video asset.",
    "film_adapt_timeline_mismatch": "The edit list does not explain the stored picture.",
    "film_adapt_conflict": "Film score adaptation commit does not match the preview.",
}

_HTTP_STATUS: dict[str, int] = {
    "film_adapt_asset_missing": 404,
    "film_adapt_timeline_mismatch": 409,
    "film_adapt_conflict": 409,
}

FilmAdaptStrategy = Literal[
    "unchanged",
    "local_tempo",
    "transition_shorten",
    "transition_extend",
    "phrase_contract",
    "phrase_extend",
    "bar_remove",
    "bar_insert",
    "silence_insert",
    "targeted_regenerate",
]
FilmAdaptReason = Literal["picture_shorten", "picture_extend", "hit_move", "silent_gap"]
FilmAdaptChange = Literal["unchanged", "moved", "added", "removed"]
FilmAdaptStatus = Literal["aligned", "soft", "unsatisfiable", "boundary"]
FilmAdaptWarning = Literal[
    "hit_unsatisfiable",
    "climax_region_edited",
    "motif_occurrence_trimmed",
    "melody_protected",
    "tempo_restored",
    "note_truncated",
    "film_origin_unchanged",
]


class FilmAdaptError(Exception):
    """Domain error mapped to HTTP by the film-score router. ``candidate`` stays null."""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        self.message = message or FILM_ADAPT_ERROR_MESSAGES.get(code, code)
        self.http_status = _HTTP_STATUS.get(code, 422)
        self.candidate = None
        super().__init__(self.message)


def log_film_adapt_schema_rejection(model: str, code: str) -> None:
    """DEBUG a schema rejection. Cue labels and note fields are never included."""
    logger.debug(
        "film adapt model rejected model=%s error_code=%s",
        model,
        code,
    )


def _reject(model: str, code: str = "film_adapt_invalid") -> NoReturn:
    log_film_adapt_schema_rejection(model, code)
    raise ValueError(code)


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _reject_playable(model: str, data: Any) -> None:
    if not isinstance(data, dict):
        return
    forbidden = sorted(FORBIDDEN_PLAYABLE_TOP_LEVEL.intersection(data.keys()))
    if forbidden:
        log_film_adapt_schema_rejection(model, "film_adapt_invalid")
        raise ValueError("film_adapt_invalid")


class FilmTimelineSnapshot(BaseModel):
    """Request-only picture baseline. It is not stored on the scoring document."""

    model_config = ConfigDict(extra="forbid")

    duration_seconds: float = Field(gt=0, le=3600)
    frame_rate_numerator: int = Field(ge=1)
    frame_rate_denominator: int = Field(ge=1)
    video_origin_seconds: float = Field(ge=0)
    musical_origin_tick: int = Field(ge=0)
    cues: list[FilmCueSnapshot] = Field(default_factory=list, max_length=64)

    @field_validator("duration_seconds", "video_origin_seconds")
    @classmethod
    def _finite_time(cls, value: float) -> float:
        if not _finite(value) or float(value) < 0:
            _reject(cls.__name__)
        if float(value) == 0 and value == 0:
            return 0.0
        return float(value)

    @field_validator("frame_rate_numerator", "frame_rate_denominator", "musical_origin_tick")
    @classmethod
    def _positive_ints(cls, value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            _reject(cls.__name__)
        return value

    @model_validator(mode="after")
    def _rate_and_duration(self) -> FilmTimelineSnapshot:
        if self.frame_rate_numerator < 1 or self.frame_rate_denominator < 1:
            _reject(self.__class__.__name__)
        if self.duration_seconds <= 0 or self.duration_seconds > 3600:
            _reject(self.__class__.__name__)
        seen: set[str] = set()
        for cue in self.cues:
            if cue.id in seen:
                _reject(self.__class__.__name__)
            seen.add(cue.id)
        return self


class _EditBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op_id: str = Field(pattern=r"^edit_[0-9a-f]{8}$")


class DeleteSpanEdit(_EditBase):
    kind: Literal["delete_span"]
    start_seconds: float
    end_seconds: float

    @model_validator(mode="after")
    def _span(self) -> DeleteSpanEdit:
        if not _finite(self.start_seconds) or not _finite(self.end_seconds):
            _reject(self.__class__.__name__)
        if self.start_seconds < 0 or self.end_seconds < 0 or self.end_seconds <= self.start_seconds:
            _reject(self.__class__.__name__)
        return self


class InsertSpanEdit(_EditBase):
    kind: Literal["insert_span"]
    at_seconds: float = Field(ge=0)
    duration_seconds: float = Field(gt=0, le=600)

    @model_validator(mode="after")
    def _span(self) -> InsertSpanEdit:
        if not _finite(self.at_seconds) or not _finite(self.duration_seconds):
            _reject(self.__class__.__name__)
        if self.at_seconds < 0 or self.duration_seconds <= 0 or self.duration_seconds > 600:
            _reject(self.__class__.__name__)
        return self


class MoveHitEdit(_EditBase):
    kind: Literal["move_hit"]
    cue_id: str = Field(pattern=r"^hit_[0-9a-f]{8}$")
    from_seconds: float = Field(ge=0)
    to_seconds: float = Field(ge=0)

    @model_validator(mode="after")
    def _span(self) -> MoveHitEdit:
        if not _finite(self.from_seconds) or not _finite(self.to_seconds):
            _reject(self.__class__.__name__)
        if self.from_seconds < 0 or self.to_seconds < 0:
            _reject(self.__class__.__name__)
        return self


FilmPictureEdit = Annotated[
    Union[DeleteSpanEdit, InsertSpanEdit, MoveHitEdit],
    Field(discriminator="kind"),
]


class FilmScoreAdaptPreviewRequest(BaseModel):
    """Explicit adaptation preview. Opening the Agents tab does not send this."""

    model_config = ConfigDict(extra="forbid")

    previous: FilmTimelineSnapshot
    edits: list[FilmPictureEdit] = Field(min_length=1, max_length=16)
    expected_source_fingerprint: str = Field(min_length=1, max_length=128)

    @model_validator(mode="before")
    @classmethod
    def _reject_generation_fields(cls, data: Any) -> Any:
        _reject_playable(cls.__name__, data)
        if isinstance(data, dict) and (
            "replace_existing" in data or "brief" in data or "instruments" in data
        ):
            _reject(cls.__name__)
        return data


class FilmAdaptOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op_id: str = Field(pattern=r"^edit_[0-9a-f]{8}$")
    kind: Literal["delete_span", "insert_span", "move_hit"]
    strategy: FilmAdaptStrategy
    start_bar: int = Field(ge=1)
    end_bar: int = Field(ge=1)
    bars_delta: int
    tempo_bpm: int | None = Field(default=None, ge=40, le=240)
    section_id: str = Field(min_length=1, max_length=120)
    reason_code: FilmAdaptReason

    @field_validator("strategy", mode="before")
    @classmethod
    def _strategy(cls, value: object) -> object:
        if value not in FILM_ADAPT_STRATEGIES:
            _reject(cls.__name__)
        return value

    @field_validator("reason_code", mode="before")
    @classmethod
    def _reason(cls, value: object) -> object:
        if value not in FILM_ADAPT_REASONS:
            _reject(cls.__name__)
        return value

    @model_validator(mode="after")
    def _bars(self) -> FilmAdaptOperation:
        if self.end_bar < self.start_bar:
            _reject(self.__class__.__name__)
        return self


class FilmAdaptHitChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cue_id: str = Field(pattern=r"^hit_[0-9a-f]{8}$")
    change: FilmAdaptChange
    previous_seconds: float | None = None
    next_seconds: float | None = None
    status: FilmAdaptStatus

    @field_validator("change", mode="before")
    @classmethod
    def _change(cls, value: object) -> object:
        if value not in FILM_ADAPT_CHANGES:
            _reject(cls.__name__)
        return value

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value: object) -> object:
        if value not in FILM_ADAPT_STATUSES:
            _reject(cls.__name__)
        return value

    @field_validator("previous_seconds", "next_seconds")
    @classmethod
    def _seconds(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if not _finite(value) or float(value) < 0:
            _reject(cls.__name__)
        return float(value)


class FilmAdaptPreserved(BaseModel):
    model_config = ConfigDict(extra="forbid")

    motifs: bool
    melodies: bool
    harmony: bool
    climax: bool
    instrumentation: bool


class FilmAdaptCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events_unchanged: int = Field(ge=0)
    events_shifted: int = Field(ge=0)
    events_removed: int = Field(ge=0)
    events_added: int = Field(ge=0)


class FilmScoreAdaptationV1(BaseModel):
    """Inspectable adaptation proposal. ``committed`` is always false."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["film.score.adaptation.v1"] = FILM_ADAPT_SCHEMA_VERSION
    project_id: str = Field(min_length=1, max_length=128)
    source_fingerprint: str = Field(min_length=1, max_length=128)
    scoring_document_revision: int = Field(ge=0)
    previous_timeline_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    operations: list[FilmAdaptOperation] = Field(min_length=1, max_length=16)
    hit_changes: list[FilmAdaptHitChange] = Field(max_length=64)
    preserved: FilmAdaptPreserved
    counts: FilmAdaptCounts
    warnings: list[FilmAdaptWarning] = Field(default_factory=list, max_length=32)
    committed: Literal[False] = False

    @field_validator("warnings")
    @classmethod
    def _warnings(cls, value: list[str]) -> list[str]:
        for code in value:
            if code not in FILM_ADAPT_WARNING_CODES:
                _reject(cls.__name__)
        return value

    @model_validator(mode="before")
    @classmethod
    def _reject_shape(cls, data: Any) -> Any:
        _reject_playable(cls.__name__, data)
        if isinstance(data, dict) and data.get("committed") not in (None, False):
            _reject(cls.__name__)
        return data


class FilmScoreAdaptPreviewV1(BaseModel):
    """Session preview. ``candidate`` is null when the edit is refused."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["film.score.adaptation.preview.v1"] = FILM_ADAPT_PREVIEW_SCHEMA_VERSION
    proposal: FilmScoreAdaptationV1
    candidate: dict[str, Any] | None = None
    candidate_fingerprint: str | None = Field(default=None, min_length=16, max_length=128)
    committed: Literal[False] = False

    @model_validator(mode="before")
    @classmethod
    def _committed_false(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("committed") not in (None, False):
            _reject(cls.__name__)
        return data

    @model_validator(mode="after")
    def _fingerprint_pair(self) -> FilmScoreAdaptPreviewV1:
        if self.candidate is None and self.candidate_fingerprint is not None:
            _reject(self.__class__.__name__)
        if self.candidate is not None and not self.candidate_fingerprint:
            _reject(self.__class__.__name__)
        return self


class FilmScoreAdaptCommitRequest(BaseModel):
    """Explicit commit. The service recomputes the candidate. Cue rows are not sent."""

    model_config = ConfigDict(extra="forbid")

    previous: FilmTimelineSnapshot
    edits: list[FilmPictureEdit] = Field(min_length=1, max_length=16)
    candidate: dict[str, Any]
    candidate_fingerprint: str = Field(min_length=16, max_length=128)
    expected_source_fingerprint: str = Field(min_length=1, max_length=128)
    expected_document_revision: int = Field(ge=0)
    branch_id: str = Field(min_length=1, max_length=80)
    expected_active_branch_id: str = Field(min_length=1, max_length=80)
    expected_working_version: int = Field(ge=0)
    expected_head_revision_id: str = Field(min_length=1, max_length=80)

    @model_validator(mode="before")
    @classmethod
    def _reject_generation_fields(cls, data: Any) -> Any:
        if isinstance(data, dict) and (
            "replace_existing" in data or "brief" in data or "artifact_role_map" in data
        ):
            _reject(cls.__name__)
        return data


def film_adapt_error_from_validation(exc: ValidationError, *, model: str) -> FilmAdaptError:
    """Map a schema failure to ``film_adapt_invalid`` without echoing field values."""
    log_film_adapt_schema_rejection(model, "film_adapt_invalid")
    return FilmAdaptError("film_adapt_invalid")
