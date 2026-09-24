"""Golden + quality tests for parametric audio alignment map builders."""

from __future__ import annotations

import pytest

from app.audio_alignment_schemas import AudioAlignmentV1
from app.composition_schemas import CompositionV2
from app.services.audio_alignment import (
    TEMPO_DIVERGENCE_EPSILON_BPM,
    bar_range_to_audio_window,
    build_alignment_from_scaffolding,
    source_seconds_to_tick,
    tick_to_source_seconds,
)
from app.services.composition_timeline import compile_timeline
from tests.test_composition_v2_schema import minimal_v2


def _sixteen_bar_120bpm(**overrides):
    """16 bars @ 120 BPM / PPQ 480 / 4/4 → 30720 duration_ticks."""
    data = minimal_v2(
        tempo=120,
        ticks_per_quarter=480,
        bar_count=16,
        duration_ticks=16 * 1920,
        sections=[
            {
                "type": "verse",
                "start_bar": 1,
                "bar_count": 16,
                "start_tick": 0,
                "duration_ticks": 16 * 1920,
            }
        ],
    )
    data.update(overrides)
    return CompositionV2.model_validate(data)


def _scaffolding(**overrides):
    base = {
        "tempo_bpm": 120,
        "tempo_confidence": 0.85,
        "tempo_source": "estimated",
        "meter": "4/4",
        "beat_grid": {
            "downbeat_offset_seconds": 0.5,
            "ticks_per_quarter": 480,
            "confidence": 0.8,
        },
    }
    base.update(overrides)
    return base


def test_golden_bar_10_at_120bpm_offset_half_second() -> None:
    """Bar 10 start @ 120 BPM / PPQ 480 / offset 0.5s → 18.5s source time.

    Bar length = 1920 ticks = 2.0s musical; bar 10 starts at tick 17280
    → musical 18.0s + offset 0.5s = 18.5s.
    """
    composition = _sixteen_bar_120bpm()
    timeline = compile_timeline(composition)
    assert timeline.bar_start_tick(10) == 17280

    start = tick_to_source_seconds(
        17280,
        timeline=timeline,
        downbeat_offset_seconds=0.5,
        origin_tick=0,
    )
    assert start == pytest.approx(18.5, abs=1e-6)

    end_tick = timeline.bar_end_tick(10)
    end = tick_to_source_seconds(
        end_tick,
        timeline=timeline,
        downbeat_offset_seconds=0.5,
        origin_tick=0,
    )
    assert end == pytest.approx(20.5, abs=1e-6)

    # Inverse round-trip
    back = source_seconds_to_tick(
        18.5,
        timeline=timeline,
        downbeat_offset_seconds=0.5,
        origin_tick=0,
    )
    assert back == 17280


def test_bar_range_to_audio_window_bars_9_12() -> None:
    composition = _sixteen_bar_120bpm()
    alignment = build_alignment_from_scaffolding(
        composition=composition,
        scaffolding=_scaffolding(),
        source_audio_asset_id="src_golden01",
        composition_fingerprint="snap_golden_fp01",
    )
    window = bar_range_to_audio_window(
        9, 12, composition=composition, alignment=alignment
    )
    # Bar 9 start tick = 8*1920 = 15360 → 16.0 + 0.5 = 16.5
    # Bar 12 end tick = 12*1920 = 23040 → 24.0 + 0.5 = 24.5
    assert window["start_seconds"] == pytest.approx(16.5, abs=1e-6)
    assert window["end_seconds"] == pytest.approx(24.5, abs=1e-6)
    assert window["start_tick"] == 15360
    assert window["end_tick"] == 23040


def test_build_alignment_sets_tempo_diverged_issue() -> None:
    composition = _sixteen_bar_120bpm(tempo=140)
    alignment = build_alignment_from_scaffolding(
        composition=composition,
        scaffolding=_scaffolding(tempo_bpm=120),
        source_audio_asset_id="src_div01",
        composition_fingerprint="snap_div_fp01xx",
    )
    assert "alignment_tempo_diverged" in alignment.quality.issues
    assert alignment.quality.overall_confidence <= 0.35
    assert alignment.map.tempo_bpm == 140.0
    assert alignment.map.scaffolding_tempo_bpm == 120.0
    assert abs(140.0 - 120.0) > TEMPO_DIVERGENCE_EPSILON_BPM


def test_build_alignment_stem_bindings_role_to_track_only() -> None:
    composition = _sixteen_bar_120bpm()
    alignment = build_alignment_from_scaffolding(
        composition=composition,
        scaffolding=_scaffolding(),
        source_audio_asset_id="src_stem01",
        composition_fingerprint="snap_stem_fp01x",
        stem_bindings=[{"stem": "melody", "track_id": "track_melody"}],
    )
    assert isinstance(alignment, AudioAlignmentV1)
    assert alignment.stem_bindings[0].stem == "melody"
    assert alignment.stem_bindings[0].track_id == "track_melody"
    dumped = alignment.model_dump()
    assert "stem_audio_asset_id" not in dumped["stem_bindings"][0]


def test_build_alignment_missing_source_raises() -> None:
    composition = _sixteen_bar_120bpm()
    from app.audio_alignment_schemas import AudioAlignmentError

    with pytest.raises(AudioAlignmentError) as exc:
        build_alignment_from_scaffolding(
            composition=composition,
            scaffolding=_scaffolding(),
            source_audio_asset_id="",
        )
    assert exc.value.code == "audio_alignment_missing_source"
