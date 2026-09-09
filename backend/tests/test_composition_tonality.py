"""Tonal-center analysis unit coverage (generation + analysis inference)."""

from __future__ import annotations

from app.analysis_schemas import ANALYSIS_ALGORITHM_VERSION
from app.schemas import Composition
from app.services.composition_analysis_context import build_analysis_context
from app.services.composition_tonality import (
    analyze_composition_tonality,
    analyze_harmony_tonality,
    infer_tonality_from_context,
    parse_chord_symbol,
    parse_key,
    score_keys_from_pitch_histogram,
)


def test_parse_key_and_chords():
    key = parse_key("F# minor")
    assert key is not None
    assert key.tonic_pc == 6
    assert key.mode == "minor"
    assert parse_chord_symbol("C#7").quality == "dom7"
    assert parse_chord_symbol("Am").root_pc == 9
    slash = parse_chord_symbol("D7/F#")
    assert slash.bass_pc == 6
    assert slash.quality == "dom7"


def test_a_minor_progression_contradicts_f_sharp_minor_request():
    chords = [(1, "Am"), (2, "F"), (3, "C"), (4, "Dm"), (5, "E7"), (6, "Am"), (7, "F"), (8, "C")]
    result = analyze_harmony_tonality(chords, "F# minor", bar_count=8, boundary_bars=[1, 5])
    assert result.status == "contradiction"
    assert result.winning_candidate == "A minor"
    assert result.reason == "competing_tonal_center"


def test_f_sharp_minor_with_chromaticism_passes():
    chords = [
        (1, "F#m"),
        (2, "C#7"),
        (3, "Bm"),
        (4, "G#7"),  # secondary dominant
        (5, "C#7"),
        (6, "D"),  # borrowed/relative color
        (7, "F#m"),
        (8, "C#7"),
    ]
    result = analyze_harmony_tonality(chords, "F# minor", bar_count=8, boundary_bars=[1, 5])
    assert result.status == "ok"
    assert result.winning_candidate == "F# minor"


def test_relabeled_metadata_still_fails_on_a_centered_events():
    events = []
    tick = 0
    for pitch in ["A4", "C5", "E5", "A4", "B4", "C5", "E5", "A5"] * 4:
        events.append({"pitch": pitch, "start_tick": tick, "duration_ticks": 240, "velocity": 90})
        tick += 240
    composition = Composition(
        tempo=100,
        key="F# minor",
        time_signature="4/4",
        ticks_per_quarter=480,
        duration_ticks=8 * 1920,
        bar_count=8,
        sections=[
            {
                "type": "verse",
                "start_bar": 1,
                "bar_count": 8,
                "start_tick": 0,
                "duration_ticks": 8 * 1920,
            }
        ],
        tracks=[
            {
                "id": "m",
                "name": "m",
                "instrument": "piano",
                "role": "melody",
                "midi_program": 0,
                "channel": 1,
                "events": events,
            }
        ],
        harmony=[
            {"bar": i, "chord": ch}
            for i, ch in enumerate(["Am", "F", "C", "Dm", "E7", "Am", "F", "C"], start=1)
        ],
    )
    result = analyze_composition_tonality(composition, "F# minor")
    assert result.status == "contradiction"
    assert result.winning_candidate == "A minor"


