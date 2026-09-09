"""Acceptance-style persistence regression: create → edit → reopen equals saved composition."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from app.db import initialize_database, reset_database_initialization_cache
from app.services import project_store as store
from app.services.project_composition import normalize_project_composition
from tests.test_composition_schema import valid_composition
from tests.test_composition_v2_schema import _motif_definition, _motif_track, minimal_v2


@pytest.fixture
def project_db(tmp_path, monkeypatch):
    db_path = tmp_path / "acceptance.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    reset_database_initialization_cache()
    initialize_database()
    return db_path


def _edited_composition():
    data = valid_composition()
    # Simulate several piano-roll note edits
    events = data["tracks"][0]["events"]
    events[0]["pitch"] = "D4"
    events[0]["start_tick"] = 480
    events[0]["velocity"] = 96
    events.append(
        {
            "type": "note",
            "pitch": "G4",
            "start_tick": 960,
            "duration_ticks": 480,
            "velocity": 88,
        }
    )
    events.append(
        {
            "type": "note",
            "pitch": "A4",
            "start_tick": 1440,
            "duration_ticks": 240,
            "velocity": 70,
        }
    )
    return data


def test_create_save_reopen_preserves_edited_composition(project_db, caplog):
    created = store.create_project("Acceptance Song", db_path=project_db)
    edited = _edited_composition()
    normalized = normalize_project_composition(edited, project_id=created.id)
    canonical = normalized.composition.model_dump(mode="json")

    store.update_project(
        created.id,
        composition=json.dumps(canonical, separators=(",", ":")),
        generation_provider="deepseek",
        generation_model="deepseek-chat",
        generation_prompt={"genre": "ambient", "mood": "calm"},
        db_path=project_db,
    )

    # New connection/session: clear init cache and reopen from path
    reset_database_initialization_cache()
    with caplog.at_level("INFO"):
        reopened = store.get_project(created.id, db_path=project_db)

    assert reopened.name == "Acceptance Song"
    loaded = json.loads(reopened.composition_json)
    assert loaded == canonical
    assert loaded["schema_version"] == "composition.v2"
    assert loaded["tracks"][0]["events"][0]["pitch"] == "D4"
    assert len(loaded["tracks"][0]["events"]) == 4
    assert reopened.generation_provider == "deepseek"
    assert reopened.generation_model == "deepseek-chat"
    assert "api_key" not in (reopened.generation_prompt_json or "")

    summary = reopened.composition_summary()
    assert summary["track_count"] == 1
    assert summary["event_count"] == 4
    assert summary["bar_count"] == 2


def test_persistence_round_trips_multi_role_same_instrument_tracks(project_db):
    data = valid_composition()
    data["tracks"] = [
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
        {
            "id": "bass-1",
            "name": "Bass",
            "instrument": "bass",
            "role": "bass",
            "midi_program": 32,
            "channel": 3,
            "events": [
                {"type": "note", "pitch": "C2", "start_tick": 0, "duration_ticks": 1920, "velocity": 84},
            ],
        },
    ]
    created = store.create_project("Multi Role", db_path=project_db)
    normalized = normalize_project_composition(data, project_id=created.id)
    canonical = normalized.composition.model_dump(mode="json")
    store.update_project(
        created.id,
        composition=json.dumps(canonical, separators=(",", ":")),
        db_path=project_db,
    )
    reset_database_initialization_cache()
    reopened = store.get_project(created.id, db_path=project_db)
    loaded = json.loads(reopened.composition_json)
    assert [(t["id"], t["instrument"], t["role"]) for t in loaded["tracks"]] == [
        ("melody-1", "piano", "melody"),
        ("harmony-1", "piano", "harmony"),
        ("bass-1", "bass", "bass"),
    ]
    assert len(loaded["tracks"]) == 3


def test_create_save_reopen_preserves_motif_metadata(project_db):
    created = store.create_project("Motif Song", db_path=project_db)
    payload = minimal_v2(tracks=[_motif_track()], motifs=[_motif_definition()])
    normalized = normalize_project_composition(payload, project_id=created.id)
    canonical = normalized.composition.model_dump(mode="json")

    store.update_project(
        created.id,
        composition=json.dumps(canonical, separators=(",", ":")),
        db_path=project_db,
    )

    reset_database_initialization_cache()
    reopened = store.get_project(created.id, db_path=project_db)
    loaded = json.loads(reopened.composition_json)

    assert loaded == canonical
    assert loaded["motifs"][0]["id"] == "motif-a"
    assert loaded["motifs"][0]["occurrences"][0]["event_ids"] == ["n1", "n2", "n3"]
    assert "events" not in loaded["motifs"][0]["occurrences"][0]


def test_duplicate_project_preserves_motif_metadata(project_db):
    created = store.create_project("Motif Duplicate", db_path=project_db)
    payload = minimal_v2(tracks=[_motif_track()], motifs=[_motif_definition()])
    normalized = normalize_project_composition(payload, project_id=created.id)
    canonical = normalized.composition.model_dump(mode="json")
    store.update_project(
        created.id,
        composition=json.dumps(canonical, separators=(",", ":")),
        db_path=project_db,
    )

    duplicated = store.duplicate_project(created.id, db_path=project_db)
    loaded = json.loads(duplicated.composition_json)
    assert loaded["motifs"] == canonical["motifs"]
    assert loaded["tracks"][0]["events"] == canonical["tracks"][0]["events"]
