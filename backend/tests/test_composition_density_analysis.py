"""Density analysis unit coverage."""

from __future__ import annotations

from app.analysis_schemas import ANALYSIS_ALGORITHM_VERSION
from app.services.composition_analysis_context import build_analysis_context
from app.services.composition_density_analysis import (
    DENSITY_METHOD,
    analyze_density_from_context,
    bar_density_snapshot,
)
from app.services.composition_harmony_analysis import analyze_harmony_from_context


def _v2_shell(*, bar_count: int = 2, tracks=None, time_signature="4/4", **overrides):
    duration = bar_count * 1920
    if "time_signature_changes" in overrides:
        # Caller supplies duration / sections when meter varies.
        pass
    data = {
        "schema_version": "composition.v2",
        "tempo": 100,
        "key": "C major",
        "time_signature": time_signature,
        "ticks_per_quarter": 480,
        "bar_count": bar_count,
        "duration_ticks": duration,
        "sections": [
            {
                "type": "verse",
                "start_bar": 1,
                "bar_count": bar_count,
                "start_tick": 0,
                "duration_ticks": duration,
            }
        ],
        "tracks": tracks
        or [
            {
                "id": "density-1",
                "name": "Density",
                "instrument": "piano",
                "role": "harmony",
                "midi_program": 0,
                "channel": 1,
                "events": [],
            }
        ],
        "harmony": [],
        "tempo_changes": [],
        "time_signature_changes": [],
        "key_changes": [],
        "markers": [],
    }
    data.update(overrides)
    return data


def _track(events, *, track_id="density-1", role="harmony", instrument="piano", channel=1, is_drum=False):
    track = {
        "id": track_id,
        "name": track_id,
        "instrument": instrument,
        "role": role,
        "midi_program": 0,
        "channel": channel,
        "events": events,
    }
    if is_drum:
        track["is_drum"] = True
        track["channel"] = 10
        track["midi_program"] = 0
    return track


def test_union_occupancy_vs_note_load_polyphony():
    # Two fully overlapping whole notes: union ratio 1.0, note load 2.0
    events = [
        {"pitch": "C4", "start_tick": 0, "duration_ticks": 1920, "velocity": 80},
        {"pitch": "E4", "start_tick": 0, "duration_ticks": 1920, "velocity": 80},
    ]
    raw = _v2_shell(bar_count=1, tracks=[_track(events)], duration_ticks=1920)
    raw["sections"][0]["duration_ticks"] = 1920
    result = analyze_density_from_context(build_analysis_context(raw))
    assert result.metrics.active_time_union_ratio == 1.0
    assert result.metrics.note_load == 2.0
    assert result.metrics.max_simultaneity == 2
    assert result.metrics.mean_simultaneity == 2.0
    assert result.inference.method == DENSITY_METHOD
    assert result.inference.method_version == ANALYSIS_ALGORITHM_VERSION


def test_crossing_note_clipped_across_bars():
    # Note from mid-bar-1 into mid-bar-2 must split occupancy.
    events = [
        {"pitch": "C4", "start_tick": 960, "duration_ticks": 1920, "velocity": 80},
    ]
    raw = _v2_shell(bar_count=2, tracks=[_track(events)])
    context = build_analysis_context(raw)
    bar1 = bar_density_snapshot(context, 1)
    bar2 = bar_density_snapshot(context, 2)
    assert bar1["note_load_ticks"] == 960
    assert bar2["note_load_ticks"] == 960
    assert bar1["attack_count"] == 1
    assert bar2["attack_count"] == 0  # attack-at-onset only in bar 1


def test_variable_meter_attacks_per_bar():
    # Bar1 4/4 (1920), bar2 3/4 (1440); one attack each bar.
    duration = 1920 + 1440
    events = [
        {"pitch": "C4", "start_tick": 0, "duration_ticks": 480, "velocity": 80},
        {"pitch": "D4", "start_tick": 1920, "duration_ticks": 480, "velocity": 80},
    ]
    raw = _v2_shell(
        bar_count=2,
        tracks=[_track(events)],
        duration_ticks=duration,
        time_signature="4/4",
        time_signature_changes=[{"tick": 1920, "time_signature": "3/4"}],
        sections=[
            {
                "type": "verse",
                "start_bar": 1,
                "bar_count": 2,
                "start_tick": 0,
                "duration_ticks": duration,
            }
        ],
    )
    result = analyze_density_from_context(build_analysis_context(raw))
    assert result.metrics.attacks_per_bar == 1.0
    assert result.metrics.attacks_per_quarter is not None
    assert result.inference.status == "ok"


def test_median_ioi_and_active_track_ratio():
    events_a = [
        {"pitch": "C4", "start_tick": 0, "duration_ticks": 240, "velocity": 80},
        {"pitch": "D4", "start_tick": 480, "duration_ticks": 240, "velocity": 80},
        {"pitch": "E4", "start_tick": 960, "duration_ticks": 240, "velocity": 80},
    ]
    events_b = [
        {"pitch": "G3", "start_tick": 0, "duration_ticks": 1920, "velocity": 70},
    ]
    raw = _v2_shell(
        bar_count=1,
        duration_ticks=1920,
        tracks=[
            _track(events_a, track_id="a", role="melody"),
            _track(events_b, track_id="b", role="bass", instrument="bass"),
        ],
        sections=[
            {
                "type": "verse",
                "start_bar": 1,
                "bar_count": 1,
                "start_tick": 0,
                "duration_ticks": 1920,
            }
        ],
    )
    result = analyze_density_from_context(build_analysis_context(raw))
    assert result.metrics.median_inter_onset_ticks == 480.0
    assert result.metrics.active_track_ratio == 1.0
    assert result.metrics.distinct_pitch_class_density is not None
    assert result.metrics.distinct_pitch_class_density > 0


def test_chord_changes_from_harmony():
    events = [
        {"pitch": "C3", "start_tick": 0, "duration_ticks": 1920, "velocity": 80},
        {"pitch": "E3", "start_tick": 0, "duration_ticks": 1920, "velocity": 75},
        {"pitch": "G3", "start_tick": 0, "duration_ticks": 1920, "velocity": 70},
        {"pitch": "G3", "start_tick": 1920, "duration_ticks": 1920, "velocity": 80},
        {"pitch": "B3", "start_tick": 1920, "duration_ticks": 1920, "velocity": 75},
        {"pitch": "D4", "start_tick": 1920, "duration_ticks": 1920, "velocity": 70},
    ]
    raw = _v2_shell(bar_count=2, tracks=[_track(events)])
    context = build_analysis_context(raw)
    harmony = analyze_harmony_from_context(context)
    result = analyze_density_from_context(context, harmony)
    assert result.metrics.chord_changes >= 1


def test_empty_scope_insufficient_evidence():
    raw = _v2_shell(bar_count=1, duration_ticks=1920, tracks=[_track([])])
    raw["sections"][0]["duration_ticks"] = 1920
    result = analyze_density_from_context(build_analysis_context(raw))
    assert result.inference.status == "insufficient_evidence"
    assert result.metrics.note_load == 0.0
    assert result.metrics.active_time_union_ratio == 0.0
