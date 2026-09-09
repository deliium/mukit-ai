from __future__ import annotations

from io import BytesIO

import pytest

from app.schemas import Composition, CompositionV2
from app.services.composition_migration import migrate_v1_to_v2
from app.services.composition_midi import (
    CompositionMidiError,
    automation_sample_interval,
    composition_to_midi_ready,
    render_midi,
    render_midi_with_report,
)
from app.services.fixture_compositions import FIXTURE_V2_EXPRESSIVE, load_composition_fixture


def _parse_midi_absolute_events(midi_bytes: bytes) -> list[dict]:
    import mido

    mid = mido.MidiFile(file=BytesIO(midi_bytes))
    events: list[dict] = []
    for track_index, track in enumerate(mid.tracks):
        absolute_tick = 0
        for message in track:
            absolute_tick += message.time
            payload = {
                "track_index": track_index,
                "tick": absolute_tick,
                "type": message.type,
            }
            if message.is_meta:
                payload["meta"] = message.type
                if hasattr(message, "name"):
                    payload["name"] = message.name
                if message.type == "set_tempo":
                    payload["tempo"] = message.tempo
                if message.type in {"marker", "text"}:
                    payload["text"] = message.text
            else:
                payload["channel"] = message.channel
                if message.type == "note_on":
                    payload["note"] = message.note
                    payload["velocity"] = message.velocity
                elif message.type == "note_off":
                    payload["note"] = message.note
                elif message.type == "control_change":
                    payload["control"] = message.control
                    payload["value"] = message.value
            events.append(payload)
    return events


def test_midi_ready_mapping_preserves_track_and_event_metadata():
    composition = Composition.model_validate(
        {
            "schema_version": "composition.v1",
            "tempo": 110,
            "key": "C major",
            "time_signature": "4/4",
            "ticks_per_quarter": 480,
            "bar_count": 1,
            "duration_ticks": 1920,
            "sections": [{"type": "intro", "start_bar": 1, "bar_count": 1, "start_tick": 0, "duration_ticks": 1920}],
            "tracks": [
                {
                    "id": "bass-1",
                    "name": "Bass",
                    "instrument": "bass",
                    "role": "bass",
                    "midi_program": 32,
                    "channel": 2,
                    "events": [{"type": "note", "pitch": "C2", "start_tick": 240, "duration_ticks": 960, "velocity": 101}],
                }
            ],
            "harmony": [],
        }
    )

    midi_ready = composition_to_midi_ready(composition)

    assert midi_ready["ticks_per_quarter"] == 480
    assert midi_ready["tracks"][0]["midi_program"] == 32
    assert midi_ready["tracks"][0]["channel"] == 2
    assert midi_ready["tracks"][0]["events"][0]["velocity"] == 101


def test_render_midi_produces_standard_midi_file_bytes():
    composition = Composition.model_validate(
        {
            "schema_version": "composition.v1",
            "tempo": 110,
            "key": "C major",
            "time_signature": "4/4",
            "ticks_per_quarter": 480,
            "bar_count": 1,
            "duration_ticks": 1920,
            "sections": [{"type": "intro", "start_bar": 1, "bar_count": 1, "start_tick": 0, "duration_ticks": 1920}],
            "tracks": [
                {
                    "id": "bass-1",
                    "name": "Bass",
                    "instrument": "bass",
                    "role": "bass",
                    "midi_program": 32,
                    "channel": 2,
                    "volume": 100,
                    "pan": 0,
                    "events": [
                        {"type": "note", "pitch": "C2", "start_tick": 0, "duration_ticks": 480, "velocity": 101},
                        {"type": "note", "pitch": "E2", "start_tick": 0, "duration_ticks": 480, "velocity": 90},
                    ],
                }
            ],
            "harmony": [],
        }
    )

    midi_bytes = render_midi(composition)

    assert midi_bytes[:4] == b"MThd"
    assert len(midi_bytes) > 40


def test_v1_and_migrated_v2_midi_note_equality():
    v1 = Composition.model_validate(
        {
            "schema_version": "composition.v1",
            "tempo": 120,
            "key": "C major",
            "time_signature": "4/4",
            "ticks_per_quarter": 480,
            "bar_count": 1,
            "duration_ticks": 1920,
            "sections": [{"type": "intro", "start_bar": 1, "bar_count": 1, "start_tick": 0, "duration_ticks": 1920}],
            "tracks": [
                {
                    "id": "t1",
                    "name": "Piano",
                    "instrument": "piano",
                    "role": "harmony",
                    "midi_program": 0,
                    "channel": 1,
                    "events": [
                        {"type": "note", "pitch": "C4", "start_tick": 0, "duration_ticks": 480, "velocity": 80},
                        {"type": "note", "pitch": "E4", "start_tick": 480, "duration_ticks": 480, "velocity": 82},
                    ],
                }
            ],
            "harmony": [],
        }
    )
    v2 = migrate_v1_to_v2(v1).composition
    from tests.test_export_fidelity import midi_note_tuples_from_bytes

    v1_notes = midi_note_tuples_from_bytes(render_midi(v1), v2)
    v2_notes = midi_note_tuples_from_bytes(render_midi(v2), v2)
    assert v1_notes == v2_notes


