"""Tonal-center analysis unit coverage."""

from __future__ import annotations

from app.schemas import Composition
from app.services.composition_tonality import (
    analyze_composition_tonality,
    analyze_harmony_tonality,
    parse_chord_symbol,
    parse_key,
)


def test_parse_key_and_chords():
    key = parse_key("F# minor")
    assert key is not None
    assert key.tonic_pc == 6
    assert key.mode == "minor"
    assert parse_chord_symbol("C#7").quality == "dom7"
    assert parse_chord_symbol("Am").root_pc == 9


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
