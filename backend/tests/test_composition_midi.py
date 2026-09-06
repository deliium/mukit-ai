from app.schemas import Composition
from app.services.composition_midi import composition_to_midi_ready, render_midi


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