def test_v2_expressive_projection_emits_controllers_and_tempo_change():
    composition = load_composition_fixture(FIXTURE_V2_EXPRESSIVE)
    result = render_midi_with_report(composition)
    events = _parse_midi_absolute_events(result.midi_bytes)

    tempo_events = [event for event in events if event.get("meta") == "set_tempo"]
    assert any(event["tick"] == 0 for event in tempo_events)
    assert any(event["tick"] == 3840 for event in tempo_events)

    cc64 = [event for event in events if event.get("control") == 64]
    assert any(event["tick"] == 0 and event["value"] == 127 for event in cc64)
    assert any(event["tick"] == 1920 and event["value"] == 0 for event in cc64)

    markers = [event for event in events if event.get("meta") in {"marker", "text"}]
    assert len(markers) >= 2

    assert "articulation_transformed" in result.report.compact_codes()
    assert "sustain_projected" in result.report.compact_codes()
    assert "automation_sampled" in result.report.compact_codes()
    assert "marker_normalized" in result.report.compact_codes()


def test_same_tick_ordering_note_off_before_note_on():
    composition = CompositionV2.model_validate(
        {
            "schema_version": "composition.v2",
            "tempo": 120,
            "key": "C major",
            "time_signature": "4/4",
            "ticks_per_quarter": 480,
            "bar_count": 1,
            "duration_ticks": 1920,
            "sections": [{"type": "intro", "start_bar": 1, "bar_count": 1, "start_tick": 0, "duration_ticks": 1920}],
            "tracks": [
                {
                    "id": "t1",
                    "name": "Piano",
                    "instrument": "piano",
                    "role": "harmony",
                    "midi_program": 0,
                    "channel": 1,
                    "events": [
                        {"type": "note", "pitch": "C4", "start_tick": 0, "duration_ticks": 480, "velocity": 80},
                        {"type": "note", "pitch": "D4", "start_tick": 480, "duration_ticks": 480, "velocity": 80},
                    ],
                }
            ],
            "harmony": [],
        }
    )
    events = _parse_midi_absolute_events(render_midi(composition))
    at_480 = [event for event in events if event["tick"] == 480 and event["type"] in {"note_on", "note_off"}]
    assert at_480[0]["type"] == "note_off"
    assert at_480[1]["type"] == "note_on"


def test_tie_collapse_and_staccato_gate_in_midi():
    composition = load_composition_fixture(FIXTURE_V2_EXPRESSIVE)
    from tests.test_export_fidelity import midi_note_tuples_from_bytes

    notes = midi_note_tuples_from_bytes(render_midi(composition), composition)
    melody = [note for note in notes if note.track_id == "melody-1"]
    staccato = next(note for note in melody if note.pitch == "E4")
    assert staccato.duration_ticks == 240
    tied = next(note for note in melody if note.pitch == "G4")
    assert tied.start_tick == 960
    assert tied.duration_ticks == 960


def test_shared_channel_control_conflict_rejected():
    composition = CompositionV2.model_validate(
        {
            "schema_version": "composition.v2",
            "tempo": 120,
            "key": "C major",
            "time_signature": "4/4",
            "ticks_per_quarter": 480,
            "bar_count": 1,
            "duration_ticks": 1920,
            "sections": [{"type": "intro", "start_bar": 1, "bar_count": 1, "start_tick": 0, "duration_ticks": 1920}],
            "tracks": [
                {
                    "id": "a",
                    "name": "A",
                    "instrument": "piano",
                    "role": "harmony",
                    "midi_program": 0,
                    "channel": 1,
                    "volume": 100,
                    "events": [{"type": "note", "pitch": "C4", "start_tick": 0, "duration_ticks": 480, "velocity": 80}],
                    "sustain_pedals": [{"start_tick": 0, "duration_ticks": 480}],
                },
                {
                    "id": "b",
                    "name": "B",
                    "instrument": "piano",
                    "role": "harmony",
                    "midi_program": 0,
                    "channel": 1,
                    "volume": 90,
                    "events": [{"type": "note", "pitch": "E4", "start_tick": 480, "duration_ticks": 480, "velocity": 80}],
                },
            ],
            "harmony": [],
        }
    )
    with pytest.raises(CompositionMidiError, match="channel 1"):
        render_midi_with_report(composition)


