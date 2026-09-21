"""DAW interoperability checklist for SMF Type 1 projection.

Inventory of what Ableton / Reaper / generic SMF importers expect from
``render_midi_with_report`` on the expressive V2 fixture.

Intentional omissions (do not invent notes or mid-track program changes):
- No mid-track ``program_change`` events (V2 has no timed program field).
- Linear automation is sampled (``automation_sampled``), not continuous curves.
- Harmony / motifs never invent playable MIDI notes.
- Sections project to markers only (lossy form labels); they never invent notes.
"""

from __future__ import annotations

import logging
from io import BytesIO

import mido
import pytest

from app.services.composition_midi import (
    CC_EXPRESSION,
    CC_PAN,
    CC_SUSTAIN,
    CC_VOLUME,
    render_midi_with_report,
)
from app.services.fixture_compositions import FIXTURE_V2_EXPRESSIVE, load_composition_fixture

logger = logging.getLogger(__name__)

# Stable checklist codes for DEBUG diagnostics (not projection report codes).
DAW_CHECKLIST_CODES = (
    "smf_type1_multitrack",
    "track_name",
    "channel_map",
    "program_at_tick0",
    "tempo_map",
    "time_signature_map",
    "markers",
    "section_markers",
    "cc_volume_pan_expression_sustain",
    "intentional_omissions",
)


def _parse_tracks(midi_bytes: bytes) -> mido.MidiFile:
    return mido.MidiFile(file=BytesIO(midi_bytes))


def _absolute_messages(track: mido.MidiTrack) -> list[tuple[int, mido.Message]]:
    absolute = 0
    out: list[tuple[int, mido.Message]] = []
    for message in track:
        absolute += message.time
        out.append((absolute, message))
    return out


def _first_track_name(track: mido.MidiTrack) -> str | None:
    for message in track:
        if message.is_meta and message.type == "track_name":
            return message.name
    return None


@pytest.fixture(scope="module")
def expressive_midi():
    composition = load_composition_fixture(FIXTURE_V2_EXPRESSIVE)
    result = render_midi_with_report(composition)
    mid = _parse_tracks(result.midi_bytes)
    logger.debug(
        "DAW checklist fixture rendered",
        extra={
            "checklist_codes": list(DAW_CHECKLIST_CODES),
            "smf_type": mid.type,
            "track_count": len(mid.tracks),
            "byte_length": len(result.midi_bytes),
            "projection_codes": result.report.compact_codes(),
        },
    )
    return composition, result, mid


def test_daw_smf_type1_multitrack(expressive_midi):
    composition, _result, mid = expressive_midi
    assert mid.type == 1
    # Conductor + one SMF track per V2 track
    assert len(mid.tracks) == 1 + len(composition.tracks)
    logger.debug("daw checklist ok", extra={"code": "smf_type1_multitrack"})


def test_daw_track_names(expressive_midi):
    composition, _result, mid = expressive_midi
    assert _first_track_name(mid.tracks[0]) == "Conductor"
    for track_index, v2_track in enumerate(composition.tracks):
        smf_track = mid.tracks[track_index + 1]
        assert _first_track_name(smf_track) == (v2_track.name or v2_track.id)
    logger.debug("daw checklist ok", extra={"code": "track_name"})


def test_daw_channel_and_program_at_tick0(expressive_midi):
    composition, _result, mid = expressive_midi
    for track_index, v2_track in enumerate(composition.tracks):
        smf_track = mid.tracks[track_index + 1]
        absolute = _absolute_messages(smf_track)
        note_channels = {
            msg.channel
            for _tick, msg in absolute
            if msg.type in {"note_on", "note_off", "control_change", "program_change"}
            and not msg.is_meta
        }
        assert note_channels == {v2_track.channel - 1}

        program_at_zero = [
            msg
            for tick, msg in absolute
            if tick == 0 and msg.type == "program_change"
        ]
        if v2_track.is_drum:
            assert program_at_zero == []
        else:
            assert len(program_at_zero) == 1
            assert program_at_zero[0].program == v2_track.midi_program
            assert program_at_zero[0].channel == v2_track.channel - 1

        # Intentional omission: no mid-track program changes.
        late_programs = [
            msg for tick, msg in absolute if tick > 0 and msg.type == "program_change"
        ]
        assert late_programs == []
    logger.debug(
        "daw checklist ok",
        extra={"code": "channel_map", "also": "program_at_tick0"},
    )


