import logging

from app.schemas import Composition
from app.services.composition_validator import validate_composition_integrity


def _base_composition(**overrides) -> dict:
    payload = {
        "schema_version": "composition.v1",
        "tempo": 80,
        "key": "A minor",
        "time_signature": "4/4",
        "ticks_per_quarter": 480,
        "bar_count": 4,
        "duration_ticks": 7680,
        "sections": [
            {
                "type": "intro",
                "start_bar": 1,
                "bar_count": 2,
                "start_tick": 0,
                "duration_ticks": 3840,
            },
            {
                "type": "outro",
                "start_bar": 3,
                "bar_count": 2,
                "start_tick": 3840,
                "duration_ticks": 3840,
            },
        ],
        "tracks": [
            {
                "id": "melody-1",
                "name": "Melody",
                "instrument": "piano",
                "role": "melody",
                "midi_program": 0,
                "channel": 1,
                "staff": "treble",
                "events": [
                    {"pitch": "A4", "start_tick": 0, "duration_ticks": 480, "velocity": 80},
                    {"pitch": "C5", "start_tick": 1920, "duration_ticks": 480, "velocity": 80},
                    {"pitch": "E5", "start_tick": 3840, "duration_ticks": 480, "velocity": 80},
                    {"pitch": "A4", "start_tick": 5760, "duration_ticks": 480, "velocity": 80},
                ],
            },
            {
                "id": "bass-1",
                "name": "Bass",
                "instrument": "bass",
                "role": "bass",
                "midi_program": 32,
                "channel": 2,
                "events": [
                    {"pitch": "A2", "start_tick": 0, "duration_ticks": 1920, "velocity": 84},
                    {"pitch": "E2", "start_tick": 1920, "duration_ticks": 1920, "velocity": 84},
                    {"pitch": "A2", "start_tick": 3840, "duration_ticks": 1920, "velocity": 84},
                    {"pitch": "E2", "start_tick": 5760, "duration_ticks": 1920, "velocity": 84},
                ],
            },
            {
                "id": "harmony-1",
                "name": "Piano Accompaniment",
                "instrument": "piano",
                "role": "harmony",
                "midi_program": 0,
                "channel": 3,
                "staff": "grand",
                "events": [
                    {"pitch": "A3", "start_tick": 0, "duration_ticks": 960, "velocity": 70, "staff": "bass"},
                    {"pitch": "C5", "start_tick": 0, "duration_ticks": 960, "velocity": 68, "staff": "treble"},
                    {"pitch": "E3", "start_tick": 1920, "duration_ticks": 960, "velocity": 70, "staff": "bass"},
                    {"pitch": "A4", "start_tick": 3840, "duration_ticks": 960, "velocity": 70, "staff": "treble"},
                ],
            },
        ],
        "harmony": [
            {"bar": 1, "chord": "Am"},
            {"bar": 3, "chord": "E"},
        ],
    }
    payload.update(overrides)
    return payload


def test_validator_accepts_valid_composition(caplog):
    caplog.set_level(logging.INFO)
    result = validate_composition_integrity(
        Composition.model_validate(_base_composition()),
        requested_instruments=["piano", "bass"],
        complexity="simple",
    )
    assert result.ok
    assert result.error_codes() == []
    assert "Composition integrity validation passed" in caplog.text


def test_validator_rejects_missing_melody(caplog):
    caplog.set_level(logging.WARNING)
    payload = _base_composition()
    payload["tracks"] = [track for track in payload["tracks"] if track["role"] != "melody"]
    result = validate_composition_integrity(payload, complexity="simple")
    assert not result.ok
    assert "missing_required_track" in result.error_codes()
    assert "Composition integrity validation failed" in caplog.text


def test_validator_rejects_empty_required_track():
    payload = _base_composition()
    payload["tracks"][0]["events"] = []
    result = validate_composition_integrity(payload, complexity="simple")
    assert not result.ok
    assert "empty_required_track" in result.error_codes()


def test_validator_rejects_pitch_out_of_range():
    payload = _base_composition()
    payload["tracks"][1]["events"][0]["pitch"] = "C5"  # too high for bass
    result = validate_composition_integrity(payload, complexity="simple")
    assert not result.ok
    assert "event_out_of_range" in result.error_codes()