def test_tempo_quantization_reported_for_non_roundtrip_bpm():
    composition = CompositionV2.model_validate(
        {
            "schema_version": "composition.v2",
            "tempo": 103,
            "key": "C major",
            "time_signature": "4/4",
            "ticks_per_quarter": 480,
            "bar_count": 1,
            "duration_ticks": 1920,
            "sections": [{"type": "intro", "start_bar": 1, "bar_count": 1, "start_tick": 0, "duration_ticks": 1920}],
            "tracks": [
                {
                    "id": "t1",
                    "name": "Piano",
                    "instrument": "piano",
                    "role": "harmony",
                    "midi_program": 0,
                    "channel": 1,
                    "events": [{"type": "note", "pitch": "C4", "start_tick": 0, "duration_ticks": 480, "velocity": 80}],
                }
            ],
            "harmony": [],
        }
    )
    import mido

    result = render_midi_with_report(composition)
    tempo_events = [event for event in _parse_midi_absolute_events(result.midi_bytes) if event.get("meta") == "set_tempo"]
    effective = round(mido.tempo2bpm(tempo_events[0]["tempo"]))
    if effective != 103:
        assert "tempo_quantized" in result.report.compact_codes()


def test_automation_sample_interval_matches_policy():
    assert automation_sample_interval(480) == 30
    assert automation_sample_interval(16) == 1


def test_midi_projection_reports_motif_metadata_omitted():
    from tests.test_composition_v2_schema import _motif_definition, _motif_track, minimal_v2

    composition = CompositionV2.model_validate(
        minimal_v2(tracks=[_motif_track()], motifs=[_motif_definition()])
    )
    result = render_midi_with_report(composition)
    assert result.midi_bytes[:4] == b"MThd"
    assert "motif_metadata_omitted" in result.report.compact_codes()
    issue = next(item for item in result.report.issues if item.code == "motif_metadata_omitted")
    assert issue.status == "omitted"
    assert issue.path == "motifs"
    assert issue.details["motif_count"] == 1
    assert issue.details["occurrence_count"] == 1


def test_midi_projection_skips_motif_omission_without_motifs():
    from tests.test_composition_v2_schema import _motif_track, minimal_v2

    composition = CompositionV2.model_validate(minimal_v2(tracks=[_motif_track()]))
    result = render_midi_with_report(composition)
    assert "motif_metadata_omitted" not in result.report.compact_codes()


def test_arranged_candidate_midi_preserves_explicit_programs():
    """MIDI export uses track midi_program, not role-inferred remapping."""
    import asyncio

    from app.arrangement_schemas import CompositionArrangementPreviewRequest
    from app.llm_settings import LLMProviderSettings, LLMSettings
    from app.schemas import LLMModelSelection
    from app.services.llm_composition_arrangement import run_composition_arrangement_preview
    from tests.test_composition_arrangement_context import (
        _acceptance_instrumentation,
        _piano_sketch_v2,
    )

    settings = LLMSettings(
        providers=(
            LLMProviderSettings(
                provider="fake",
                model="fake-deterministic",
                api_key="unused",
                is_default=True,
            ),
        ),
        default_provider="fake",
        request_timeout_seconds=30,
        temperature=0.4,
    )
    request = CompositionArrangementPreviewRequest.model_validate(
        {
            "composition": _piano_sketch_v2(),
            "operation": "piano_to_ensemble",
            "source_track_ids": ["piano-melody", "piano-accomp", "bass-1"],
            "instrumentation": _acceptance_instrumentation(),
            "candidate_count": 1,
            "selection": LLMModelSelection(provider="fake", model="fake-deterministic"),
        }
    )
    import os

    os.environ["LLM_FAKE_MODE"] = "1"
    response = asyncio.run(run_composition_arrangement_preview(request, settings=settings))
    candidate = response.candidates[0].composition
    result = render_midi_with_report(candidate)
    assert result.midi_bytes[:4] == b"MThd"

    # Cello (42) and string ensemble (48) programs must appear when present on tracks.
    programs = {track.midi_program for track in candidate.tracks}
    assert 42 in programs or any("cello" in t.instrument.lower() for t in candidate.tracks)
    midi_ready = composition_to_midi_ready(candidate)
    for track, ready in zip(candidate.tracks, midi_ready["tracks"], strict=True):
        assert ready["midi_program"] == track.midi_program
        assert ready["channel"] == track.channel
