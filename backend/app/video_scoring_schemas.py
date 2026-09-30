"""Strict ``video.asset.v1`` and ``video.scoring.v1`` documents.

The asset is a probe snapshot of one immutable picture file. The scoring
document holds the explicit frame rate, start timecode, sync origin, and hit
points. Neither document is ``composition.v5`` and neither stores note events.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

logger = logging.getLogger(__name__)

VIDEO_ASSET_SCHEMA_VERSION: Literal["video.asset.v1"] = "video.asset.v1"
VIDEO_SCORING_SCHEMA_VERSION: Literal["video.scoring.v1"] = "video.scoring.v1"

_ASSET_ID_RE = re.compile(r"^vid_[0-9a-f]{8}$")
_HIT_ID_RE = re.compile(r"^hit_[0-9a-f]{8}$")
_TIMECODE_RE = re.compile(
    r"^(?P<hours>[0-9]{2}):(?P<minutes>[0-5][0-9]):(?P<seconds>[0-5][0-9]):(?P<frames>[0-9]{2})$"
)

CLOSED_FRAME_RATES: tuple[tuple[int, int], ...] = (
    (24, 1),
    (25, 1),
    (30, 1),
    (24000, 1001),
    (30000, 1001),
)
DROP_FRAME_RATE: tuple[int, int] = (30000, 1001)
NOMINAL_FRAMES: dict[tuple[int, int], int] = {
    (24, 1): 24,
    (25, 1): 25,
    (30, 1): 30,
    (24000, 1001): 24,
    (30000, 1001): 30,
}
MAX_HIT_POINTS = 64

VideoContainer = Literal["mp4", "mov"]
VideoContentType = Literal["video/mp4", "video/quicktime"]
FrameRateSource = Literal["probed", "explicit"]
TimecodeMode = Literal["non_drop", "drop_frame"]

VideoScoringErrorCode = Literal[
    "video_scoring_invalid",
    "video_frame_rate_unsupported",
    "video_frame_rate_incomplete",
    "video_frame_rate_required",
    "video_drop_frame_unsupported",
    "video_timecode_invalid",
    "video_container_unsupported",
    "video_moov_not_found",
    "video_missing_video_track",
    "video_duration_invalid",
    "video_resolution_invalid",
    "video_asset_missing",
    "video_scoring_conflict",
    "video_upload_too_large",
    "video_map_selector_invalid",
    "video_composition_missing",
    "video_musical_origin_outside",
    "video_time_clamped",
    "musical_time_clamped",
]

VIDEO_SCORING_ERROR_MESSAGES: dict[str, str] = {
    "video_scoring_invalid": "Video scoring document failed validation.",
    "video_frame_rate_unsupported": "Frame rate is not one of the closed scoring rates.",
    "video_frame_rate_incomplete": "Frame rate numerator, denominator, and source must be set together.",
    "video_frame_rate_required": "Timecode requires a closed frame rate.",
    "video_drop_frame_unsupported": "Drop-frame timecode requires 30000/1001.",
    "video_timecode_invalid": "Start timecode is not HH:MM:SS:FF for the frame rate.",
    "video_container_unsupported": "Video container brand is not a supported MP4 or MOV.",
    "video_moov_not_found": "ISO-BMFF size chain did not yield a readable moov box.",
    "video_missing_video_track": "Container has no video track.",
    "video_duration_invalid": "Video duration is missing or not a positive finite number.",
    "video_resolution_invalid": "Video width or height is missing.",
    "video_asset_missing": "This project has no video asset.",
    "video_scoring_conflict": "Video scoring revision does not match.",
    "video_upload_too_large": "Video upload exceeds the configured byte limit.",
    "video_map_selector_invalid": "Map query requires exactly one of video_seconds, tick, or bar.",
    "video_composition_missing": "Project has no composition timeline to map.",
    "video_musical_origin_outside": "Musical origin tick is outside the composition.",
    "video_time_clamped": "Video time was clamped to the asset duration.",
    "musical_time_clamped": "Musical time was clamped to the composition.",
}

_HTTP_STATUS: dict[str, int] = {
    "video_asset_missing": 404,
    "video_scoring_conflict": 409,
    "video_upload_too_large": 413,
}


def log_video_schema_rejection(model: str, code: str) -> None:
    """DEBUG a schema rejection. The code is logged; file bytes are not."""
    logger.debug(
        "video scoring model rejected",
        extra={"model": model, "error_code": code},
    )


class VideoScoringError(Exception):
    """Domain error mapped to HTTP by the video scoring router."""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        self.message = message or VIDEO_SCORING_ERROR_MESSAGES.get(code, code)
        self.http_status = _HTTP_STATUS.get(code, 422)
        super().__init__(self.message)


def nominal_frames_for(numerator: int, denominator: int) -> int | None:
    return NOMINAL_FRAMES.get((numerator, denominator))


def is_closed_frame_rate(numerator: int, denominator: int) -> bool:
    return (numerator, denominator) in CLOSED_FRAME_RATES


def _reject(model: str, code: str) -> None:
    log_video_schema_rejection(model, code)
    raise ValueError(code)


def _finite(value: float) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


class HitPointV1(BaseModel):
    """A labeled video-time and musical-tick pair. It is not a composition marker."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^hit_[0-9a-f]{8}$")
    label: str = Field(min_length=1, max_length=80)
    video_seconds: float = Field(ge=0)
    musical_tick: int = Field(ge=0)

    @field_validator("video_seconds")
    @classmethod
    def _finite_video_seconds(cls, value: float) -> float:
        if not _finite(value):
            _reject(cls.__name__, "video_scoring_invalid")
        return float(value)


