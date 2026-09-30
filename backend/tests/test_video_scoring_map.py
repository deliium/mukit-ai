"""Tempo-aware video map and drop-frame timecode."""

from __future__ import annotations

import logging

import pytest

from app.services.composition_timeline import compile_timeline
from app.services.video_scoring_map import (
    format_timecode,
    map_tick_to_video,
    map_video_to_music,
    score_seconds_from_video,
    video_seconds_from_score,
)
from app.video_scoring_schemas import VideoScoringError


def _timeline():
    return compile_timeline(
        {
            "ticks_per_quarter": 480,
            "duration_ticks": 7680,
            "bar_count": 4,
            "tempo": 120,
            "time_signature": "4/4",
            "key": "C major",
            "tempo_changes": [{"tick": 3840, "bpm": 60}],
        }
    )


def _map_kwargs(timeline):
    return {
        "timeline": timeline,
        "duration_seconds": 30.0,
        "video_origin_seconds": 0.0,
        "musical_origin_tick": 0,
        "frame_rate_numerator": 24,
        "frame_rate_denominator": 1,
        "timecode_mode": "non_drop",
        "start_timecode": "00:00:00:00",
    }


def test_tempo_change_is_not_constant_bpm(caplog: pytest.LogCaptureFixture) -> None:
    timeline = _timeline()
    constant_tick = 6.0 / (60.0 / 120.0 / 480.0)
    with caplog.at_level(logging.DEBUG):
        mapped = map_video_to_music(6.0, **_map_kwargs(timeline))
    assert mapped.tick != int(constant_tick)
    assert mapped.tick == 4800
    assert any(record.message == "video scoring map" for record in caplog.records)


def test_in_range_round_trip_within_one_tick() -> None:
    timeline = _timeline()
    forward = map_video_to_music(5.5, **_map_kwargs(timeline))
    backward = map_tick_to_video(forward.tick, **_map_kwargs(timeline))
    again = map_video_to_music(backward.video_seconds, **_map_kwargs(timeline))
    assert abs(again.tick - forward.tick) <= 1
    pure_score = score_seconds_from_video(
        5.5,
        timeline=timeline,
        video_origin_seconds=0.0,
        musical_origin_tick=0,
    )
    pure_video = video_seconds_from_score(
        pure_score,
        timeline=timeline,
        video_origin_seconds=0.0,
        musical_origin_tick=0,
    )
    assert pure_video == pytest.approx(5.5)


def test_clamp_warning_codes() -> None:
    timeline = _timeline()
    video_clamped = map_video_to_music(-1.0, **_map_kwargs(timeline))
    assert "video_time_clamped" in video_clamped.warnings
    musical = map_video_to_music(100.0, **_map_kwargs(timeline))
    assert "musical_time_clamped" in musical.warnings or "video_time_clamped" in musical.warnings
    past_tick = map_tick_to_video(99_999, **_map_kwargs(timeline))
    assert "musical_time_clamped" in past_tick.warnings


def test_timecode_zero_and_drop_frame_minute() -> None:
    assert (
        format_timecode(
            0.0,
            frame_rate_numerator=24,
            frame_rate_denominator=1,
            timecode_mode="non_drop",
            start_timecode="00:00:00:00",
        )
        == "00:00:00:00"
    )
    first_dropped = 1800 * 1001 / 30000
    assert (
        format_timecode(
            first_dropped,
            frame_rate_numerator=30000,
            frame_rate_denominator=1001,
            timecode_mode="drop_frame",
            start_timecode="00:00:00:00",
        )
        == "00:01:00:02"
    )


def test_nondrop_does_not_raise_drop_frame_error() -> None:
    format_timecode(
        1.0,
        frame_rate_numerator=24,
        frame_rate_denominator=1,
        timecode_mode="non_drop",
        start_timecode="00:00:00:00",
    )


def test_missing_rate_is_required() -> None:
    timeline = _timeline()
    kwargs = _map_kwargs(timeline)
    kwargs["frame_rate_numerator"] = None
    kwargs["frame_rate_denominator"] = None
    with pytest.raises(VideoScoringError) as exc:
        map_video_to_music(1.0, **kwargs)
    assert exc.value.code == "video_frame_rate_required"
