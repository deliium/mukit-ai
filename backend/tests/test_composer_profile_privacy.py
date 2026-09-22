"""Privacy / logging regression guards for composer profiles."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.composer_profile_schemas import (
    ComposerProfileV1,
    PreferenceFields,
)
from app.db import initialize_database, reset_database_initialization_cache
from app.schemas import LLMMusicGenerationRequest, LLMPromptParameters
from app.services import composer_profile_store as store
from app.services.composer_profile_merge import resolve_profile_merge


SERVICES_DIR = Path(__file__).resolve().parents[1] / "app" / "services"
PROFILE_SERVICE_FILES = (
    "composer_profile_store.py",
    "composer_profile_derive.py",
    "composer_profile_resolve.py",
    "composer_profile_merge.py",
)


@pytest.fixture
def project_db(tmp_path, monkeypatch):
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    reset_database_initialization_cache()
    initialize_database()
    return db_path


def test_schema_rejects_event_bearing_payloads():
    with pytest.raises(ValidationError):
        PreferenceFields.model_validate({"events": [{"pitch": 60}]})
    with pytest.raises(ValidationError):
        ComposerProfileV1.model_validate(
            {
                "id": "prof_x",
                "name": "Bad",
                "created_at": "2026-09-22T12:00:00Z",
                "updated_at": "2026-09-22T12:00:00Z",
                "tracks": [],
            }
        )


def test_store_rejects_secret_like_notes(project_db):
    from app.composer_profile_schemas import ComposerProfileError

    with pytest.raises(ComposerProfileError) as exc:
        store.create_profile(
            name="Secretive",
            notes="openai_api_key sk-abcdefghijklmnopqrstuvwxyz012345",
            db_path=project_db,
        )
    assert exc.value.code == "composer_profile_forbidden_payload"


def test_merge_never_changes_prompt_instruments_or_key(project_db):
    profile = store.create_profile(
        name="Prefs",
        explicit=PreferenceFields(preferred_instruments=["Orchestra"]),
        db_path=project_db,
    )
    request = LLMMusicGenerationRequest(
        prompt=LLMPromptParameters(
            genre="rock",
            mood="energetic",
            key="E minor",
            instruments=["guitar", "drums"],
            duration_bars=4,
        ),
        profile_id=profile.id,
        profile_strength="strong",
    )
    snapshot = request.prompt.model_dump()
    resolve_profile_merge(
        profile_id=profile.id,
        profile_strength="strong",
        db_path=project_db,
    )
    assert request.prompt.model_dump() == snapshot
    assert request.prompt.instruments == ["guitar", "drums"]
    assert request.prompt.key == "E minor"


def test_profile_services_do_not_import_dataset_cli():
    forbidden_modules = {
        "app.dataset",
        "app.dataset.cli",
        "app.dataset.ingest",
    }
    for name in PROFILE_SERVICE_FILES:
        path = SERVICES_DIR / name
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module)
        overlap = imported.intersection(forbidden_modules)
        assert not overlap, f"{name} imports dataset modules: {overlap}"
        text = path.read_text(encoding="utf-8")
        assert "DATASET_ROOT" not in text or "never" in text.lower()
