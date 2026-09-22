"""Tests for deterministic composer profile derive aggregation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.composer_profile_schemas import ComposerProfileDeriveSource, ComposerProfileError
from app.db import initialize_database, reset_database_initialization_cache
from app.services import composer_profile_derive as derive
from app.services import project_store


FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "fixtures"
    / "composition_v2_expressive.json"
)


@pytest.fixture
def project_db(tmp_path, monkeypatch):
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    reset_database_initialization_cache()
    initialize_database()
    return db_path


def _seed_project(name: str, db_path: Path):
    composition = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return project_store.create_project(
        name, composition=composition, db_path=db_path
    )


def test_derive_from_projects_builds_derived_not_events(project_db):
    a = _seed_project("Cinematic A", project_db)
    b = _seed_project("Cinematic B", project_db)
    profile, warnings = derive.derive_composer_profile(
        [
            ComposerProfileDeriveSource(project_id=a.id),
            ComposerProfileDeriveSource(project_id=b.id),
        ],
        name="My Cinematic Style",
        db_path=project_db,
    )
    assert profile.name == "My Cinematic Style"
    assert len(profile.source_projects) == 2
    assert preference_has_bands(profile)
    body = profile.model_dump()
    assert "events" not in body
    assert "tracks" not in body
    assert profile.explicit.midi_mean_band is None
    assert profile.derived.stats_meta is not None
    assert profile.derived.stats_meta["source_count"] == 2
    assert not any("DATASET" in w for w in warnings)


def preference_has_bands(profile) -> bool:
    derived = profile.derived
    return any(
        [
            derived.midi_mean_band,
            derived.rhythmic_density_band,
            derived.preferred_instruments,
            derived.tension_shape,
        ]
    )


def test_derive_empty_fails(project_db):
    with pytest.raises(ComposerProfileError) as exc:
        derive.derive_composer_profile(
            [ComposerProfileDeriveSource(project_id="missing_proj")],
            db_path=project_db,
        )
    assert exc.value.code == "composer_profile_derive_empty"


def test_derive_respects_source_cap(project_db, monkeypatch):
    monkeypatch.setenv("COMPOSER_PROFILE_MAX_SOURCE_PROJECTS", "1")
    a = _seed_project("One", project_db)
    b = _seed_project("Two", project_db)
    with pytest.raises(ComposerProfileError) as exc:
        derive.derive_composer_profile(
            [
                ComposerProfileDeriveSource(project_id=a.id),
                ComposerProfileDeriveSource(project_id=b.id),
            ],
            db_path=project_db,
        )
    assert exc.value.code == "composer_profile_cap_exceeded"
