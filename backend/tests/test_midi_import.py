"""Tests for deterministic MIDI → composition.v2 import."""

from __future__ import annotations

from io import BytesIO

import mido
import pytest

from app.import_schemas import CompositionImportError
from app.import_settings import MIDI_HEADER_SIGNATURE, load_import_settings
from app.services.composition_midi_import import import_midi_bytes, parse_midi_bytes
from app.services.composition_timeline import compile_timeline


def _midi_bytes(build) -> bytes:
    buf = BytesIO()
    build.save(file=buf)
    return buf.getvalue()


def _simple_type1(*, program: int = 0, channel: int = 0, tempo: int | None = 500000) -> bytes:
    mid = mido.MidiFile(type=1, ticks_per_beat=480)
    conductor = mido.MidiTrack()
    mid.tracks.append(conductor)
    if tempo is not None:
        conductor.append(mido.MetaMessage("set_tempo", tempo=tempo, time=0))
    conductor.append(mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0))
    conductor.append(mido.MetaMessage("key_signature", key="C", time=0))
    conductor.append(mido.MetaMessage("end_of_track", time=0))

    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("track_name", name="Piano", time=0))
    track.append(mido.Message("program_change", program=program, channel=channel, time=0))
    track.append(mido.Message("control_change", control=7, value=110, channel=channel, time=0))
    track.append(mido.Message("note_on", note=60, velocity=90, channel=channel, time=0))
    track.append(mido.Message("note_off", note=60, velocity=0, channel=channel, time=480))
    track.append(mido.MetaMessage("end_of_track", time=0))
    return _midi_bytes(mid)


def test_import_simple_midi_preserves_note_and_metadata():
    result = import_midi_bytes(_simple_type1(), display_filename="simple.mid")
    composition = result.composition
    assert composition.schema_version == "composition.v2"
    assert composition.tempo == 120
    assert composition.time_signature == "4/4"
    assert composition.key == "C major"
    assert composition.ticks_per_quarter == 480
    assert len(composition.tracks) == 1
    track = composition.tracks[0]
    assert track.midi_program == 0
    assert track.channel == 1
    assert track.volume == 110
    assert len(track.events) == 1
    assert track.events[0].pitch == "C4"
    assert track.events[0].velocity == 90
    assert track.events[0].duration_ticks == 480
    compile_timeline(composition)


def test_import_is_deterministic_for_identical_bytes():
    data = _simple_type1()
    first = import_midi_bytes(data, display_filename="a.mid")
    second = import_midi_bytes(data, display_filename="b.mid")
    assert first.composition.model_dump(mode="json") == second.composition.model_dump(mode="json")
    assert [issue.code for issue in first.import_report.issues] == [
        issue.code for issue in second.import_report.issues
    ]


def test_velocity_zero_note_on_acts_as_note_off():
    mid = mido.MidiFile(type=0, ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.Message("note_on", note=64, velocity=80, time=0))
    track.append(mido.Message("note_on", note=64, velocity=0, time=240))
    track.append(mido.MetaMessage("end_of_track", time=0))
    parsed = parse_midi_bytes(_midi_bytes(mid))
    assert len(parsed.score.tracks) == 1
    assert len(parsed.score.tracks[0].notes) == 1
    assert parsed.score.tracks[0].notes[0].duration_ticks == 240


def test_overlapping_same_pitch_uses_fifo():
    mid = mido.MidiFile(type=0, ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.Message("note_on", note=60, velocity=40, time=0))
    track.append(mido.Message("note_on", note=60, velocity=80, time=0))
    track.append(mido.Message("note_off", note=60, velocity=0, time=100))
    track.append(mido.Message("note_off", note=60, velocity=0, time=100))
    track.append(mido.MetaMessage("end_of_track", time=0))
    notes = parse_midi_bytes(_midi_bytes(mid)).score.tracks[0].notes
    assert [note.velocity for note in notes] == [40, 80]
    assert [note.duration_ticks for note in notes] == [100, 200]


def test_program_change_splits_track_segments():
    mid = mido.MidiFile(type=1, ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.Message("program_change", program=0, time=0))
    track.append(mido.Message("note_on", note=60, velocity=90, time=0))
    track.append(mido.Message("note_off", note=60, velocity=0, time=240))
    track.append(mido.Message("program_change", program=40, time=0))
    track.append(mido.Message("note_on", note=67, velocity=90, time=0))
    track.append(mido.Message("note_off", note=67, velocity=0, time=240))
    track.append(mido.MetaMessage("end_of_track", time=0))
    result = import_midi_bytes(_midi_bytes(mid), display_filename="split.mid")
    programs = sorted(track.midi_program for track in result.composition.tracks)
    assert programs == [0, 40]
    assert any(issue.code == "program_change_split_track" for issue in result.import_report.issues)


def test_channel_10_is_drums():
    result = import_midi_bytes(_simple_type1(channel=9), display_filename="drums.mid")
    assert result.composition.tracks[0].channel == 10
    assert result.composition.tracks[0].is_drum is True
    assert result.composition.tracks[0].role == "drums"


def test_sustain_and_expression_automation():
    mid = mido.MidiFile(type=0, ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.Message("control_change", control=11, value=127, time=0))
    track.append(mido.Message("control_change", control=64, value=127, time=0))
    track.append(mido.Message("note_on", note=60, velocity=90, time=0))
    track.append(mido.Message("control_change", control=11, value=64, time=240))
    track.append(mido.Message("control_change", control=64, value=0, time=240))
    track.append(mido.Message("note_off", note=60, velocity=0, time=0))
    track.append(mido.MetaMessage("end_of_track", time=0))
    parsed = parse_midi_bytes(_midi_bytes(mid)).score.tracks[0]
    assert parsed.expression == 127
    assert parsed.sustain_pedals
    assert parsed.sustain_pedals[0].start_tick == 0
    assert parsed.sustain_pedals[0].duration_ticks == 480
    assert parsed.automation
    assert parsed.automation[0].parameter == "expression"
    assert parsed.automation[0].points[0].tick == 240


def test_reject_empty_and_non_midi_and_oversize():
    with pytest.raises(CompositionImportError) as empty:
        parse_midi_bytes(b"")
    assert empty.value.http_status == 422

    with pytest.raises(CompositionImportError) as bad:
        parse_midi_bytes(b"not-midi")
    assert bad.value.code == "import_unsupported_media_type"
    assert bad.value.http_status == 415

    settings = load_import_settings({"IMPORT_MAX_UPLOAD_BYTES": "2048"})
    oversized = MIDI_HEADER_SIGNATURE + (b"\x00" * 3000)
    with pytest.raises(CompositionImportError) as huge:
        parse_midi_bytes(oversized, settings=settings)
    assert huge.value.http_status == 413


def test_truncated_midi_raises_malformed():
    data = _simple_type1()[:-8]
    with pytest.raises(CompositionImportError) as exc_info:
        parse_midi_bytes(data)
    assert exc_info.value.code == "import_malformed_source"


def test_pitch_bend_counted_as_unsupported_omission():
    mid = mido.MidiFile(type=0, ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.Message("note_on", note=60, velocity=90, time=0))
    track.append(mido.Message("pitchwheel", pitch=1000, time=0))
    track.append(mido.Message("note_off", note=60, velocity=0, time=480))
    track.append(mido.MetaMessage("end_of_track", time=0))
    result = import_midi_bytes(_midi_bytes(mid), display_filename="bend.mid")
    assert any(issue.code == "unsupported_midi_event_omitted" for issue in result.import_report.issues)
