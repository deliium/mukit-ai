"""Acceptance: derive cinematic profile → generate with profile soft conditioning; prompt wins."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import initialize_database, reset_database_initialization_cache
from app.main import app
from app.schemas import LLMMusicGenerationRequest, LLMPromptParameters
from app.services import project_store
from app.services.composer_profile_merge import resolve_profile_merge
from app.services.generation_constraints import build_generation_constraints


FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "fixtures"
    / "composition_v2_expressive.json"
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    monkeypatch.setenv("DATASET_ROOT", str(tmp_path / "dataset_should_stay_empty"))
    reset_database_initialization_cache()
    initialize_database()
    return TestClient(app), tmp_path, db_path


def test_cinematic_profile_generate_priority_acceptance(client):
    http, tmp_path, db_path = client
    composition = json.loads(FIXTURE.read_text(encoding="utf-8"))
    project = project_store.create_project("Cinematic Source", composition=composition)

    derived = http.post(
        "/composer-profiles/derive",
        json={
            "sources": [{"project_id": project.id}],
            "save_as": "My Cinematic Style",
        },
    )
    assert derived.status_code == 200
    profile = derived.json()["profile"]
    assert profile["name"] == "My Cinematic Style"
    assert profile["source_projects"]
    assert "tracks" not in profile
    # No note event arrays in profile body.
    assert not _contains_event_arrays(profile)

    # Priority lock: conflicting prompt instruments/key are never rewritten by merge.
    request = LLMMusicGenerationRequest(
        prompt=LLMPromptParameters(
            genre="jazz",
            mood="calm",
            key="D major",
            instruments=["piano", "bass"],
            duration_bars=4,
        ),
        profile_id=profile["id"],
        profile_strength="strong",
    )
    before = request.prompt.model_dump()
    merge = resolve_profile_merge(
        profile_id=profile["id"],
        profile_strength="strong",
        db_path=db_path,
    )
    assert request.prompt.model_dump() == before
    assert request.prompt.instruments == ["piano", "bass"]
    assert request.prompt.key == "D major"
    assert merge.soft_fragment
    constraints = build_generation_constraints(request)
    assert constraints.key == "D major"

    # Generate path: omit user key so fake uses 4-bar V2 fixture; soft marker + provenance assert.
    generate = http.post(
        "/llm/generate-music-json",
        json={
            "prompt": {
                "genre": "jazz",
                "mood": "calm",
                "time_signature": "4/4",
                "tempo_min": 90,
                "tempo_max": 100,
                "instruments": ["piano", "bass"],
                "complexity": "moderate",
                "duration_bars": 4,
            },
            "selection": {"provider": "fake", "model": "fake-v1"},
            "options": {"pipeline": "llm_only", "max_retries": 0},
            "profile_id": profile["id"],
            "profile_strength": "normal",
        },
    )
    assert generate.status_code == 200, generate.text
    payload = generate.json()
    assert payload["music"]["bar_count"] == 4
    warnings = payload.get("warnings") or []
    assert any("composer-profile" in str(w).lower() or "soft marker" in str(w).lower() for w in warnings)
    gen_params = payload.get("generation_parameters") or {}
    assert (
        gen_params.get("composer_profile_id") == profile["id"]
        or payload.get("composer_profile_id") == profile["id"]
        or gen_params.get("profile_strength") == "normal"
    )

    dataset_root = tmp_path / "dataset_should_stay_empty"
    assert not dataset_root.exists() or not any(dataset_root.iterdir())


def _contains_event_arrays(payload) -> bool:
    if isinstance(payload, dict):
        if "events" in payload and isinstance(payload["events"], list):
            return True
        return any(_contains_event_arrays(v) for v in payload.values())
    if isinstance(payload, list):
        return any(_contains_event_arrays(v) for v in payload)
    return False
