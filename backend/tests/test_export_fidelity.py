"""Deterministic Composition V1 export fidelity helpers and tests.

Shared fixture and note-tuple extractors used to prove MIDI, MusicXML, and
canonical JSON describe the same audible events.
"""

from __future__ import annotations

from typing import NamedTuple

from app.schemas import Composition


class CanonicalNoteTuple(NamedTuple):
    track_id: str
    pitch: str
    start_tick: int
    duration_ticks: int
    velocity: int


def build_export_fidelity_composition() -> Composition:
    """Build a deterministic multi-track Composition for export fidelity checks.

    Includes:
    - 6/8 meter (non-4/4)
    - two bars with an empty/rest gap in bar 1 of the melody
    - polyphony (same start_tick, overlapping durations)
    - non-zero note starts and varied durations/velocities
    - distinct instruments, programs, channels, volume, and pan
    """
    return Composition.model_validate(
        {
            "schema_version": "composition.v1",
            "tempo": 96,
            "key": "G minor",
            "time_signature": "6/8",
            "ticks_per_quarter": 480,
            "bar_count": 2,
            "duration_ticks": 2880,
            "sections": [
                {
                    "type": "intro",
                    "start_bar": 1,
                    "bar_count": 1,
                    "start_tick": 0,
                    "duration_ticks": 1440,
                },
                {
                    "type": "verse",
                    "start_bar": 2,
                    "bar_count": 1,
                    "start_tick": 1440,
                    "duration_ticks": 1440,
                },
            ],
            "tracks": [
                {
                    "id": "melody-1",
                    "name": "Melody",
                    "instrument": "flute",
                    "role": "melody",
                    "midi_program": 73,
                    "channel": 1,
                    "is_drum": False,
                    "volume": 110,
                    "pan": -10,
                    "staff": "treble",
                    "events": [
                        # Rest gap from tick 0..240, then a short note.
                        {
                            "type": "note",
                            "pitch": "G5",
                            "start_tick": 240,
                            "duration_ticks": 240,
                            "velocity": 96,
                            "staff": "treble",
                        },
                        {
                            "type": "note",
                            "pitch": "Bb5",
                            "start_tick": 720,
                            "duration_ticks": 480,
                            "velocity": 88,
                            "staff": "treble",
                        },
                        # Cross-measure-friendly sustained note into bar 2.
                        {
                            "type": "note",
                            "pitch": "D5",
                            "start_tick": 1200,
                            "duration_ticks": 720,
                            "velocity": 100,
                            "staff": "treble",
                        },
                    ],
                },
                {
                    "id": "harmony-2",
                    "name": "Harmony",
                    "instrument": "piano",
                    "role": "harmony",
                    "midi_program": 0,
                    "channel": 2,
                    "is_drum": False,
                    "volume": 90,
                    "pan": 0,
                    "staff": "grand",
                    "events": [
                        # Polyphony: two pitches at the same start with equal duration.
                        {
                            "type": "note",
                            "pitch": "G3",
                            "start_tick": 0,
                            "duration_ticks": 1440,
                            "velocity": 70,
                            "staff": "bass",
                        },
                        {
                            "type": "note",
                            "pitch": "Bb3",
                            "start_tick": 0,
                            "duration_ticks": 1440,
                            "velocity": 72,
                            "staff": "bass",
                        },
                        {
                            "type": "note",
                            "pitch": "D4",
                            "start_tick": 0,
                            "duration_ticks": 720,
                            "velocity": 74,
                            "staff": "treble",
                        },
                        # Overlapping duration polyphony in bar 2.
                        {
                            "type": "note",
                            "pitch": "F4",
                            "start_tick": 1440,
                            "duration_ticks": 960,
                            "velocity": 78,
                            "staff": "treble",
                        },
                        {
                            "type": "note",
                            "pitch": "A4",
                            "start_tick": 1680,
                            "duration_ticks": 720,
                            "velocity": 82,
                            "staff": "treble",
                        },
                    ],
                },
                {
                    "id": "bass-3",
                    "name": "Bass",
                    "instrument": "electric_bass",
                    "role": "bass",
                    "midi_program": 33,
                    "channel": 3,
                    "is_drum": False,
                    "volume": 100,
                    "pan": 12,
                    "staff": "bass",
                    "events": [
                        {
                            "type": "note",
                            "pitch": "G2",
                            "start_tick": 0,
                            "duration_ticks": 720,
                            "velocity": 101,
                        },
                        {
                            "type": "note",
                            "pitch": "D2",
                            "start_tick": 720,
                            "duration_ticks": 720,
                            "velocity": 95,
                        },
                        {
                            "type": "note",
                            "pitch": "G2",
                            "start_tick": 1440,
                            "duration_ticks": 1440,
                            "velocity": 105,
                        },
                    ],
                },
                {
                    # Empty track: rest/gap coverage for exporters.
                    "id": "pad-4",
                    "name": "Pad",
                    "instrument": "strings",
                    "role": "pad",
                    "midi_program": 48,
                    "channel": 4,
                    "is_drum": False,
                    "volume": 60,
                    "pan": -20,
                    "events": [],
                },
            ],
            "harmony": [
                {"bar": 1, "chord": "Gm"},
                {"bar": 2, "chord": "D"},
            ],
        }
    )


