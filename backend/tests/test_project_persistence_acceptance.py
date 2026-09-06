"""Acceptance-style persistence regression: create → edit → reopen equals saved composition."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from app.db import initialize_database, reset_database_initialization_cache
from app.services import project_store as store
from app.services.project_composition import normalize_project_composition
from tests.test_composition_schema import valid_composition


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
    assert loaded["tracks"][0]["events"][0]["pitch"] == "D4"
    assert len(loaded["tracks"][0]["events"]) == 4
    assert reopened.generation_provider == "deepseek"
    assert reopened.generation_model == "deepseek-chat"
    assert "api_key" not in (reopened.generation_prompt_json or "")

    summary = reopened.composition_summary()
    assert summary["track_count"] == 1
    assert summary["event_count"] == 4
    assert summary["bar_count"] == 2