class VideoAssetV1(BaseModel):
    """Probe snapshot plus storage identity. The stored bytes are never rewritten."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["video.asset.v1"] = VIDEO_ASSET_SCHEMA_VERSION
    asset_id: str = Field(pattern=r"^vid_[0-9a-f]{8}$")
    project_id: str = Field(min_length=1, max_length=128)
    container: VideoContainer
    content_type: VideoContentType
    byte_size: int = Field(ge=1)
    sha256_prefix: str = Field(pattern=r"^[0-9a-f]{16}$")
    duration_seconds: float = Field(gt=0)
    frame_rate_numerator: int = Field(gt=0)
    frame_rate_denominator: int = Field(gt=0)
    has_audio: bool
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    created_at: str = Field(min_length=1, max_length=64)

    @field_validator("duration_seconds")
    @classmethod
    def _finite_duration(cls, value: float) -> float:
        if not _finite(value) or float(value) <= 0:
            _reject(cls.__name__, "video_duration_invalid")
        return float(value)

    @model_validator(mode="after")
    def _content_type_matches_container(self) -> VideoAssetV1:
        expected = "video/quicktime" if self.container == "mov" else "video/mp4"
        if self.content_type != expected:
            _reject(self.__class__.__name__, "video_scoring_invalid")
        return self


class VideoScoringV1(BaseModel):
    """Editable sync document. ``document_revision`` 0 is the GET default with no row."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["video.scoring.v1"] = VIDEO_SCORING_SCHEMA_VERSION
    project_id: str = Field(min_length=1, max_length=128)
    asset_id: str | None = Field(default=None, pattern=r"^vid_[0-9a-f]{8}$")
    frame_rate_numerator: int | None = Field(default=None, gt=0)
    frame_rate_denominator: int | None = Field(default=None, gt=0)
    frame_rate_source: FrameRateSource | None = None
    timecode_mode: TimecodeMode = "non_drop"
    start_timecode: str = "00:00:00:00"
    video_origin_seconds: float = Field(default=0, ge=0)
    musical_origin_tick: int = Field(default=0, ge=0)
    hit_points: list[HitPointV1] = Field(default_factory=list, max_length=MAX_HIT_POINTS)
    document_revision: int = Field(default=0, ge=0)

    @field_validator("start_timecode")
    @classmethod
    def _timecode_shape(cls, value: str) -> str:
        if _TIMECODE_RE.fullmatch(value) is None:
            _reject(cls.__name__, "video_timecode_invalid")
        return value

    @field_validator("video_origin_seconds")
    @classmethod
    def _finite_origin(cls, value: float) -> float:
        if not _finite(value) or float(value) < 0:
            _reject(cls.__name__, "video_scoring_invalid")
        return float(value)

    @field_validator("hit_points")
    @classmethod
    def _unique_hit_ids(cls, points: list[HitPointV1]) -> list[HitPointV1]:
        seen: set[str] = set()
        for point in points:
            if point.id in seen:
                _reject(cls.__name__, "video_scoring_invalid")
            seen.add(point.id)
        return points

    @model_validator(mode="after")
    def _frame_rate_and_drop(self) -> VideoScoringV1:
        rate = (self.frame_rate_numerator, self.frame_rate_denominator, self.frame_rate_source)
        if rate == (None, None, None):
            if self.timecode_mode == "drop_frame":
                _reject(self.__class__.__name__, "video_drop_frame_unsupported")
            return self
        if any(part is None for part in rate):
            _reject(self.__class__.__name__, "video_frame_rate_incomplete")
        numerator = int(self.frame_rate_numerator or 0)
        denominator = int(self.frame_rate_denominator or 0)
        if not is_closed_frame_rate(numerator, denominator):
            _reject(self.__class__.__name__, "video_frame_rate_unsupported")
        if self.timecode_mode == "drop_frame" and (numerator, denominator) != DROP_FRAME_RATE:
            _reject(self.__class__.__name__, "video_drop_frame_unsupported")
        match = _TIMECODE_RE.fullmatch(self.start_timecode)
        if match is None:
            _reject(self.__class__.__name__, "video_timecode_invalid")
        frames = int(match.group("frames"))
        nominal = nominal_frames_for(numerator, denominator) or 0
        if frames >= nominal:
            _reject(self.__class__.__name__, "video_timecode_invalid")
        hours = int(match.group("hours"))
        if hours > 23:
            _reject(self.__class__.__name__, "video_timecode_invalid")
        return self


