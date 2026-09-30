"""Pure video-time ↔ musical-tick map and SMPTE timecode.

Uses ``CompiledTimeline`` for tempo changes. No SQLite, FastAPI, or file I/O.
Hit points are labels; this module does not warp time through them.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass

from app.composition_schemas import round_half_away_from_zero
from app.services.composition_timeline import CompiledTimeline
from app.video_scoring_schemas import (
    DROP_FRAME_RATE,
    NOMINAL_FRAMES,
    VideoScoringError,
    is_closed_frame_rate,
)

logger = logging.getLogger(__name__)

_TIMECODE_RE = re.compile(
    r"^(?P<hours>[0-9]{2}):(?P<minutes>[0-5][0-9]):(?P<seconds>[0-5][0-9]):(?P<frames>[0-9]{2})$"
)
_DROP_FRAMES_PER_MINUTE = 2
_NOMINAL_DF = 30


@dataclass(frozen=True)
class VideoMusicMap:
    video_seconds: float
    score_seconds: float
    tick: int
    bar: int
    timecode: str
    warnings: tuple[str, ...]


def score_seconds_from_video(
    video_seconds: float,
    *,
    timeline: CompiledTimeline,
    video_origin_seconds: float,
    musical_origin_tick: int,
) -> float:
    """Unclamped inverse. Callers that pass in-range values get the raw line."""
    origin = timeline.tick_to_seconds(int(musical_origin_tick))
    score = origin + (float(video_seconds) - float(video_origin_seconds))
    logger.debug(
        "score_seconds_from_video",
        extra={
            "video_seconds": video_seconds,
            "score_seconds": score,
            "musical_origin_tick": int(musical_origin_tick),
        },
    )
    return score


def video_seconds_from_score(
    score_seconds: float,
    *,
    timeline: CompiledTimeline,
    video_origin_seconds: float,
    musical_origin_tick: int,
) -> float:
    origin = timeline.tick_to_seconds(int(musical_origin_tick))
    video = float(video_origin_seconds) + (float(score_seconds) - origin)
    logger.debug(
        "video_seconds_from_score",
        extra={
            "score_seconds": score_seconds,
            "video_seconds": video,
            "musical_origin_tick": int(musical_origin_tick),
        },
    )
    return video


def map_video_to_music(
    video_seconds: float,
    *,
    timeline: CompiledTimeline,
    duration_seconds: float,
    video_origin_seconds: float,
    musical_origin_tick: int,
    frame_rate_numerator: int | None,
    frame_rate_denominator: int | None,
    timecode_mode: str,
    start_timecode: str,
) -> VideoMusicMap:
    _require_rate(frame_rate_numerator, frame_rate_denominator)
    warnings: list[str] = []
    video = float(video_seconds)
    if video < 0 or video > duration_seconds:
        video = min(max(video, 0.0), float(duration_seconds))
        warnings.append("video_time_clamped")
        logger.warning("video_time_clamped", extra={"duration_seconds": duration_seconds})
    score = score_seconds_from_video(
        video,
        timeline=timeline,
        video_origin_seconds=video_origin_seconds,
        musical_origin_tick=musical_origin_tick,
    )
    score, musical_warning = _clamp_score_seconds(score, timeline)
    if musical_warning:
        warnings.append(musical_warning)
    return _finish(
        video=video,
        score=score,
        timeline=timeline,
        warnings=warnings,
        frame_rate_numerator=int(frame_rate_numerator or 0),
        frame_rate_denominator=int(frame_rate_denominator or 0),
        timecode_mode=timecode_mode,
        start_timecode=start_timecode,
    )


def map_tick_to_video(
    tick: int,
    *,
    timeline: CompiledTimeline,
    duration_seconds: float,
    video_origin_seconds: float,
    musical_origin_tick: int,
    frame_rate_numerator: int | None,
    frame_rate_denominator: int | None,
    timecode_mode: str,
    start_timecode: str,
) -> VideoMusicMap:
    _require_rate(frame_rate_numerator, frame_rate_denominator)
    warnings: list[str] = []
    clamped = int(tick)
    if clamped < 0 or clamped > timeline.duration_ticks:
        clamped = min(max(clamped, 0), timeline.duration_ticks)
        warnings.append("musical_time_clamped")
        logger.warning(
            "musical_time_clamped",
            extra={"duration_ticks": timeline.duration_ticks},
        )
    score = timeline.tick_to_seconds(clamped)
    video = video_seconds_from_score(
        score,
        timeline=timeline,
        video_origin_seconds=video_origin_seconds,
        musical_origin_tick=musical_origin_tick,
    )
    if video < 0 or video > duration_seconds:
        video = min(max(video, 0.0), float(duration_seconds))
        warnings.append("video_time_clamped")
        logger.warning("video_time_clamped", extra={"duration_seconds": duration_seconds})
    return _finish(
        video=video,
        score=score,
        timeline=timeline,
        warnings=warnings,
        forced_tick=clamped,
        frame_rate_numerator=int(frame_rate_numerator or 0),
        frame_rate_denominator=int(frame_rate_denominator or 0),
        timecode_mode=timecode_mode,
        start_timecode=start_timecode,
    )


def map_bar_to_video(
    bar: int,
    *,
    timeline: CompiledTimeline,
    duration_seconds: float,
    video_origin_seconds: float,
    musical_origin_tick: int,
    frame_rate_numerator: int | None,
    frame_rate_denominator: int | None,
    timecode_mode: str,
    start_timecode: str,
) -> VideoMusicMap:
    if bar < 1 or bar > timeline.bar_count:
        raise VideoScoringError("video_map_selector_invalid")
    return map_tick_to_video(
        timeline.bar_start_tick(bar),
        timeline=timeline,
        duration_seconds=duration_seconds,
        video_origin_seconds=video_origin_seconds,
        musical_origin_tick=musical_origin_tick,
        frame_rate_numerator=frame_rate_numerator,
        frame_rate_denominator=frame_rate_denominator,
        timecode_mode=timecode_mode,
        start_timecode=start_timecode,
    )


def format_timecode(
    video_seconds: float,
    *,
    frame_rate_numerator: int,
    frame_rate_denominator: int,
    timecode_mode: str,
    start_timecode: str,
) -> str:
    if not is_closed_frame_rate(frame_rate_numerator, frame_rate_denominator):
        raise VideoScoringError("video_frame_rate_required")
    nominal = NOMINAL_FRAMES[(frame_rate_numerator, frame_rate_denominator)]
    if timecode_mode == "drop_frame" and (frame_rate_numerator, frame_rate_denominator) != DROP_FRAME_RATE:
        raise VideoScoringError("video_drop_frame_unsupported")
    frames_from_zero = math.floor(
        (float(video_seconds) * frame_rate_numerator / frame_rate_denominator) + 1e-9
    )
    if frames_from_zero < 0:
        frames_from_zero = 0
    display = frames_from_zero + parse_timecode(
        start_timecode,
        frame_rate_numerator=frame_rate_numerator,
        frame_rate_denominator=frame_rate_denominator,
        timecode_mode=timecode_mode,
    )
    if timecode_mode == "drop_frame":
        hours, minutes, seconds, frames = _frames_to_drop_frame(display)
    else:
        hours, minutes, seconds, frames = _frames_to_nondrop(display, nominal)
    text = f"{hours:02d}:{minutes:02d}:{seconds:02d}:{frames:02d}"
    logger.debug(
        "format_timecode",
        extra={"video_seconds": video_seconds, "timecode": text, "timecode_mode": timecode_mode},
    )
    return text


def cue_video_seconds(
    timecode: str,
    *,
    frame_rate_numerator: int,
    frame_rate_denominator: int,
    timecode_mode: str,
    start_timecode: str,
) -> float:
    """Unclamped picture seconds for an authored ``HH:MM:SS:FF`` address.

    A short asset does not change the result. Negative offsets from the start
    timecode are ``video_timecode_invalid``.
    """
    logger.debug(
        "cue_video_seconds start",
        extra={"timecode": timecode, "start_timecode": start_timecode, "timecode_mode": timecode_mode},
    )
    if not is_closed_frame_rate(frame_rate_numerator, frame_rate_denominator):
        raise VideoScoringError("video_frame_rate_required")
    frames_from_zero = parse_timecode(
        timecode,
        frame_rate_numerator=frame_rate_numerator,
        frame_rate_denominator=frame_rate_denominator,
        timecode_mode=timecode_mode,
    ) - parse_timecode(
        start_timecode,
        frame_rate_numerator=frame_rate_numerator,
        frame_rate_denominator=frame_rate_denominator,
        timecode_mode=timecode_mode,
    )
    if frames_from_zero < 0:
        logger.warning(
            "video_timecode_invalid",
            extra={"error_code": "video_timecode_invalid", "frames_from_zero": frames_from_zero},
        )
        raise VideoScoringError("video_timecode_invalid")
    seconds = frames_from_zero * frame_rate_denominator / frame_rate_numerator
    logger.debug(
        "cue_video_seconds",
        extra={"frames_from_zero": frames_from_zero, "video_seconds": seconds},
    )
    return seconds


def musical_tick_for_score_seconds(score_seconds: float, timeline: CompiledTimeline) -> int:
    """Tick for unclamped score seconds. Past the composition, the end tick."""
    if score_seconds < 0:
        logger.debug(
            "musical_tick_for_score_seconds",
            extra={"score_seconds": score_seconds, "musical_tick": 0, "outside": "before"},
        )
        return 0
    total = timeline.total_duration_seconds()
    if score_seconds >= total:
        logger.debug(
            "musical_tick_for_score_seconds",
            extra={
                "score_seconds": score_seconds,
                "musical_tick": timeline.duration_ticks,
                "outside": "after",
            },
        )
        return int(timeline.duration_ticks)
    tick = round_half_away_from_zero(timeline.seconds_to_tick(score_seconds))
    logger.debug(
        "musical_tick_for_score_seconds",
        extra={"score_seconds": score_seconds, "musical_tick": tick, "outside": None},
    )
    return tick


def parse_timecode(
    text: str,
    *,
    frame_rate_numerator: int,
    frame_rate_denominator: int,
    timecode_mode: str,
) -> int:
    """Return the frame count that ``format_timecode`` uses as ``frames(start_timecode)``."""
    if not is_closed_frame_rate(frame_rate_numerator, frame_rate_denominator):
        raise VideoScoringError("video_frame_rate_required")
    match = _TIMECODE_RE.fullmatch(text)
    if match is None:
        raise VideoScoringError("video_timecode_invalid")
    hours = int(match.group("hours"))
    minutes = int(match.group("minutes"))
    seconds = int(match.group("seconds"))
    frames = int(match.group("frames"))
    nominal = NOMINAL_FRAMES[(frame_rate_numerator, frame_rate_denominator)]
    if hours > 23 or frames >= nominal:
        raise VideoScoringError("video_timecode_invalid")
    if timecode_mode == "drop_frame":
        if (frame_rate_numerator, frame_rate_denominator) != DROP_FRAME_RATE:
            raise VideoScoringError("video_drop_frame_unsupported")
        total_minutes = hours * 60 + minutes
        nominal_frames = ((hours * 3600 + minutes * 60 + seconds) * _NOMINAL_DF) + frames
        dropped = _DROP_FRAMES_PER_MINUTE * (total_minutes - (total_minutes // 10))
        return nominal_frames - dropped
    return ((hours * 3600 + minutes * 60 + seconds) * nominal) + frames


def _frames_to_nondrop(frame_count: int, nominal: int) -> tuple[int, int, int, int]:
    frames = frame_count % nominal
    rest = frame_count // nominal
    seconds = rest % 60
    rest //= 60
    minutes = rest % 60
    hours = rest // 60
    return hours, minutes, seconds, frames


def _frames_to_drop_frame(frame_count: int) -> tuple[int, int, int, int]:
    """Total-frame drop-frame display for 30 nominal. Skips :00 and :01 except every tenth minute."""
    frames_per_10_minutes = 17982
    frames_per_minute_drop = 1798
    ten_min = frame_count // frames_per_10_minutes
    remainder = frame_count % frames_per_10_minutes
    if remainder < 1800:
        minute_in_chunk = 0
        frame_in_minute = remainder
    else:
        adjusted = remainder - 1800
        minute_in_chunk = 1 + (adjusted // frames_per_minute_drop)
        frame_in_minute = (adjusted % frames_per_minute_drop) + _DROP_FRAMES_PER_MINUTE
    total_minutes = ten_min * 10 + minute_in_chunk
    hours = total_minutes // 60
    minutes = total_minutes % 60
    seconds = frame_in_minute // _NOMINAL_DF
    frames = frame_in_minute % _NOMINAL_DF
    return hours, minutes, seconds, frames


def _require_rate(numerator: int | None, denominator: int | None) -> None:
    if numerator is None or denominator is None or not is_closed_frame_rate(numerator, denominator):
        raise VideoScoringError("video_frame_rate_required")


def _clamp_score_seconds(score: float, timeline: CompiledTimeline) -> tuple[float, str | None]:
    total = timeline.total_duration_seconds()
    if score < 0:
        logger.warning("musical_time_clamped", extra={"duration_ticks": timeline.duration_ticks})
        return 0.0, "musical_time_clamped"
    if score > total:
        logger.warning("musical_time_clamped", extra={"duration_ticks": timeline.duration_ticks})
        return total, "musical_time_clamped"
    return score, None


def _finish(
    *,
    video: float,
    score: float,
    timeline: CompiledTimeline,
    warnings: list[str],
    frame_rate_numerator: int,
    frame_rate_denominator: int,
    timecode_mode: str,
    start_timecode: str,
    forced_tick: int | None = None,
) -> VideoMusicMap:
    if forced_tick is None:
        raw = timeline.seconds_to_tick(score)
        tick = round_half_away_from_zero(raw)
        if tick < 0 or tick > timeline.duration_ticks:
            tick = min(max(tick, 0), timeline.duration_ticks)
            if "musical_time_clamped" not in warnings:
                warnings.append("musical_time_clamped")
    else:
        tick = forced_tick
    bar = timeline.bar_at_tick(tick)
    timecode = format_timecode(
        video,
        frame_rate_numerator=frame_rate_numerator,
        frame_rate_denominator=frame_rate_denominator,
        timecode_mode=timecode_mode,
        start_timecode=start_timecode,
    )
    result = VideoMusicMap(
        video_seconds=video,
        score_seconds=score,
        tick=tick,
        bar=bar,
        timecode=timecode,
        warnings=tuple(warnings),
    )
    logger.debug(
        "video scoring map",
        extra={
            "video_seconds": result.video_seconds,
            "tick": result.tick,
            "bar": result.bar,
            "timecode": result.timecode,
            "warning_count": len(result.warnings),
        },
    )
    return result