def canonical_note_tuples(composition: Composition) -> list[CanonicalNoteTuple]:
    """Extract sorted audible note tuples from a canonical Composition."""
    notes: list[CanonicalNoteTuple] = []
    for track in composition.tracks:
        for event in track.events:
            notes.append(
                CanonicalNoteTuple(
                    track_id=track.id,
                    pitch=event.pitch,
                    start_tick=event.start_tick,
                    duration_ticks=event.duration_ticks,
                    velocity=event.velocity,
                )
            )
    return sorted(notes, key=lambda item: (item.track_id, item.start_tick, item.pitch, item.duration_ticks))


def assert_note_tuples_equal(
    expected: list[CanonicalNoteTuple],
    actual: list[CanonicalNoteTuple],
    *,
    source_format: str,
) -> None:
    """Compare note tuples with explicit mismatch context for export fidelity."""
    if len(expected) != len(actual):
        raise AssertionError(
            f"{source_format} note count mismatch: expected {len(expected)}, got {len(actual)}; "
            f"expected={expected!r}; actual={actual!r}"
        )
    for index, (exp, act) in enumerate(zip(expected, actual)):
        if exp != act:
            raise AssertionError(
                f"{source_format} note mismatch at index {index}: "
                f"expected track={exp.track_id} pitch={exp.pitch} start={exp.start_tick} "
                f"duration={exp.duration_ticks} velocity={exp.velocity}; "
                f"got track={act.track_id} pitch={act.pitch} start={act.start_tick} "
                f"duration={act.duration_ticks} velocity={act.velocity}"
            )