class VideoScoringPersistedV1(VideoScoringV1):
    """A stored row. Revision starts at 1."""

    document_revision: int = Field(ge=1)


class VideoScoringUpdateV1(BaseModel):
    """PUT body. The server keeps ``asset_id`` and assigns the next revision."""

    model_config = ConfigDict(extra="forbid")

    expected_document_revision: int = Field(ge=0)
    frame_rate_numerator: int | None = Field(default=None, gt=0)
    frame_rate_denominator: int | None = Field(default=None, gt=0)
    frame_rate_source: FrameRateSource | None = None
    timecode_mode: TimecodeMode = "non_drop"
    start_timecode: str = "00:00:00:00"
    video_origin_seconds: float = Field(default=0, ge=0)
    musical_origin_tick: int = Field(default=0, ge=0)
    hit_points: list[HitPointV1] = Field(default_factory=list, max_length=MAX_HIT_POINTS)

    @field_validator("start_timecode")
    @classmethod
    def _timecode_shape(cls, value: str) -> str:
        if _TIMECODE_RE.fullmatch(value) is None:
            _reject(cls.__name__, "video_timecode_invalid")
        return value

    @field_validator("video_origin_seconds")
    @classmethod
    def _finite_origin(cls, value: float) -> float:
        if not _finite(value) or float(value) < 0:
            _reject(cls.__name__, "video_scoring_invalid")
        return float(value)

    @field_validator("hit_points")
    @classmethod
    def _unique_hit_ids(cls, points: list[HitPointV1]) -> list[HitPointV1]:
        seen: set[str] = set()
        for point in points:
            if point.id in seen:
                _reject(cls.__name__, "video_scoring_invalid")
            seen.add(point.id)
        return points

    @model_validator(mode="after")
    def _frame_rate_and_drop(self) -> VideoScoringUpdateV1:
        rate = (self.frame_rate_numerator, self.frame_rate_denominator, self.frame_rate_source)
        if rate == (None, None, None):
            if self.timecode_mode == "drop_frame":
                _reject(self.__class__.__name__, "video_drop_frame_unsupported")
            return self
        if any(part is None for part in rate):
            _reject(self.__class__.__name__, "video_frame_rate_incomplete")
        numerator = int(self.frame_rate_numerator or 0)
        denominator = int(self.frame_rate_denominator or 0)
        if not is_closed_frame_rate(numerator, denominator):
            _reject(self.__class__.__name__, "video_frame_rate_unsupported")
        if self.timecode_mode == "drop_frame" and (numerator, denominator) != DROP_FRAME_RATE:
            _reject(self.__class__.__name__, "video_drop_frame_unsupported")
        match = _TIMECODE_RE.fullmatch(self.start_timecode)
        if match is None:
            _reject(self.__class__.__name__, "video_timecode_invalid")
        if int(match.group("frames")) >= (nominal_frames_for(numerator, denominator) or 0):
            _reject(self.__class__.__name__, "video_timecode_invalid")
        return self


