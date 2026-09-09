import json
from pathlib import Path

from app.schemas import Composition, CompositionV2, LLMMusicJson
from app.services.music_json_renderer import render_musicxml
from tests.fixtures.load_fixture import load_v2_expressive


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

    musicxml, report = render_musicxml(music)

    assert "score-partwise" in musicxml or "score-timewise" in musicxml
    assert "<fifths>-3</fifths>" in musicxml
    assert "<beats>4</beats>" in musicxml
    assert "<beat-type>4</beat-type>" in musicxml
    assert "<per-minute>92</per-minute>" in musicxml
    assert "Tonality: C minor | Dimension: 4/4 | Tempo: 92 BPM" not in musicxml
    assert report.status == "exact"
    assert report.issues == []


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

    musicxml, report = render_musicxml(music)

    assert report.status == "exact"
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

    musicxml, report = render_musicxml(composition)

    assert report.status == "exact"
    assert "<pitch>" in musicxml
    assert "<harmony" in musicxml
    assert "<rest measure=\"yes\" />" in musicxml
    assert "<rest" in musicxml


def test_render_musicxml_canonical_bass_track_uses_f_clef():
    composition = Composition.model_validate(
        {
            "schema_version": "composition.v1",
            "tempo": 100,
            "key": "C major",
            "time_signature": "4/4",
            "ticks_per_quarter": 480,
            "bar_count": 1,
            "duration_ticks": 1920,
            "sections": [{"type": "intro", "start_bar": 1, "bar_count": 1, "start_tick": 0, "duration_ticks": 1920}],
            "tracks": [
                {
                    "id": "melody-1",
                    "name": "Flute",
                    "instrument": "flute",
                    "role": "melody",
                    "midi_program": 73,
                    "channel": 1,
                    "events": [
                        {
                            "type": "note",
                            "pitch": "C5",
                            "start_tick": 0,
                            "duration_ticks": 480,
                            "velocity": 90,
                        }
                    ],
                },
                {
                    "id": "bass-2",
                    "name": "Bass",
                    "instrument": "electric bass",
                    "role": "bass",
                    "midi_program": 33,
                    "channel": 2,
                    "events": [
                        {
                            "type": "note",
                            "pitch": "C2",
                            "start_tick": 0,
                            "duration_ticks": 960,
                            "velocity": 85,
                        }
                    ],
                },
            ],
            "harmony": [{"bar": 1, "chord": "C"}],
        }
    )

    musicxml, report = render_musicxml(composition)

    assert report.status == "exact"
    assert "<sign>G</sign>" in musicxml
    assert "<sign>F</sign>" in musicxml


def test_render_musicxml_canonical_staff_bass_uses_f_clef_without_bass_role():
    composition = Composition.model_validate(
        {
            "schema_version": "composition.v1",
            "tempo": 96,
            "key": "A minor",
            "time_signature": "4/4",
            "ticks_per_quarter": 480,
            "bar_count": 1,
            "duration_ticks": 1920,
            "sections": [{"type": "intro", "start_bar": 1, "bar_count": 1, "start_tick": 0, "duration_ticks": 1920}],
            "tracks": [
                {
                    "id": "low-1",
                    "name": "Low Strings",
                    "instrument": "strings",
                    "role": "pad",
                    "midi_program": 48,
                    "channel": 1,
                    "staff": "bass",
                    "events": [
                        {
                            "type": "note",
                            "pitch": "A2",
                            "start_tick": 0,
                            "duration_ticks": 1920,
                            "velocity": 70,
                        }
                    ],
                }
            ],
            "harmony": [{"bar": 1, "chord": "Am"}],
        }
    )

    musicxml, report = render_musicxml(composition)

    assert report.status == "exact"
    assert "<sign>F</sign>" in musicxml


def test_render_musicxml_canonical_piano_grand_has_g_and_f_clefs():
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
                    "staff": "grand",
                    "events": [
                        {
                            "type": "note",
                            "pitch": "A4",
                            "start_tick": 0,
                            "duration_ticks": 480,
                            "velocity": 90,
                            "staff": "treble",
                        },
                        {
                            "type": "note",
                            "pitch": "A2",
                            "start_tick": 0,
                            "duration_ticks": 960,
                            "velocity": 80,
                            "staff": "bass",
                        },
                    ],
                }
            ],
            "harmony": [{"bar": 1, "chord": "Am"}],
        }
    )

    musicxml, report = render_musicxml(composition)

    assert report.status == "exact"
    assert "<sign>G</sign>" in musicxml
    assert "<sign>F</sign>" in musicxml


def test_render_musicxml_legacy_bass_track_uses_f_clef():
    music = LLMMusicJson.model_validate(
        {
            "tempo": 100,
            "key": "C major",
            "time_signature": "4/4",
            "sections": [{"type": "intro", "bars": 1}],
            "tracks": [
                {"instrument": "flute", "role": "melody"},
                {"instrument": "electric bass", "role": "bass"},
            ],
            "harmony": [{"bar": 1, "chord": "C"}],
        }
    )

    musicxml, report = render_musicxml(music)

    assert report.status == "exact"
    assert "<sign>G</sign>" in musicxml
    assert "<sign>F</sign>" in musicxml


