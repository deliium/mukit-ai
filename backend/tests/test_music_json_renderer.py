from app.schemas import Composition, LLMMusicJson
from app.services.music_json_renderer import render_musicxml


def test_render_musicxml_from_valid_music_json():
    music = LLMMusicJson.model_validate(
        {
            "tempo": 92,
            "key": "C minor",
            "time_signature": "4/4",
            "sections": [{"type": "intro", "bars": 2}],
            "tracks": [{"instrument": "piano", "role": "harmony"}],
            "harmony": [{"bar": 1, "chord": "Cm"}, {"bar": 2, "chord": "Ab"}],
        }
    )

    musicxml, warnings = render_musicxml(music)

    assert "score-partwise" in musicxml or "score-timewise" in musicxml
    assert "<fifths>-3</fifths>" in musicxml
    assert "<beats>4</beats>" in musicxml
    assert "<beat-type>4</beat-type>" in musicxml
    assert "<per-minute>92</per-minute>" in musicxml
    assert "Tonality: C minor | Dimension: 4/4 | Tempo: 92 BPM" not in musicxml
    assert warnings == []


def test_render_musicxml_piano_harmony_chords_as_notes():
    music = LLMMusicJson.model_validate(
        {
            "tempo": 88,
            "key": "A minor",
            "time_signature": "4/4",
            "sections": [{"type": "intro", "bars": 4}],
            "tracks": [{"instrument": "piano", "role": "harmony"}],
            "harmony": [
                {"bar": 1, "chord": "Am"},
                {"bar": 2, "chord": "F"},
                {"bar": 3, "chord": "C"},
                {"bar": 4, "chord": "G"},
            ],
            "notes": [
                {"track": 1, "staff": "treble", "bar": 1, "beat": 1, "pitch": "A4", "duration": 1},
                {"track": 1, "staff": "treble", "bar": 1, "beat": 1, "pitch": "C5", "duration": 1},
                {"track": 1, "staff": "treble", "bar": 1, "beat": 1, "pitch": "E5", "duration": 1},
                {"track": 1, "staff": "bass", "bar": 1, "beat": 1, "pitch": "A2", "duration": 2},
                {"track": 1, "staff": "bass", "bar": 1, "beat": 3, "pitch": "E3", "duration": 2},
                {"track": 1, "staff": "treble", "bar": 2, "beat": 1, "pitch": "F4", "duration": 1},
                {"track": 1, "staff": "treble", "bar": 2, "beat": 1, "pitch": "A4", "duration": 1},
                {"track": 1, "staff": "treble", "bar": 2, "beat": 1, "pitch": "C5", "duration": 1},
                {"track": 1, "staff": "bass", "bar": 2, "beat": 1, "pitch": "F2", "duration": 4},
            ],
        }
    )

    musicxml, warnings = render_musicxml(music)

    assert warnings == []
    assert "<note" in musicxml
    assert "<pitch>" in musicxml
    assert "<harmony" in musicxml
    assert "Piano" in musicxml


def test_render_musicxml_canonical_uses_events_without_harmony_fallback():
    composition = Composition.model_validate(
        {
            "schema_version": "composition.v1",
            "tempo": 88,
            "key": "A minor",
            "time_signature": "4/4",
            "ticks_per_quarter": 480,
            "bar_count": 1,
            "duration_ticks": 1920,
            "sections": [{"type": "intro", "start_bar": 1, "bar_count": 1, "start_tick": 0, "duration_ticks": 1920}],
            "tracks": [
                {
                    "id": "piano-1",
                    "name": "Piano",
                    "instrument": "piano",
                    "role": "harmony",
                    "midi_program": 0,
                    "channel": 1,
                    "events": [
                        {"type": "note", "pitch": "A4", "start_tick": 0, "duration_ticks": 480, "velocity": 90, "staff": "treble"},
                        {"type": "note", "pitch": "C5", "start_tick": 0, "duration_ticks": 480, "velocity": 90, "staff": "treble"},
                    ],
                },
                {
                    "id": "strings-2",
                    "name": "Strings",
                    "instrument": "strings",
                    "role": "pad",
                    "midi_program": 48,
                    "channel": 2,
                    "events": [],
                },
            ],
            "harmony": [{"bar": 1, "chord": "Am"}],
        }
    )

    musicxml, warnings = render_musicxml(composition)

    assert warnings == []
    assert "<pitch>" in musicxml
    assert "<harmony" in musicxml
    assert "<rest measure=\"yes\" />" in musicxml
    assert "<rest" in musicxml