class VideoAssetUploadResponseV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset: VideoAssetV1
    scoring: VideoScoringV1


class VideoScoringMapResponseV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    video_seconds: float
    tick: int = Field(ge=0)
    bar: int = Field(ge=1)
    timecode: str
    warnings: list[str] = Field(default_factory=list)


def default_video_scoring(project_id: str) -> VideoScoringV1:
    """GET defaults. Revision 0 means no row exists and nothing is inserted."""
    return VideoScoringV1(
        project_id=project_id,
        asset_id=None,
        frame_rate_numerator=None,
        frame_rate_denominator=None,
        frame_rate_source=None,
        timecode_mode="non_drop",
        start_timecode="00:00:00:00",
        video_origin_seconds=0,
        musical_origin_tick=0,
        hit_points=[],
        document_revision=0,
    )


def scoring_from_update(
    project_id: str,
    update: VideoScoringUpdateV1,
    *,
    asset_id: str | None,
    document_revision: int,
) -> VideoScoringV1:
    return VideoScoringV1.model_validate(
        {
            "project_id": project_id,
            "asset_id": asset_id,
            "frame_rate_numerator": update.frame_rate_numerator,
            "frame_rate_denominator": update.frame_rate_denominator,
            "frame_rate_source": update.frame_rate_source,
            "timecode_mode": update.timecode_mode,
            "start_timecode": update.start_timecode,
            "video_origin_seconds": update.video_origin_seconds,
            "musical_origin_tick": update.musical_origin_tick,
            "hit_points": [point.model_dump() for point in update.hit_points],
            "document_revision": document_revision,
        }
    )


def parse_video_scoring(payload: dict[str, Any], *, persisted: bool) -> VideoScoringV1:
    model = VideoScoringPersistedV1 if persisted else VideoScoringV1
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        code = _validation_code(exc)
        log_video_schema_rejection(model.__name__, code)
        raise VideoScoringError(code, VIDEO_SCORING_ERROR_MESSAGES.get(code, code)) from exc


def parse_video_scoring_update(payload: dict[str, Any]) -> VideoScoringUpdateV1:
    try:
        return VideoScoringUpdateV1.model_validate(payload)
    except ValidationError as exc:
        code = _validation_code(exc)
        log_video_schema_rejection(VideoScoringUpdateV1.__name__, code)
        raise VideoScoringError(code, VIDEO_SCORING_ERROR_MESSAGES.get(code, code)) from exc


def _validation_code(exc: ValidationError) -> str:
    for error in exc.errors():
        message = str(error.get("msg") or "")
        for code in VIDEO_SCORING_ERROR_MESSAGES:
            if code in message:
                return code
        context = error.get("ctx") or {}
        error_value = context.get("error")
        if error_value is not None:
            text = str(error_value)
            for code in VIDEO_SCORING_ERROR_MESSAGES:
                if code in text:
                    return code
    return "video_scoring_invalid"


def assert_asset_id(value: str) -> str:
    if _ASSET_ID_RE.fullmatch(value) is None:
        raise VideoScoringError("video_scoring_invalid")
    return value


def assert_hit_id(value: str) -> str:
    if _HIT_ID_RE.fullmatch(value) is None:
        raise VideoScoringError("video_scoring_invalid")
    return value