def test_validator_allows_strings_cello_register():
    payload = _base_composition()
    payload["tracks"].append(
        {
            "id": "strings-1",
            "name": "Strings",
            "instrument": "strings",
            "role": "pad",
            "midi_program": 48,
            "channel": 3,
            "events": [
                {"pitch": "E2", "start_tick": 0, "duration_ticks": 1920, "velocity": 60},
                {"pitch": "A2", "start_tick": 1920, "duration_ticks": 1920, "velocity": 60},
                {"pitch": "F2", "start_tick": 3840, "duration_ticks": 1920, "velocity": 60},
                {"pitch": "B2", "start_tick": 5760, "duration_ticks": 1920, "velocity": 60},
            ],
        }
    )
    result = validate_composition_integrity(payload, complexity="simple")
    assert result.ok
    assert "event_out_of_range" not in result.error_codes()


def test_validator_bass_density_softer_than_melody_for_complex():
    payload = _base_composition()
    # 4 bars, complex melody/harmony need 6 events; bass only needs ~2 (0.5/bar).
    payload["tracks"][0]["events"] = [
        {"pitch": "A4", "start_tick": i * 480, "duration_ticks": 480, "velocity": 80} for i in range(6)
    ]
    payload["tracks"][1]["events"] = [
        {"pitch": "A2", "start_tick": 0, "duration_ticks": 3840, "velocity": 84},
        {"pitch": "E2", "start_tick": 3840, "duration_ticks": 3840, "velocity": 84},
    ]
    payload["tracks"][2]["events"] = [
        {"pitch": "A3", "start_tick": i * 480, "duration_ticks": 480, "velocity": 70, "staff": "bass"}
        for i in range(6)
    ]
    result = validate_composition_integrity(payload, complexity="complex")
    assert result.ok
    assert "empty_required_track" not in result.error_codes()


def test_validator_rejects_event_outside_composition():
    payload = _base_composition()
    payload["tracks"][0]["events"][0]["start_tick"] = 7500
    payload["tracks"][0]["events"][0]["duration_ticks"] = 480
    result = validate_composition_integrity(payload, complexity="simple")
    assert not result.ok
    assert "event_out_of_range" in result.error_codes() or "schema_invalid" in result.error_codes()


def test_validator_rejects_harmony_only_substitution():
    payload = _base_composition()
    for track in payload["tracks"]:
        track["events"] = []
    result = validate_composition_integrity(payload, complexity="simple")
    assert not result.ok
    assert "harmony_only" in result.error_codes() or "empty_required_track" in result.error_codes()


def test_validator_warns_missing_requested_strings():
    result = validate_composition_integrity(
        _base_composition(),
        requested_instruments=["piano", "bass", "strings"],
        complexity="simple",
    )
    assert result.ok
    assert any(item.code == "missing_requested_instrument" for item in result.warnings)


def test_validator_warns_drum_channel_semantics():
    payload = _base_composition()
    payload["tracks"].append(
        {
            "id": "drums-1",
            "name": "Drums",
            "instrument": "drums",
            "role": "drums",
            "midi_program": 0,
            "channel": 1,
            "is_drum": True,
            "events": [],
        }
    )
    result = validate_composition_integrity(payload, complexity="simple")
    assert result.ok
    assert any(item.code == "track_range_semantics" for item in result.warnings)


def test_validator_detects_bar_overflow_density():
    payload = _base_composition()
    # Pack many overlapping full-bar events into bar 1 to trip density overflow.
    payload["tracks"][0]["events"] = [
        {"pitch": "A4", "start_tick": 0, "duration_ticks": 1920, "velocity": 80}
        for _ in range(8)
    ] + [
        {"pitch": "C5", "start_tick": 1920, "duration_ticks": 480, "velocity": 80},
        {"pitch": "E5", "start_tick": 3840, "duration_ticks": 480, "velocity": 80},
        {"pitch": "A4", "start_tick": 5760, "duration_ticks": 480, "velocity": 80},
    ]
    result = validate_composition_integrity(payload, complexity="simple")
    assert not result.ok
    assert "bar_overflow" in result.error_codes()
