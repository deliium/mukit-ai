"""Synthetic composition builders for analysis scale / mutation tests."""

from __future__ import annotations

from typing import Any


def note(pitch: str, start: int = 0, duration: int = 480, velocity: int = 80) -> dict[str, Any]:
    return {
        "pitch": pitch,
        "start_tick": start,
        "duration_ticks": duration,
        "velocity": velocity,
    }


def build_scale_composition(
    *,
    note_count: int = 100_000,
    track_count: int = 64,
    bar_count: int = 512,
    section_bars: int = 32,
) -> dict[str, Any]:
    """Build a normal-limit synthetic V2 near import caps (notes/tracks/bars)."""
    duration = bar_count * 1920
    notes_per_track = max(1, note_count // track_count)
    pitches = ["C4", "D4", "E4", "F4", "G4", "A4", "B4"]
    tracks: list[dict[str, Any]] = []
    for track_index in range(track_count):
        events: list[dict[str, Any]] = []
        step = max(1, duration // max(1, notes_per_track))
        for note_index in range(notes_per_track):
            start = min((note_index * step) % duration, duration - 120)
            events.append(note(pitches[(note_index + track_index) % 7], start, 60))
        role = "melody" if track_index == 0 else ("bass" if track_index == 1 else "harmony")
        tracks.append(
            {
                "id": f"t{track_index:02d}",
                "name": f"t{track_index:02d}",
                "instrument": "piano",
                "role": role,
                "midi_program": 0,
                "channel": 1,
                "events": events,
            }
        )

    sections: list[dict[str, Any]] = []
    for section_index, start_bar in enumerate(range(1, bar_count + 1, section_bars)):
        bars = min(section_bars, bar_count - start_bar + 1)
        start_tick = (start_bar - 1) * 1920
        sections.append(
            {
                "id": f"s{section_index}",
                "type": "verse" if section_index % 2 == 0 else "chorus",
                "start_bar": start_bar,
                "bar_count": bars,
                "start_tick": start_tick,
                "duration_ticks": bars * 1920,
            }
        )

    return {
        "schema_version": "composition.v2",
        "tempo": 120,
        "key": "C major",
        "time_signature": "4/4",
        "ticks_per_quarter": 480,
        "bar_count": bar_count,
        "duration_ticks": duration,
        "sections": sections,
        "tracks": tracks,
        "harmony": [],
        "tempo_changes": [],
        "time_signature_changes": [],
        "key_changes": [],
        "markers": [],
    }