def test_daw_tempo_and_time_signature_maps(expressive_midi):
    composition, _result, mid = expressive_midi
    conductor = _absolute_messages(mid.tracks[0])

    tempo_events = [(tick, msg) for tick, msg in conductor if msg.type == "set_tempo"]
    assert any(tick == 0 for tick, _msg in tempo_events)
    # Expressive fixture tempo_changes at tick 3840 → 80 BPM
    assert any(tick == 3840 for tick, _msg in tempo_events)
    initial_bpm = round(mido.tempo2bpm(tempo_events[0][1].tempo))
    assert initial_bpm == composition.tempo

    meter_events = [(tick, msg) for tick, msg in conductor if msg.type == "time_signature"]
    assert any(tick == 0 for tick, _msg in meter_events)
    num, den = (int(part) for part in composition.time_signature.split("/"))
    tick0_meter = next(msg for tick, msg in meter_events if tick == 0)
    assert tick0_meter.numerator == num
    assert tick0_meter.denominator == den
    logger.debug(
        "daw checklist ok",
        extra={"code": "tempo_map", "also": "time_signature_map"},
    )


def test_daw_markers_from_composition_markers(expressive_midi):
    _composition, result, mid = expressive_midi
    conductor = _absolute_messages(mid.tracks[0])
    markers = [
        (tick, msg)
        for tick, msg in conductor
        if msg.is_meta and msg.type in {"marker", "text"}
    ]
    assert len(markers) >= 2
    texts = {msg.text for _tick, msg in markers}
    assert "A" in texts
    assert "rit." in texts
    assert "marker_normalized" in result.report.compact_codes()
    logger.debug("daw checklist ok", extra={"code": "markers"})


def test_daw_section_labels_exported_as_markers(expressive_midi):
    composition, result, mid = expressive_midi
    conductor = _absolute_messages(mid.tracks[0])
    texts = {
        msg.text
        for _tick, msg in conductor
        if msg.is_meta and msg.type in {"marker", "text"}
    }
    assert "Opening" in texts
    assert "Close" in texts
    assert any(
        tick == 3840 and msg.text == "Close"
        for tick, msg in conductor
        if msg.is_meta and msg.type == "marker"
    )
    assert "section_exported_as_marker" in result.report.compact_codes()
    assert len(composition.sections) >= 2
    logger.debug("daw checklist ok", extra={"code": "section_markers"})


def test_daw_cc_volume_pan_expression_sustain(expressive_midi):
    composition, result, mid = expressive_midi
    # Melody track has volume/pan/expression static + sustain + expression automation.
    melody_index = next(i for i, t in enumerate(composition.tracks) if t.id == "melody-1")
    smf_track = mid.tracks[melody_index + 1]
    absolute = _absolute_messages(smf_track)
    controls = {
        msg.control
        for _tick, msg in absolute
        if msg.type == "control_change"
    }
    assert CC_VOLUME in controls
    assert CC_PAN in controls
    assert CC_EXPRESSION in controls
    assert CC_SUSTAIN in controls

    codes = result.report.compact_codes()
    assert "automation_sampled" in codes
    assert "sustain_projected" in codes
    logger.debug(
        "daw checklist ok",
        extra={"code": "cc_volume_pan_expression_sustain"},
    )


def test_daw_intentional_omissions_documented(expressive_midi):
    """Lock known non-goals so DAW docs stay honest."""
    composition, result, mid = expressive_midi

    # Harmony never invents notes beyond track events.
    note_ons = sum(
        1
        for track in mid.tracks
        for _tick, msg in _absolute_messages(track)
        if msg.type == "note_on" and getattr(msg, "velocity", 0) > 0
    )
    authored_notes = sum(len(track.events) for track in composition.tracks)
    assert note_ons <= authored_notes
    assert len(composition.harmony) >= 1

    logger.debug(
        "daw checklist ok",
        extra={
            "code": "intentional_omissions",
            "automation_sampled": "automation_sampled" in result.report.compact_codes(),
            "mid_track_program_changes": False,
            "sections_as_markers_only": "section_exported_as_marker" in result.report.compact_codes(),
        },
    )
