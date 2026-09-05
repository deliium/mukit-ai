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
    assert composition.schema_version == "composition.v1"
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
