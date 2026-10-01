"""Pure command tests for musical universe entities, themes, and variants."""

from __future__ import annotations

import logging

import pytest

from app.musical_universe_schemas import MusicalUniverseError, MusicalUniverseV1, parse_musical_universe
from app.services.musical_universe_commands import apply_command_payload, apply_musical_universe_command
from app.musical_universe_schemas import parse_musical_universe_command

_FINGERPRINT = "cd" * 32


def _empty() -> MusicalUniverseV1:
    return parse_musical_universe(
        {
            "schema_version": "musical.universe.v1",
            "id": "muniv_" + "a1" * 8,
            "name": "Franchise",
            "entities": [],
            "themes": [],
        }
    )


def test_create_character_and_theme_a(caplog: pytest.LogCaptureFixture) -> None:
    universe = _empty()
    with caplog.at_level(logging.DEBUG, logger="app.services.musical_universe_commands"):
        with_entity = apply_command_payload(
            universe,
            {"op": "create_entity", "kind": "character", "label": "Ada"},
        )
    assert len(with_entity.entities) == 1
    assert with_entity.entities[0].kind == "character"
    assert universe.entities == []
    assert "Ada" not in caplog.text
    applied = [
        record
        for record in caplog.records
        if record.getMessage() == "Musical universe command applied"
    ]
    assert applied[-1].command == "create_entity"
    assert applied[-1].entity_count == 1

    themed = apply_command_payload(
        with_entity,
        {
            "op": "create_theme",
            "entity_id": with_entity.entities[0].id,
            "label": "Theme A",
            "source": {
                "project_id": "project-a",
                "motif_id": "motif_theme_a",
                "occurrence_id": "occ_original",
            },
            "source_fingerprint": _FINGERPRINT,
            "source_checked": True,
        },
    )
    assert themed.themes[0].label == "Theme A"
    assert themed.themes[0].source.occurrence_id == "occ_original"
    assert "events" not in themed.model_dump(mode="json")


def test_rejects_source_that_is_not_a_motif_ref() -> None:
    universe = apply_command_payload(
        _empty(),
        {"op": "create_entity", "kind": "character", "label": "Ada"},
    )
    with pytest.raises(MusicalUniverseError) as captured:
        apply_command_payload(
            universe,
            {
                "op": "create_theme",
                "entity_id": universe.entities[0].id,
                "label": "Theme A",
                "source": {
                    "project_id": "project-a",
                    "motif_id": "motif_theme_a",
                    "occurrence_id": "occ_original",
                    "event_ids": ["a1", "a2", "a3", "a4"],
                },
                "source_fingerprint": _FINGERPRINT,
                "source_checked": True,
            },
        )
    assert captured.value.code == "musical_universe_invalid"
    assert universe.themes == []


def test_rejects_33rd_variant_and_delete_while_theme_remains() -> None:
    universe = apply_command_payload(
        _empty(),
        {"op": "create_entity", "kind": "character", "label": "Ada"},
    )
    themed = apply_command_payload(
        universe,
        {
            "op": "create_theme",
            "entity_id": universe.entities[0].id,
            "label": "Theme A",
            "source": {
                "project_id": "project-a",
                "motif_id": "motif_theme_a",
                "occurrence_id": "occ_original",
            },
            "source_fingerprint": _FINGERPRINT,
            "source_checked": True,
        },
    )
    theme_id = themed.themes[0].id
    current = themed
    for index in range(32):
        current = apply_musical_universe_command(
            current,
            parse_musical_universe_command(
                {
                    "op": "add_variant",
                    "theme_id": theme_id,
                    "label": f"Variant {index}",
                    "operation": "repeat",
                    "parameters": {},
                }
            ),
        )
    assert len(current.themes[0].variants) == 32
    with pytest.raises(MusicalUniverseError) as too_many:
        apply_command_payload(
            current,
            {
                "op": "add_variant",
                "theme_id": theme_id,
                "label": "One more",
                "operation": "repeat",
                "parameters": {},
            },
        )
    assert too_many.value.code == "musical_universe_invalid"
    assert len(current.themes[0].variants) == 32

    with pytest.raises(MusicalUniverseError) as in_use:
        apply_command_payload(
            current,
            {"op": "delete_entity", "entity_id": current.entities[0].id},
        )
    assert in_use.value.code == "universe_entity_in_use"
    assert current.entities[0].kind == "character"


def test_notes_key_is_rejected_before_a_document_is_returned() -> None:
    universe = _empty()
    with pytest.raises(MusicalUniverseError) as captured:
        apply_command_payload(universe, {"op": "create_entity", "kind": "character", "label": "Ada", "notes": []})
    assert captured.value.code == "embedded_note_material"
    assert universe.entities == []