def musicxml_note_tuples(
    musicxml: str,
    composition: Composition,
    *,
    include_velocity: bool = False,
) -> list[CanonicalNoteTuple]:
    """Extract note tuples from MusicXML via music21, mapped back to track IDs.

    MusicXML/music21 typically does not preserve MIDI velocity, so velocity is
    reported as 0 unless ``include_velocity`` is true and velocities are present.
    Tied notes that span measures are merged back into a single logical note.
    """
    from music21 import chord as m21_chord
    from music21 import converter, harmony as m21_harmony
    from music21 import note as m21_note

    score = converter.parseData(musicxml)
    ticks_per_quarter = composition.ticks_per_quarter
    parts = list(score.parts)

    # Piano grand-staff rendering may emit two PartStaffs for one composition track.
    part_to_track: list[str] = []
    for track in composition.tracks:
        is_piano = "piano" in track.instrument.lower() or track.staff == "grand"
        if is_piano:
            part_to_track.append(track.id)
            part_to_track.append(track.id)
        else:
            part_to_track.append(track.id)

    notes: list[CanonicalNoteTuple] = []
    for part_idx, part in enumerate(parts):
        track_id = part_to_track[part_idx] if part_idx < len(part_to_track) else f"part-{part_idx}"
        pending: dict[str, CanonicalNoteTuple] = {}
        for element in part.recurse().notes:
            if isinstance(element, m21_harmony.ChordSymbol):
                continue
            if isinstance(element, m21_note.Note):
                pitch_notes = [element]
            elif isinstance(element, m21_chord.Chord):
                pitch_notes = list(element.notes)
            else:
                continue
            duration_ticks = int(round(float(element.duration.quarterLength) * ticks_per_quarter))
            if duration_ticks <= 0:
                continue
            start_tick = int(round(float(element.getOffsetInHierarchy(score)) * ticks_per_quarter))
            for pitch_note in pitch_notes:
                pitch_name = pitch_note.pitch.nameWithOctave.replace("-", "b")
                velocity_value = getattr(getattr(pitch_note, "volume", None), "velocity", None)
                velocity = int(velocity_value) if include_velocity and velocity_value is not None else 0
                key = f"{track_id}:{pitch_name}"
                ties = getattr(pitch_note, "tie", None)
                tie_type = ties.type if ties is not None else None
                if tie_type == "stop" and key in pending:
                    previous = pending.pop(key)
                    notes.append(
                        CanonicalNoteTuple(
                            track_id=previous.track_id,
                            pitch=previous.pitch,
                            start_tick=previous.start_tick,
                            duration_ticks=previous.duration_ticks + duration_ticks,
                            velocity=previous.velocity,
                        )
                    )
                    continue
                candidate = CanonicalNoteTuple(
                    track_id=track_id,
                    pitch=pitch_name,
                    start_tick=start_tick,
                    duration_ticks=duration_ticks,
                    velocity=velocity,
                )
                if tie_type == "start":
                    pending[key] = candidate
                elif tie_type == "continue" and key in pending:
                    previous = pending[key]
                    pending[key] = CanonicalNoteTuple(
                        track_id=previous.track_id,
                        pitch=previous.pitch,
                        start_tick=previous.start_tick,
                        duration_ticks=previous.duration_ticks + duration_ticks,
                        velocity=previous.velocity,
                    )
                else:
                    notes.append(candidate)
        for leftover in pending.values():
            notes.append(leftover)

    return sorted(notes, key=lambda item: (item.track_id, item.start_tick, item.pitch, item.duration_ticks))


def midi_note_tuples_from_ready(midi_ready: dict) -> list[CanonicalNoteTuple]:
    """Extract note tuples from a MIDI-ready intermediate structure."""
    notes: list[CanonicalNoteTuple] = []
    for track in midi_ready.get("tracks", []):
        track_id = track["id"]
        for event in track.get("events", []):
            notes.append(
                CanonicalNoteTuple(
                    track_id=track_id,
                    pitch=event["pitch"],
                    start_tick=event["start_tick"],
                    duration_ticks=event["duration_ticks"],
                    velocity=event["velocity"],
                )
            )
    return sorted(notes, key=lambda item: (item.track_id, item.start_tick, item.pitch, item.duration_ticks))


