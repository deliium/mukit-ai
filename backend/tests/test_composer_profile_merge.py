"""Tests for additive composer-profile generate merge (prompt/hard win)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.composer_profile_schemas import PreferenceFields
from app.db import initialize_database, reset_database_initialization_cache
from app.schemas import LLMMusicGenerationRequest, LLMPromptParameters
from app.services.composer_profile_merge import (
    append_profile_fragment,
    merge_provenance_keys,
    resolve_profile_merge,
)
from app.services import composer_profile_store as store
from app.services.generation_constraints import build_generation_constraints


@pytest.fixture
def project_db(tmp_path, monkeypatch):
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    reset_database_initialization_cache()
    initialize_database()
    return db_path


def test_merge_never_mutates_prompt_fields(project_db):
    profile = store.create_profile(
        name="Cinematic Soft",
        explicit=PreferenceFields(
            midi_mean_band="high",
            preferred_instruments=["Orchestra"],
        ),
        db_path=project_db,
    )
    request = LLMMusicGenerationRequest(
        prompt=LLMPromptParameters(
            genre="jazz",
            mood="calm",
            tempo=90,
            duration_bars=4,
            instruments=["piano", "bass"],
            key="D major",
        ),
        profile_id=profile.id,
        profile_strength="strong",
    )
    before = request.prompt.model_dump()
    merge = resolve_profile_merge(
        profile_id=profile.id,
        profile_strength="strong",
        db_path=project_db,
    )
    after = request.prompt.model_dump()
    assert before == after
    assert merge.soft_fragment
    assert "Orchestra" in merge.soft_fragment or "midi_mean_band" in merge.soft_fragment
    constraints = build_generation_constraints(request)
    assert constraints.key == "D major"
    assert request.prompt.instruments == ["piano", "bass"]
    assert request.prompt.key == "D major"
    assert merge.provenance["composer_profile_id"] == profile.id
    assert merge.provenance["profile_strength"] == "strong"


def test_off_strength_skips_injection(project_db):
    profile = store.create_profile(name="Unused", db_path=project_db)
    merge = resolve_profile_merge(
        profile_id=profile.id,
        profile_strength="off",
        db_path=project_db,
    )
    assert merge.soft_fragment == ""
    assert merge.provenance == {}


def test_append_and_provenance_nesting(project_db):
    profile = store.create_profile(
        name="Prov",
        explicit=PreferenceFields(tension_shape="arch"),
        db_path=project_db,
    )
    prompt = "SOFT creative preferences:\n{}"
    merge = resolve_profile_merge(
        profile_id=profile.id,
        profile_strength="normal",
        db_path=project_db,
    )
    combined = append_profile_fragment(prompt, merge.soft_fragment)
    assert combined.index("SOFT creative") < combined.index("composer-profile")
    enriched = merge_provenance_keys(
        {"pipeline_id": "llm_only", "generation_parameters": {"pipeline_id": "llm_only"}},
        merge,
    )
    assert enriched["composer_profile_id"] == profile.id
    assert enriched["generation_parameters"]["profile_strength"] == "normal"
