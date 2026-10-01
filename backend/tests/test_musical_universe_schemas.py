"""Schema tests for musical.universe.v1 and motif pins."""

from __future__ import annotations

import json
import logging

import pytest

from app.composition_schemas import CompositionV2, CompositionV2MotifDefinition
from app.musical_universe_schemas import (
    FORBIDDEN_NOTE_KEYS,
    MusicalUniverseError,
    parse_musical_universe,
)

_FINGERPRINT = "ab" * 32
_UNIVERSE_ID = "muniv_" + "a1" * 8


def _character_theme() -> dict:
    return {
        "schema_version": "musical.universe.v1",
        "id": _UNIVERSE_ID,
        "name": "  Franchise  ",
        "entities": [
            {
                "id": "ent_aaaa1111",
                "kind": "character",
                "label": "Ada",
            }
        ],
        "themes": [
            {
                "id": "theme_bbbb2222",
                "entity_id": "ent_aaaa1111",
                "label": "Theme A",
                "source": {
                    "project_id": "project-a",
                    "motif_id": "motif_theme_a",
                    "occurrence_id": "occ_original",
                },
                "source_fingerprint": _FINGERPRINT,
            }
        ],
    }


def _score_without_pins() -> dict:
    return {
        "schema_version": "composition.v2",
        "tempo": 120,
        "key": "C major",
        "time_signature": "4/4",
        "ticks_per_quarter": 480,
        "duration_ticks": 1920,
        "bar_count": 1,
        "sections": [
            {
                "type": "verse",
                "start_bar": 1,
                "bar_count": 1,
                "start_tick": 0,
                "duration_ticks": 1920,
            }
        ],
        "tracks": [
            {
                "id": "track_melody",
                "name": "Melody",
                "instrument": "violin",
                "role": "melody",
                "midi_program": 40,
                "channel": 1,
                "events": [
                    {
                        "id": "a1",
                        "type": "note",
                        "pitch": "C4",
                        "start_tick": 0,
                        "duration_ticks": 480,
                        "velocity": 80,
                    },
                    {
                        "id": "a2",
                        "type": "note",
                        "pitch": "D4",
                        "start_tick": 480,
                        "duration_ticks": 480,
                        "velocity": 80,
                    },
                    {
                        "id": "a3",
                        "type": "note",
                        "pitch": "E4",
                        "start_tick": 960,
                        "duration_ticks": 480,
                        "velocity": 80,
                    },
                ],
            }
        ],
        "motifs": [
            {
                "id": "motif_theme_a",
                "label": "Theme A",
                "occurrences": [
                    {
                        "id": "occ_original",
                        "track_id": "track_melody",
                        "event_ids": ["a1", "a2", "a3"],
                        "relationship": "original",
                    }
                ],
            }
        ],
    }


def test_character_theme_a_has_no_note_keys_or_committed() -> None:
    universe = parse_musical_universe(_character_theme())
    assert universe.name == "Franchise"
    assert universe.themes[0].label == "Theme A"
    dumped = universe.model_dump(mode="json")
    assert "committed" not in dumped
    encoded = json.dumps(dumped)
    for key in FORBIDDEN_NOTE_KEYS:
        assert f'"{key}"' not in encoded


def test_rejects_tracks_and_events(caplog: pytest.LogCaptureFixture) -> None:
    with_tracks = _character_theme()
    with_tracks["tracks"] = []
    with caplog.at_level(logging.DEBUG, logger="app.musical_universe_schemas"):
        with pytest.raises(MusicalUniverseError) as tracks:
            parse_musical_universe(with_tracks)
    assert tracks.value.code == "musical_universe_invalid"
    assert tracks.value.http_status == 422
    assert any(
        record.model == "MusicalUniverseV1" and record.code == "musical_universe_invalid"
        for record in caplog.records
        if record.name == "app.musical_universe_schemas"
    )

    with_events = _character_theme()
    with_events["events"] = [{"pitch": "C4"}]
    with pytest.raises(MusicalUniverseError) as events:
        parse_musical_universe(with_events)
    assert events.value.code == "embedded_note_material"
    mapped_body = json.dumps({"code": events.value.code, "message": events.value.message})
    assert "C4" not in mapped_body
    assert "Ada" not in mapped_body


def test_rejects_65th_entity_and_incomplete_relationship() -> None:
    payload = _character_theme()
    payload["entities"] = [
        {"id": f"ent_{index:08x}", "kind": "character", "label": f"E{index}"}
        for index in range(65)
    ]
    payload["themes"] = []
    with pytest.raises(MusicalUniverseError) as too_many:
        parse_musical_universe(payload)
    assert too_many.value.code == "musical_universe_invalid"

    broken = _character_theme()
    broken["entities"].append(
        {
            "id": "ent_cccc3333",
            "kind": "relationship",
            "label": "Bond",
            "subject_entity_id": "ent_aaaa1111",
        }
    )
    with pytest.raises(MusicalUniverseError) as relationship:
        parse_musical_universe(broken)
    assert relationship.value.code == "musical_universe_invalid"


def test_rejects_event_ids_on_theme_source_and_bad_universe_id() -> None:
    payload = _character_theme()
    payload["themes"][0]["source"]["event_ids"] = ["a1", "a2", "a3"]
    with pytest.raises(MusicalUniverseError) as source:
        parse_musical_universe(payload)
    assert source.value.code == "musical_universe_invalid"

    bad_id = _character_theme()
    bad_id["id"] = "universe-1"
    with pytest.raises(MusicalUniverseError) as identifier:
        parse_musical_universe(bad_id)
    assert identifier.value.code == "musical_universe_invalid"


def test_rejects_transpose_parameters_that_also_set_sequence_steps() -> None:
    payload = _character_theme()
    payload["themes"][0]["variants"] = [
        {
            "id": "var_dddd4444",
            "label": "Up a tone",
            "operation": "transpose",
            "parameters": {"transpose_semitones": 2, "sequence_steps": 1},
            "source_project_id": "project-a",
            "source_motif_id": "motif_theme_a",
            "source_occurrence_id": "occ_original",
        }
    ]
    with pytest.raises(MusicalUniverseError) as captured:
        parse_musical_universe(payload)
    assert captured.value.code == "universe_operation_parameters"
    assert captured.value.http_status == 422


def test_partial_motif_pin_is_rejected_and_omitted_pins_validate() -> None:
    with pytest.raises(ValueError, match="all be set or all be omitted"):
        CompositionV2MotifDefinition.model_validate(
            {
                "id": "motif_theme_a",
                "label": "Theme A",
                "musical_universe_id": _UNIVERSE_ID,
                "occurrences": [
                    {
                        "id": "occ_original",
                        "track_id": "track_melody",
                        "event_ids": ["a1", "a2", "a3"],
                        "relationship": "original",
                    }
                ],
            }
        )

    composition = CompositionV2.model_validate(_score_without_pins())
    assert composition.motifs[0].musical_universe_id is None
    assert composition.motifs[0].theme_id is None
    assert composition.motifs[0].variant_id is None


def test_committed_is_not_a_universe_field() -> None:
    payload = _character_theme()
    payload["committed"] = True
    with pytest.raises(MusicalUniverseError) as captured:
        parse_musical_universe(payload)
    assert captured.value.code == "musical_universe_invalid"