def midi_note_tuples_from_bytes(midi_bytes: bytes, composition: Composition) -> list[CanonicalNoteTuple]:
    """Extract note tuples from Standard MIDI File bytes.

    Requires ``mido`` (added with MIDI export rendering). Maps MIDI note numbers
    back to pitch names and uses track order matching composition.tracks.
    """
    try:
        import mido
    except ImportError as exc:  # pragma: no cover - dependency installed with Task 3
        raise RuntimeError("mido is required to parse exported MIDI bytes") from exc

    from io import BytesIO

    mid = mido.MidiFile(file=BytesIO(midi_bytes))
    ticks_per_beat = mid.ticks_per_beat
    if ticks_per_beat != composition.ticks_per_quarter:
        raise AssertionError(
            f"MIDI ticks_per_beat mismatch: expected {composition.ticks_per_quarter}, got {ticks_per_beat}"
        )

    # Skip conductor/meta track if present as first track with no notes.
    midi_tracks = list(mid.tracks)
    notes: list[CanonicalNoteTuple] = []
    composition_tracks = list(composition.tracks)

    # Heuristic: if there is one extra track, treat track 0 as tempo/meta only.
    track_offset = 1 if len(midi_tracks) == len(composition_tracks) + 1 else 0

    for index, midi_track in enumerate(midi_tracks[track_offset:]):
        if index >= len(composition_tracks):
            break
        track_id = composition_tracks[index].id
        absolute_tick = 0
        active: dict[int, list[tuple[int, int]]] = {}
        for message in midi_track:
            absolute_tick += message.time
            if message.type == "note_on" and message.velocity > 0:
                active.setdefault(message.note, []).append((absolute_tick, message.velocity))
            elif message.type == "note_off" or (message.type == "note_on" and message.velocity == 0):
                stack = active.get(message.note) or []
                if not stack:
                    continue
                start_tick, velocity = stack.pop(0)
                pitch = _midi_number_to_pitch(message.note)
                notes.append(
                    CanonicalNoteTuple(
                        track_id=track_id,
                        pitch=pitch,
                        start_tick=start_tick,
                        duration_ticks=absolute_tick - start_tick,
                        velocity=velocity,
                    )
                )
    return sorted(notes, key=lambda item: (item.track_id, item.start_tick, item.pitch, item.duration_ticks))


def _midi_number_to_pitch(midi_number: int) -> str:
    # Prefer flats so composition pitches like Bb round-trip from MIDI note numbers.
    note_names = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
    octave = (midi_number // 12) - 1
    return f"{note_names[midi_number % 12]}{octave}"


def note_tuples_without_velocity(notes: list[CanonicalNoteTuple]) -> list[CanonicalNoteTuple]:
    return [
        CanonicalNoteTuple(
            track_id=item.track_id,
            pitch=item.pitch,
            start_tick=item.start_tick,
            duration_ticks=item.duration_ticks,
            velocity=0,
        )
        for item in notes
    ]


def test_export_fidelity_fixture_is_valid_and_extractable():
    composition = build_export_fidelity_composition()
    notes = canonical_note_tuples(composition)

    assert composition.time_signature == "6/8"
    assert composition.bar_count == 2
    assert len(composition.tracks) == 4
    assert any(track.id == "pad-4" and not track.events for track in composition.tracks)
    assert any(note.start_tick == 240 for note in notes), "expected non-zero start in melody"
    polyphonic = [note for note in notes if note.track_id == "harmony-2" and note.start_tick == 0]
    assert len(polyphonic) >= 2
    assert {note.velocity for note in notes} != {80}
    assert_note_tuples_equal(notes, notes, source_format="canonical")


def test_midi_ready_matches_canonical_note_tuples():
    from app.services.composition_midi import composition_to_midi_ready

    composition = build_export_fidelity_composition()
    expected = canonical_note_tuples(composition)
    actual = midi_note_tuples_from_ready(composition_to_midi_ready(composition))
    assert_note_tuples_equal(expected, actual, source_format="midi-ready")


def test_midi_bytes_match_canonical_note_tuples():
    from app.services.composition_midi import render_midi

    composition = build_export_fidelity_composition()
    expected = canonical_note_tuples(composition)
    midi_bytes = render_midi(composition)
    actual = midi_note_tuples_from_bytes(midi_bytes, composition)
    assert_note_tuples_equal(expected, actual, source_format="midi")


def test_musicxml_matches_canonical_note_timing():
    from app.services.music_json_renderer import render_musicxml

    composition = build_export_fidelity_composition()
    expected = note_tuples_without_velocity(canonical_note_tuples(composition))
    musicxml, _warnings = render_musicxml(composition)
    actual = musicxml_note_tuples(musicxml, composition, include_velocity=False)
    assert_note_tuples_equal(expected, actual, source_format="musicxml")
