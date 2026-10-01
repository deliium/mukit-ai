"""Bind checks for musical-universe references."""

from __future__ import annotations

import logging

import pytest

from app.composition_schemas import CompositionV2
from app.musical_universe_schemas import parse_musical_universe
from app.services.composition_snapshot_encoding import composition_snapshot_fingerprint
from app.services.musical_universe_bind import bind_universe_references

_FINGERPRINT_SLOT = "source_fingerprint"


def vector_a_score() -> CompositionV2:
    """Project A: C4 D4 E4 F4 original motif at 4/4, 120, 480 ticks per quarter."""
    notes = []
    pitches = ("C4", "D4", "E4", "F4")
    for index, pitch in enumerate(pitches):
        notes.append(
            {
                "id": f"a{index + 1}",
                "type": "note",
                "pitch": pitch,
                "start_tick": index * 480,
                "duration_ticks": 480,
                "velocity": 80,
            }
        )
    return CompositionV2.model_validate(
        {
            "schema_version": "composition.v2",
            "tempo": 120,
            "key": "C major",
            "time_signature": "4/4",
            "ticks_per_quarter": 480,
            "duration_ticks": 7680,
            "bar_count": 4,
            "sections": [
                {
                    "id": "section_verse",
                    "type": "verse",
                    "start_bar": 1,
                    "bar_count": 4,
                    "start_tick": 0,
                    "duration_ticks": 7680,
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
                    "events": notes,
                }
            ],
            "harmony": [{"start_tick": 0, "duration_ticks": 7680, "chord": "C"}],
            "motifs": [
                {
                    "id": "motif_theme_a",
                    "label": "Theme A",
                    "occurrences": [
                        {
                            "id": "occ_original",
                            "track_id": "track_melody",
                            "event_ids": ["a1", "a2", "a3", "a4"],
                            "relationship": "original",
                        }
                    ],
                }
            ],
        }
    )


def vector_b_score() -> CompositionV2:
    """Project B: one G4 at tick 0. No motifs."""
    return CompositionV2.model_validate(
        {
            "schema_version": "composition.v2",
            "tempo": 120,
            "key": "C major",
            "time_signature": "4/4",
            "ticks_per_quarter": 480,
            "duration_ticks": 7680,
            "bar_count": 4,
            "sections": [
                {
                    "id": "section_verse",
                    "type": "verse",
                    "start_bar": 1,
                    "bar_count": 4,
                    "start_tick": 0,
                    "duration_ticks": 7680,
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
                            "id": "b0",
                            "type": "note",
                            "pitch": "G4",
                            "start_tick": 0,
                            "duration_ticks": 480,
                            "velocity": 80,
                        }
                    ],
                }
            ],
        }
    )


def theme_document(score: CompositionV2, *, fingerprint: str | None = None) -> dict:
    live = fingerprint if fingerprint is not None else composition_snapshot_fingerprint(score)
    return {
        "schema_version": "musical.universe.v1",
        "id": "muniv_" + "ab" * 8,
        "name": "Franchise",
        "entities": [
            {
                "id": "ent_aaaa1111",
                "kind": "character",
                "label": "Ada",
                "harmony_refs": [{"project_id": "project-a", "start_tick": 0}],
                "track_refs": [{"project_id": "project-a", "track_id": "track_melody"}],
                "catalog_instrument_ids": ["violin"],
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
                "source_fingerprint": live,
            }
        ],
    }


def test_matching_original_occurrence_has_no_errors(caplog: pytest.LogCaptureFixture) -> None:
    score = vector_a_score()
    universe = parse_musical_universe(theme_document(score))
    with caplog.at_level(logging.DEBUG, logger="app.services.musical_universe_bind"):
        findings = bind_universe_references(
            universe,
            ["project-a"],
            compositions={"project-a": score},
        )
    assert findings == []
    finished = [
        record
        for record in caplog.records
        if record.getMessage() == "Musical universe bind finished"
    ]
    assert finished[-1].error_count == 0
    assert finished[-1].warning_count == 0
    assert "C4" not in caplog.text
    assert "Ada" not in caplog.text


def test_missing_harmony_and_unknown_instrument() -> None:
    score = vector_a_score()
    payload = theme_document(score)
    payload["entities"][0]["harmony_refs"] = [{"project_id": "project-a", "start_tick": 999}]
    payload["entities"][0]["catalog_instrument_ids"] = ["not_a_catalog_instrument"]
    universe = parse_musical_universe(payload)
    findings = bind_universe_references(
        universe,
        ["project-a"],
        compositions={"project-a": score},
    )
    codes = {item.code for item in findings}
    assert "universe_harmony_missing" in codes
    assert "universe_instrument_unknown" in codes
    assert all(item.severity == "error" for item in findings)


def test_fingerprint_drift_is_warning_only() -> None:
    score = vector_a_score()
    drifted = "0" * 64
    assert drifted != composition_snapshot_fingerprint(score)
    universe = parse_musical_universe(theme_document(score, fingerprint=drifted))
    findings = bind_universe_references(
        universe,
        ["project-a"],
        compositions={"project-a": score},
    )
    assert len(findings) == 1
    assert findings[0].code == "universe_source_fingerprint_drift"
    assert findings[0].severity == "warning"
    assert _FINGERPRINT_SLOT not in findings[0].message