def test_render_musicxml_v2_expressive_emits_semantics_and_projection_issues():
    composition = load_v2_expressive()
    musicxml, report = render_musicxml(composition)

    assert "<per-minute>100</per-minute>" in musicxml
    assert "<per-minute>80</per-minute>" in musicxml
    assert "Opening" in musicxml
    assert "rit." in musicxml
    assert "<rehearsal" in musicxml.lower()
    assert "<articulations>" in musicxml or "accent" in musicxml.lower()
    assert "staccato" in musicxml.lower()
    assert "<dynamics>" in musicxml or "mf" in musicxml.lower()
    assert "pedal" in musicxml.lower()
    assert "automation_omitted_from_notation" in report.compact_codes()
    assert report.omitted_count >= 1


def test_render_musicxml_v2_semantic_tie_fragments_at_barlines():
    composition = CompositionV2.model_validate(
        {
            "schema_version": "composition.v2",
            "tempo": 100,
            "key": "C major",
            "time_signature": "4/4",
            "ticks_per_quarter": 480,
            "bar_count": 2,
            "duration_ticks": 3840,
            "sections": [
                {
                    "id": "s1",
                    "type": "intro",
                    "start_bar": 1,
                    "bar_count": 2,
                    "start_tick": 0,
                    "duration_ticks": 3840,
                }
            ],
            "tracks": [
                {
                    "id": "melody-1",
                    "name": "Melody",
                    "instrument": "flute",
                    "role": "melody",
                    "midi_program": 73,
                    "channel": 1,
                    "events": [
                        {
                            "type": "note",
                            "pitch": "G4",
                            "start_tick": 1440,
                            "duration_ticks": 480,
                            "velocity": 90,
                            "tie": {"group_id": "tie-1", "type": "start"},
                        },
                        {
                            "type": "note",
                            "pitch": "G4",
                            "start_tick": 1920,
                            "duration_ticks": 960,
                            "velocity": 84,
                            "tie": {"group_id": "tie-1", "type": "stop"},
                        },
                    ],
                }
            ],
            "harmony": [],
        }
    )

    musicxml, report = render_musicxml(composition)

    assert report.status == "exact"
    assert "<tie type=\"start\"" in musicxml
    assert "<tied type=\"start\"" in musicxml
    assert "<tie type=\"stop\"" in musicxml


def test_render_musicxml_v2_variable_meter_inserts_attributes_in_all_parts():
    composition = CompositionV2.model_validate(
        {
            "schema_version": "composition.v2",
            "tempo": 120,
            "key": "C major",
            "time_signature": "4/4",
            "ticks_per_quarter": 480,
            "bar_count": 2,
            "duration_ticks": 3360,
            "sections": [
                {
                    "id": "s1",
                    "type": "intro",
                    "start_bar": 1,
                    "bar_count": 2,
                    "start_tick": 0,
                    "duration_ticks": 3360,
                }
            ],
            "tracks": [
                {
                    "id": "t1",
                    "name": "Melody",
                    "instrument": "flute",
                    "role": "melody",
                    "midi_program": 73,
                    "channel": 1,
                    "events": [
                        {
                            "type": "note",
                            "pitch": "C5",
                            "start_tick": 0,
                            "duration_ticks": 480,
                            "velocity": 90,
                        },
                        {
                            "type": "note",
                            "pitch": "D5",
                            "start_tick": 1920,
                            "duration_ticks": 480,
                            "velocity": 90,
                        },
                    ],
                },
                {
                    "id": "t2",
                    "name": "Bass",
                    "instrument": "bass",
                    "role": "bass",
                    "midi_program": 33,
                    "channel": 2,
                    "events": [
                        {
                            "type": "note",
                            "pitch": "C3",
                            "start_tick": 1920,
                            "duration_ticks": 480,
                            "velocity": 80,
                        }
                    ],
                },
            ],
            "harmony": [],
            "time_signature_changes": [{"tick": 1920, "time_signature": "3/4"}],
            "key_changes": [{"tick": 1920, "key": "G major"}],
        }
    )

    musicxml, report = render_musicxml(composition)

    assert report.status == "exact"
    assert musicxml.count("<beats>4</beats>") >= 1
    assert musicxml.count("<beats>3</beats>") >= 1
    assert musicxml.count("<fifths>0</fifths>") >= 1
    assert musicxml.count("<fifths>1</fifths>") >= 1


def test_render_musicxml_v2_fixture_file_roundtrip():
    fixture_path = Path(__file__).parent / "fixtures" / "composition_v2_expressive.json"
    composition = CompositionV2.model_validate(json.loads(fixture_path.read_text(encoding="utf-8")))
    musicxml, report = render_musicxml(composition)
    assert len(musicxml) > 500
    assert "score-partwise" in musicxml or "score-timewise" in musicxml
    assert "automation_omitted_from_notation" in report.compact_codes()


def test_render_musicxml_reports_motif_metadata_omitted():
    from tests.test_composition_v2_schema import _motif_definition, _motif_track, minimal_v2

    composition = CompositionV2.model_validate(
        minimal_v2(tracks=[_motif_track()], motifs=[_motif_definition()])
    )
    musicxml, report = render_musicxml(composition)
    assert "score-partwise" in musicxml or "score-timewise" in musicxml
    assert "motif_metadata_omitted" in report.compact_codes()
    issue = next(item for item in report.issues if item.code == "motif_metadata_omitted")
    assert issue.status == "omitted"
    assert issue.details["motif_count"] == 1


def test_render_musicxml_skips_motif_omission_for_old_v2_without_motifs():
    from tests.test_composition_v2_schema import _motif_track, minimal_v2

    composition = CompositionV2.model_validate(minimal_v2(tracks=[_motif_track()]))
    _musicxml, report = render_musicxml(composition)
    assert "motif_metadata_omitted" not in report.compact_codes()
