import pytest

from app.services.composition_normalizer import CompositionNormalizationError, normalize_composition_json


def legacy_music_json(notes=None):
    return {
        "tempo": 96,
        "key": "A minor",
        "time_signature": "4/4",
        "sections": [{"type": "intro", "bars": 2}],
        "tracks": [{"instrument": "piano", "role": "harmony"}],
        "harmony": [{"bar": 1, "chord": "Am"}],
        "notes": notes if notes is not None else [
            {"track": 1, "staff": "treble", "bar": 2, "beat": 1.5, "pitch": "A4", "duration": 0.5},
        ],
    }


def test_normalizer_migrates_legacy_notes_and_defaults_velocity(caplog):
    with caplog.at_level("WARNING"):
        composition = normalize_composition_json(legacy_music_json())

    event = composition.tracks[0].events[0]
    assert composition.schema_version == "composition.v2"
    assert event.start_tick == 2160
    assert event.duration_ticks == 240
    assert event.velocity == 80
    assert "Defaulting missing legacy note velocity" in caplog.text


def test_normalizer_preserves_legacy_velocity():
    composition = normalize_composition_json(
        legacy_music_json([{"track": 1, "staff": "treble", "bar": 1, "beat": 1, "pitch": "C4", "duration": 1, "velocity": 99}])
    )

    assert composition.tracks[0].events[0].velocity == 99


def test_normalizer_rejects_legacy_harmony_only_json():
    with pytest.raises(CompositionNormalizationError):
        normalize_composition_json(legacy_music_json([]))


def test_normalizer_returns_canonical_composition_unchanged():
    composition = normalize_composition_json(legacy_music_json())

    assert normalize_composition_json(composition) is composition


def test_midi_program_mapping_covers_common_gm_instruments():
    from app.services.composition_normalizer import _midi_program_for_instrument

    assert _midi_program_for_instrument("Acoustic Grand Piano") == 0
    assert _midi_program_for_instrument("Electric Bass") == 32
    assert _midi_program_for_instrument("Clarinet Solo") == 71
    assert _midi_program_for_instrument("Oboe") == 68
    assert _midi_program_for_instrument("Church Organ") == 19
    assert _midi_program_for_instrument("Orchestral Harp") == 46
    assert _midi_program_for_instrument("Unknown Widget") == 0


def test_canonical_normalization_preserves_same_instrument_multi_role_tracks():
    payload = {
        "schema_version": "composition.v1",
        "tempo": 100,
        "key": "C major",
        "time_signature": "4/4",
        "ticks_per_quarter": 480,
        "bar_count": 2,
        "duration_ticks": 3840,
        "sections": [
            {"type": "intro", "start_bar": 1, "bar_count": 2, "start_tick": 0, "duration_ticks": 3840},
        ],
        "tracks": [
            {
                "id": "melody-1",
                "name": "Melody",
                "instrument": "piano",
                "role": "melody",
                "midi_program": 0,
                "channel": 1,
                "events": [
                    {"type": "note", "pitch": "C5", "start_tick": 0, "duration_ticks": 480, "velocity": 80},
                ],
            },
            {
                "id": "harmony-1",
                "name": "Piano Accompaniment",
                "instrument": "piano",
                "role": "harmony",
                "midi_program": 0,
                "channel": 2,
                "events": [
                    {"type": "note", "pitch": "C3", "start_tick": 0, "duration_ticks": 960, "velocity": 70},
                ],
            },
        ],
        "harmony": [{"bar": 1, "chord": "C"}],
    }
    composition = normalize_composition_json(payload)
    assert len(composition.tracks) == 2
    assert [track.instrument for track in composition.tracks] == ["piano", "piano"]
    assert [track.role for track in composition.tracks] == ["melody", "harmony"]
    assert [track.id for track in composition.tracks] == ["melody-1", "harmony-1"]


def test_normalizer_preserves_empty_motifs_on_v2():
    from tests.test_composition_v2_schema import minimal_v2

    composition = normalize_composition_json(minimal_v2())
    assert composition.schema_version == "composition.v2"
    assert composition.motifs == []


def test_normalizer_preserves_valid_motif_references():
    from tests.test_composition_v2_schema import (
        _motif_definition,
        _motif_track,
        minimal_v2,
    )

    payload = minimal_v2(tracks=[_motif_track()], motifs=[_motif_definition()])
    composition = normalize_composition_json(payload)
    assert len(composition.motifs) == 1
    assert composition.motifs[0].label == "Motif A"
    assert composition.motifs[0].occurrences[0].event_ids == ["n1", "n2", "n3"]
