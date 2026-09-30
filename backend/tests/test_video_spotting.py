"""Landing check for authored spotting cues."""

from __future__ import annotations

import logging
import math

import pytest

from app.composition_schemas import round_half_away_from_zero
from app.services.composition_timeline import compile_timeline
from app.services.video_scoring_map import cue_video_seconds, parse_timecode
from app.services.video_spotting import cue_frame_index, verify_cue_landings
from app.video_scoring_schemas import HitPointV1


def _long_timeline(*, tempo_change: bool = False):
    bar_ticks = 1920
    bar_count = 112
    duration_ticks = bar_count * bar_ticks
    body = {
        "ticks_per_quarter": 480,
        "duration_ticks": duration_ticks,
        "bar_count": bar_count,
        "tempo": 120,
        "time_signature": "4/4",
        "key": "C major",
    }
    if tempo_change:
        body["bar_count"] = 83
        body["duration_ticks"] = 83 * bar_ticks
        body["tempo_changes"] = [{"tick": 96000, "bpm": 60}]
    return compile_timeline(body)


def _cue(**overrides) -> HitPointV1:
    payload = {
        "id": "hit_0123abcd",
        "label": "Door",
        "timecode": "00:03:42:12",
        "video_seconds": 0.0,
        "musical_tick": 0,
        "tolerance_frames": 0,
    }
    payload.update(overrides)
    return HitPointV1.model_validate(payload)


def _composition(timeline, start_seconds: float, *, duration_ticks: int = 120, pitch: int = 60, tracks=None):
    if tracks is not None:
        return {"tracks": tracks}
    start_tick = round_half_away_from_zero(timeline.seconds_to_tick(start_seconds))
    return {
        "tracks": [
            {
                "events": [
                    {
                        "type": "note",
                        "start_tick": start_tick,
                        "duration_ticks": duration_ticks,
                        "pitch": pitch,
                    }
                ]
            }
        ]
    }


def _kwargs(timeline, *, duration_seconds: float = 400.0, rate=(24, 1), mode="non_drop", start="00:00:00:00"):
    return {
        "timeline": timeline,
        "duration_seconds": duration_seconds,
        "frame_rate_numerator": rate[0],
        "frame_rate_denominator": rate[1],
        "timecode_mode": mode,
        "start_timecode": start,
        "video_origin_seconds": 0.0,
        "musical_origin_tick": 0,
    }


def test_attack_on_222_5_lands_at_tolerance_zero(caplog: pytest.LogCaptureFixture) -> None:
    timeline = _long_timeline()
    expected = round_half_away_from_zero(timeline.seconds_to_tick(222.5))
    composition = _composition(timeline, 222.5)
    with caplog.at_level(logging.DEBUG):
        result = verify_cue_landings([_cue()], composition, **_kwargs(timeline))
    assert result[0].status == "landed"
    assert result[0].delta_frames == 0
    assert result[0].matches[0].start_tick == expected
    assert "60" not in " ".join(record.message for record in caplog.records)
    assert any(getattr(record, "cue_id", None) == "hit_0123abcd" for record in caplog.records)
    assert any(getattr(record, "status", None) == "landed" for record in caplog.records)


def test_three_frames_away_depends_on_tolerance() -> None:
    timeline = _long_timeline()
    composition = _composition(timeline, 222.625)
    missed = verify_cue_landings(
        [_cue(tolerance_frames=1)],
        composition,
        **_kwargs(timeline),
    )
    landed = verify_cue_landings(
        [_cue(tolerance_frames=3)],
        composition,
        **_kwargs(timeline),
    )
    assert missed[0].status == "missed"
    assert missed[0].delta_frames == 3
    assert landed[0].status == "landed"
    assert landed[0].match_count == 1


def test_sustain_across_the_frame_is_missed() -> None:
    timeline = _long_timeline()
    start_tick = round_half_away_from_zero(timeline.seconds_to_tick(220.0))
    cue_tick = round_half_away_from_zero(timeline.seconds_to_tick(222.5))
    composition = _composition(timeline, 220.0, duration_ticks=cue_tick - start_tick + 480)
    result = verify_cue_landings([_cue(tolerance_frames=0)], composition, **_kwargs(timeline))
    assert result[0].status == "missed"
    assert result[0].match_count == 0


def test_composition_without_notes_is_empty() -> None:
    timeline = _long_timeline()
    composition = {"tracks": [{"events": [{"type": "rest", "start_tick": 0}]}]}
    result = verify_cue_landings([_cue()], composition, **_kwargs(timeline))
    assert result[0].status == "empty"
    assert result[0].delta_frames is None


def test_tempo_change_does_not_use_root_tempo_tick() -> None:
    timeline = _long_timeline(tempo_change=True)
    expected = round_half_away_from_zero(timeline.seconds_to_tick(222.5))
    constant = round_half_away_from_zero(222.5 * (120 / 60.0) * timeline.ticks_per_quarter)
    assert expected != constant
    composition = _composition(timeline, 222.5)
    result = verify_cue_landings([_cue()], composition, **_kwargs(timeline))
    assert result[0].status == "landed"
    assert result[0].matches[0].start_tick == expected


def test_attack_past_asset_duration_is_not_a_match() -> None:
    timeline = _long_timeline()
    composition = _composition(timeline, 222.5)
    result = verify_cue_landings(
        [_cue(tolerance_frames=240)],
        composition,
        **_kwargs(timeline, duration_seconds=10.0),
    )
    assert result[0].status == "missed"
    assert result[0].match_count == 0


def test_null_timecode_uses_stored_seconds_frame() -> None:
    timeline = _long_timeline()
    cue = _cue(timecode=None, video_seconds=222.5)
    assert (
        cue_frame_index(
            cue,
            frame_rate_numerator=24,
            frame_rate_denominator=1,
            timecode_mode="non_drop",
            start_timecode="00:00:00:00",
        )
        == 5340
    )
    composition = _composition(timeline, 222.5)
    result = verify_cue_landings([cue], composition, **_kwargs(timeline))
    assert result[0].status == "landed"
    assert result[0].timecode is None


def test_drop_frame_cue_frame_is_parsed_not_refloored() -> None:
    timecode = "00:00:00:15"
    rate = {"frame_rate_numerator": 30000, "frame_rate_denominator": 1001, "timecode_mode": "drop_frame"}
    parsed = parse_timecode(timecode, **rate) - parse_timecode("00:00:00:00", **rate)
    seconds = cue_video_seconds(timecode, start_timecode="00:00:00:00", **rate)
    refloored = math.floor(seconds * 30000 / 1001)
    assert refloored != parsed
    cue = _cue(timecode=timecode, video_seconds=seconds)
    frame = cue_frame_index(cue, start_timecode="00:00:00:00", **rate)
    assert frame == parsed
    assert frame == 15
