import pytest

from app.schemas import Composition


def valid_composition(**overrides):
    data = {
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
                "id": "piano-1",
                "name": "Piano",
                "instrument": "piano",
                "role": "harmony",
                "midi_program": 0,
                "channel": 1,
                "events": [
                    {"type": "note", "pitch": "C4", "start_tick": 0, "duration_ticks": 480, "velocity": 80},
                    {"type": "note", "pitch": "E4", "start_tick": 0, "duration_ticks": 480, "velocity": 80},
                ],
            }
        ],
        "harmony": [{"bar": 1, "chord": "C"}],
    }
    data.update(overrides)
    return data


def test_composition_schema_accepts_polyphony_and_logs_summary(caplog):
    with caplog.at_level("INFO"):
        composition = Composition.model_validate(valid_composition())

    assert composition.schema_version == "composition.v1"
    assert len(composition.tracks[0].events) == 2
    assert "Composition validation completed" in caplog.text


@pytest.mark.parametrize(
    "event_update",
    [
        {"pitch": "C10"},
        {"velocity": 0},
        {"duration_ticks": 0},
        {"start_tick": 3840, "duration_ticks": 1},
    ],
)
def test_composition_schema_rejects_invalid_events(event_update):
    data = valid_composition()
    data["tracks"][0]["events"][0].update(event_update)

    with pytest.raises(ValueError):
        Composition.model_validate(data)


def test_composition_schema_rejects_duplicate_track_ids():
    data = valid_composition()
    data["tracks"].append({**data["tracks"][0], "name": "Duplicate"})

    with pytest.raises(ValueError):
        Composition.model_validate(data)


def test_composition_schema_rejects_non_contiguous_sections():
    data = valid_composition(
        sections=[
            {"type": "intro", "start_bar": 1, "bar_count": 1, "start_tick": 0, "duration_ticks": 1920},
            {"type": "verse", "start_bar": 3, "bar_count": 1, "start_tick": 1920, "duration_ticks": 1920},
        ]
    )

    with pytest.raises(ValueError):
        Composition.model_validate(data)


def test_composition_schema_validates_3_4_and_6_8_timing():
    assert Composition.model_validate(
        valid_composition(
            time_signature="3/4",
            bar_count=1,
            duration_ticks=1440,
            sections=[{"type": "intro", "start_bar": 1, "bar_count": 1, "start_tick": 0, "duration_ticks": 1440}],
        )
    ).duration_ticks == 1440
    assert Composition.model_validate(
        valid_composition(
            time_signature="6/8",
            bar_count=1,
            duration_ticks=1440,
            sections=[{"type": "intro", "start_bar": 1, "bar_count": 1, "start_tick": 0, "duration_ticks": 1440}],
        )
    ).duration_ticks == 1440
