"""Deterministic import fidelity: fixtures, re-import identity, export round-trip, persistence."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from app.import_schemas import CompositionImportError
from app.services.composition_midi import render_midi
from app.services.composition_midi_import import import_midi_bytes
from app.services.composition_musicxml_import import import_musicxml_bytes
from app.services.composition_timeline import compile_timeline
from app.services.music_json_renderer import render_musicxml
from app.services import project_store as store
from tests.fixtures.build_import_fixtures import (
    SENTINEL_FILENAME,
    assert_fixture_parity,
)

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "import"
VECTORS = json.loads((FIXTURE_DIR / "expected_vectors.json").read_text(encoding="utf-8"))


def _load(name: str) -> bytes:
    return (FIXTURE_DIR / name).read_bytes()


def _note_rows(composition) -> list[dict]:
    rows = []
    for track in composition.tracks:
        for event in track.events:
            rows.append(
                {
                    "track_id": track.id,
                    "pitch": event.pitch,
                    "start_tick": event.start_tick,
                    "duration_ticks": event.duration_ticks,
                    "velocity": event.velocity,
                    "event_id": event.id,
                    "articulations": list(event.articulations or []),
                    "tie": event.tie.model_dump(mode="json") if event.tie else None,
                }
            )
    return sorted(
        rows,
        key=lambda row: (row["track_id"], row["start_tick"], row["pitch"], row["duration_ticks"]),
    )


def _issue_codes(report) -> list[str]:
    return sorted({issue.code for issue in report.issues})


def test_committed_import_fixtures_match_builder():
    assert_fixture_parity()


@pytest.mark.parametrize(
    ("filename", "importer"),
    [
        ("multitrack.mid", import_midi_bytes),
        ("multipart.musicxml", import_musicxml_bytes),
        ("multipart.mxl", import_musicxml_bytes),
    ],
)
def test_valid_fixture_import_is_idempotent_and_matches_vectors(filename, importer):
    data = _load(filename)
    first = importer(data, display_filename=filename)
    second = importer(data, display_filename=filename)
    assert first.composition.model_dump(mode="json") == second.composition.model_dump(mode="json")
    assert _issue_codes(first.import_report) == _issue_codes(second.import_report)
    assert [event.id for track in first.composition.tracks for event in track.events] == [
        event.id for track in second.composition.tracks for event in track.events
    ]

    expected = VECTORS[filename]
    assert _issue_codes(first.import_report) == expected["issue_codes"]
    assert first.composition.schema_version == expected["meta"]["schema_version"]
    assert first.composition.tempo == expected["meta"]["tempo"]
    assert first.composition.time_signature == expected["meta"]["time_signature"]
    assert first.composition.key == expected["meta"]["key"]
    assert first.composition.bar_count == expected["meta"]["bar_count"]
    assert first.composition.duration_ticks == expected["meta"]["duration_ticks"]
    assert first.composition.harmony == []
    assert [section.type for section in first.composition.sections] == expected["meta"]["section_types"]
    assert _note_rows(first.composition) == expected["notes"]
    compile_timeline(first.composition)


def test_midi_fixture_reexport_preserves_playable_note_semantics():
    imported = import_midi_bytes(_load("multitrack.mid"), display_filename="multitrack.mid")
    midi_bytes = render_midi(imported.composition)
    reimported = import_midi_bytes(midi_bytes, display_filename="roundtrip.mid")

    def playable(composition):
        return sorted(
            (
                track.channel,
                track.midi_program,
                event.pitch,
                event.start_tick,
                event.duration_ticks,
                event.velocity,
            )
            for track in composition.tracks
            for event in track.events
        )

    # Program/channel/pitch/timing/velocity must survive MIDI export→import within V2 rules.
    assert playable(reimported.composition) == playable(imported.composition)


def test_musicxml_fixture_reexport_preserves_pitches_and_timing():
    imported = import_musicxml_bytes(
        _load("multipart.musicxml"),
        display_filename="multipart.musicxml",
    )
    musicxml, _report = render_musicxml(imported.composition)
    # Regenerated MusicXML may include a local DOCTYPE; strip external-safe for re-import.
    safe_xml = musicxml
    if "<!DOCTYPE" in safe_xml:
        import re

        safe_xml = re.sub(r"<!DOCTYPE[^>]*(?:\[[\s\S]*?\]\s*)?>", "", safe_xml, count=1)
    reimported = import_musicxml_bytes(safe_xml.encode("utf-8"), display_filename="roundtrip.musicxml")

    def pitch_timing(composition):
        return sorted(
            (event.pitch, event.start_tick, event.duration_ticks)
            for track in composition.tracks
            for event in track.events
        )

    assert pitch_timing(reimported.composition) == pitch_timing(imported.composition)


def test_imported_v2_persists_and_reopens_with_equal_event_ids(tmp_path, monkeypatch):
    from app.db.connection import initialize_database, reset_database_initialization_cache

    db_path = tmp_path / "import_fidelity.sqlite3"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    reset_database_initialization_cache()
    initialize_database()
    imported = import_midi_bytes(_load("multitrack.mid"), display_filename="multitrack.mid")
    created = store.create_project(
        "Imported Fidelity",
        composition=imported.composition.model_dump(mode="json"),
        db_path=db_path,
    )
    opened = store.get_project(created.id, db_path=db_path)
    assert opened is not None
    assert opened.composition_json is not None
    payload = json.loads(opened.composition_json)
    assert payload["schema_version"] == "composition.v2"
    original_ids = [event.id for track in imported.composition.tracks for event in track.events]
    reopened_ids = [event["id"] for track in payload["tracks"] for event in track["events"]]
    assert reopened_ids == original_ids
    assert opened.generation_provider is None
    assert opened.generation_model is None
    assert opened.generation_prompt_json is None


def test_import_logs_do_not_include_fixture_sentinels(caplog):
    data = _load(SENTINEL_FILENAME)
    with caplog.at_level(logging.DEBUG):
        import_midi_bytes(data, display_filename=SENTINEL_FILENAME)
        with pytest.raises(CompositionImportError):
            import_musicxml_bytes(_load("entities.musicxml"), display_filename="entities.musicxml")
    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert SENTINEL_FILENAME not in joined
    assert VECTORS["sentinels"]["lyric"] not in joined
    assert VECTORS["sentinels"]["title"] not in joined
    assert "MThd" not in joined


def test_musicxml_tie_chain_reuses_shared_group_id():
    imported = import_musicxml_bytes(
        _load("multipart.musicxml"),
        display_filename="multipart.musicxml",
    )
    tied = [
        event
        for track in imported.composition.tracks
        for event in track.events
        if event.tie is not None
    ]
    assert len(tied) >= 2
    group_ids = {event.tie.group_id for event in tied}
    assert len(group_ids) == 1
    types = {event.tie.type for event in tied}
    assert types == {"start", "stop"}