def _v2_shell(*, bar_count: int, key: str = "C major", sections=None, tracks=None, **overrides):
    duration = bar_count * 1920
    data = {
        "schema_version": "composition.v2",
        "tempo": 100,
        "key": key,
        "time_signature": "4/4",
        "ticks_per_quarter": 480,
        "bar_count": bar_count,
        "duration_ticks": duration,
        "sections": sections
        or [
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
                "id": "melody-1",
                "name": "Melody",
                "instrument": "piano",
                "role": "melody",
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


def _events_from_pitches(pitches: list[str], *, duration_ticks: int = 480, gap: int = 480):
    events = []
    tick = 0
    for pitch in pitches:
        events.append(
            {
                "pitch": pitch,
                "start_tick": tick,
                "duration_ticks": duration_ticks,
                "velocity": 90,
            }
        )
        tick += gap
    return events


def test_histogram_scoring_ranks_deterministically():
    mass = [0.0] * 12
    mass[9] = 8000.0
    mass[0] = 6000.0
    mass[4] = 6000.0
    ranked = score_keys_from_pitch_histogram(mass)
    assert ranked[0].key == "A minor"
    assert ranked[0].mode == "minor"
    assert ranked[0].pitch_class == 9
    # Stable order on equal scores: lower pitch_class, then major before minor.
    tied = [0.0] * 12
    for pc in (0, 4, 7, 9):
        tied[pc] = 5000.0
    tied_ranked = score_keys_from_pitch_histogram(tied)
    assert tied_ranked[0].key == "C major"
    assert tied_ranked[1].key == "A minor"
    assert tied_ranked[0].score == tied_ranked[1].score


def test_infer_a_minor_without_requested_key():
    pitches = ["A4", "C5", "E5", "A4", "B4", "C5", "E5", "A5"] * 4
    raw = _v2_shell(
        bar_count=8,
        key="C major",
        tracks=[
            {
                "id": "melody-1",
                "name": "Melody",
                "instrument": "piano",
                "role": "melody",
                "midi_program": 0,
                "channel": 1,
                "events": _events_from_pitches(pitches, duration_ticks=480, gap=480),
            }
        ],
    )
    context = build_analysis_context(raw, {"kind": "composition"})
    result = infer_tonality_from_context(context)
    assert result.inference.status == "ok"
    assert result.global_key is not None
    assert result.global_key.key == "A minor"
    assert result.declared_key == "C major"
    assert result.effective_key == "A minor"
    assert result.inference.method == ANALYSIS_ALGORITHM_VERSION
    assert result.global_key.inference.method_version == ANALYSIS_ALGORITHM_VERSION


def test_f_sharp_minor_chromaticism_still_ok_for_generation():
    """Generation path remains tolerant of F# minor secondary-dominant color."""
    chords = [
        (1, "F#m"),
        (2, "C#7"),
        (3, "Bm"),
        (4, "G#7"),
        (5, "C#7"),
        (6, "D"),
        (7, "F#m"),
        (8, "C#7"),
    ]
    result = analyze_harmony_tonality(chords, "F# minor", bar_count=8, boundary_bars=[1, 5])
    assert result.status == "ok"
    assert result.winning_candidate == "F# minor"


def test_relative_key_ambiguity_path():
    # Equal C/E/G/A mass yields tied relative major/minor candidates.
    pitches = ["C4", "E4", "G4", "A4"] * 8
    raw = _v2_shell(
        bar_count=8,
        key="C major",
        tracks=[
            {
                "id": "melody-1",
                "name": "Melody",
                "instrument": "piano",
                "role": "melody",
                "midi_program": 0,
                "channel": 1,
                "events": _events_from_pitches(pitches, duration_ticks=480, gap=480),
            }
        ],
    )
    context = build_analysis_context(raw, {"kind": "composition"})
    result = infer_tonality_from_context(context)
    assert result.inference.status == "ambiguous"
    assert result.global_key is not None
    assert result.global_key.key in {"C major", "A minor"}
    top_two = [item.key for item in result.global_key.candidates[:2]]
    assert set(top_two) == {"C major", "A minor"}


def test_mixed_meter_context_inference():
    # 4/4 -> 3/4 -> 6/8 with C-major triad occupancy across variable meters.
    events = [
        {"id": "n1", "pitch": "C4", "start_tick": 0, "duration_ticks": 2400, "velocity": 80},
        {"id": "n2", "pitch": "E4", "start_tick": 0, "duration_ticks": 2400, "velocity": 70},
        {"id": "n3", "pitch": "G4", "start_tick": 0, "duration_ticks": 2400, "velocity": 60},
        {"id": "n4", "pitch": "C5", "start_tick": 2400, "duration_ticks": 2400, "velocity": 80},
        {"id": "n5", "pitch": "E5", "start_tick": 2400, "duration_ticks": 2400, "velocity": 70},
        {"id": "n6", "pitch": "G5", "start_tick": 2400, "duration_ticks": 2400, "velocity": 60},
    ]
    raw = {
        "schema_version": "composition.v2",
        "tempo": 120,
        "key": "G major",
        "time_signature": "4/4",
        "ticks_per_quarter": 480,
        "bar_count": 3,
        "duration_ticks": 4800,
        "sections": [
            {
                "type": "intro",
                "start_bar": 1,
                "bar_count": 3,
                "start_tick": 0,
                "duration_ticks": 4800,
            }
        ],
        "tracks": [
            {
                "id": "melody-1",
                "name": "Melody",
                "instrument": "violin",
                "role": "melody",
                "midi_program": 40,
                "channel": 1,
                "events": events,
            }
        ],
        "harmony": [],
        "tempo_changes": [],
        "time_signature_changes": [
            {"tick": 1920, "time_signature": "3/4"},
            {"tick": 3360, "time_signature": "6/8"},
        ],
        "key_changes": [],
        "markers": [],
    }
    context = build_analysis_context(raw, {"kind": "composition"})
    result = infer_tonality_from_context(context)
    assert result.inference.status == "ok"
    assert result.global_key is not None
    assert result.global_key.key == "C major"
    assert result.local_spans


def test_sparse_empty_and_percussion_abstention():
    empty = build_analysis_context(_v2_shell(bar_count=2), {"kind": "composition"})
    empty_result = infer_tonality_from_context(empty)
    assert empty_result.inference.status == "insufficient_evidence"
    assert empty_result.global_key is not None
    assert empty_result.global_key.key is None
    assert empty_result.effective_key == "C major"

    sparse = _v2_shell(
        bar_count=2,
        tracks=[
            {
                "id": "melody-1",
                "name": "Melody",
                "instrument": "piano",
                "role": "melody",
                "midi_program": 0,
                "channel": 1,
                "events": [
                    {"pitch": "C4", "start_tick": 0, "duration_ticks": 120, "velocity": 80},
                ],
            }
        ],
    )
    sparse_result = infer_tonality_from_context(
        build_analysis_context(sparse, {"kind": "composition"})
    )
    assert sparse_result.inference.status == "insufficient_evidence"

    drums_only = _v2_shell(
        bar_count=2,
        tracks=[
            {
                "id": "drums-1",
                "name": "Drums",
                "instrument": "drums",
                "role": "rhythm",
                "midi_program": 0,
                "channel": 10,
                "is_drum": True,
                "events": [
                    {"pitch": "C2", "start_tick": 0, "duration_ticks": 1920, "velocity": 100},
                    {"pitch": "D2", "start_tick": 1920, "duration_ticks": 1920, "velocity": 100},
                ],
            }
        ],
    )
    drums_result = infer_tonality_from_context(
        build_analysis_context(drums_only, {"kind": "composition"})
    )
    assert drums_result.inference.status == "not_applicable"


def test_scope_isolation_section_with_different_notes():
    # Section 0: A minor material; Section 1: F# minor material.
    a_pitches = ["A4", "C5", "E5", "A4"] * 4
    fs_pitches = ["F#4", "A4", "C#5", "F#4"] * 4
    a_events = _events_from_pitches(a_pitches, duration_ticks=480, gap=480)
    fs_events = _events_from_pitches(fs_pitches, duration_ticks=480, gap=480)
    for event in fs_events:
        event["start_tick"] += 4 * 1920
    raw = _v2_shell(
        bar_count=8,
        key="A minor",
        sections=[
            {
                "type": "verse",
                "start_bar": 1,
                "bar_count": 4,
                "start_tick": 0,
                "duration_ticks": 4 * 1920,
            },
            {
                "type": "chorus",
                "start_bar": 5,
                "bar_count": 4,
                "start_tick": 4 * 1920,
                "duration_ticks": 4 * 1920,
            },
        ],
        tracks=[
            {
                "id": "melody-1",
                "name": "Melody",
                "instrument": "piano",
                "role": "melody",
                "midi_program": 0,
                "channel": 1,
                "events": a_events + fs_events,
            }
        ],
        key_changes=[{"tick": 4 * 1920, "key": "F# minor"}],
    )
    section0 = infer_tonality_from_context(
        build_analysis_context(raw, {"kind": "section", "section_index": 0})
    )
    section1 = infer_tonality_from_context(
        build_analysis_context(raw, {"kind": "section", "section_index": 1})
    )
    assert section0.global_key is not None
    assert section1.global_key is not None
    assert section0.global_key.key == "A minor"
    assert section1.global_key.key == "F# minor"


def test_infer_tonality_deterministic_repeated_runs():
    pitches = ["A4", "C5", "E5", "A4", "B4", "C5", "E5", "A5"] * 4
    raw = _v2_shell(
        bar_count=8,
        key="A minor",
        tracks=[
            {
                "id": "melody-1",
                "name": "Melody",
                "instrument": "piano",
                "role": "melody",
                "midi_program": 0,
                "channel": 1,
                "events": _events_from_pitches(pitches, duration_ticks=480, gap=480),
            }
        ],
    )
    first = infer_tonality_from_context(build_analysis_context(raw, {"kind": "composition"}))
    second = infer_tonality_from_context(build_analysis_context(raw, {"kind": "composition"}))
    assert first.model_dump() == second.model_dump()
