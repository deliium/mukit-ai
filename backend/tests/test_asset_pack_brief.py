"""Pack-side Composer Profile → creative.brief.v1 soft fold tests."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from app.asset_pack_schemas import AssetPackBriefV1, AssetPackError
from app.composer_profile_schemas import PreferenceFields
from app.db import initialize_database, reset_database_initialization_cache
from app.services.asset_pack_brief import (
    build_slot_creative_brief,
    preferred_instruments_from_merge,
    resolve_pack_profile_merge,
)
from app.services.asset_pack_plan import compile_asset_pack_plan
from app.services import composer_profile_store as profile_store


@pytest.fixture
def project_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    reset_database_initialization_cache()
    initialize_database()
    return db_path


def _plan(*, profile_id: str | None = None, strength: str = "normal"):
    brief = AssetPackBriefV1.model_validate(
        {
            "schema_version": "asset.pack.brief.v1",
            "title": "Brief Fold Pack",
            "preset": "game_soundtrack_v1",
            "opening_key": "D minor",
            "composer_profile_id": profile_id,
            "composer_profile_strength": strength,
            "production": {
                "schema_version": "asset.pack.production.v1",
                "master_target": "cinematic",
                "guarantee": False,
                "catalog_instrument_ids": ["violin", "cello", "french_horn"],
            },
        }
    )
    return compile_asset_pack_plan(brief)


def test_hard_instruments_and_key_win(project_db: Path) -> None:
    profile = profile_store.create_profile(
        name="Soft Orchestra",
        explicit=PreferenceFields(preferred_instruments=["Orchestra", "Choir"]),
        db_path=project_db,
    )
    plan = _plan(profile_id=profile.id, strength="strong")
    seed = next(s for s in plan.slots if s.slot_id == "main_theme")
    built = build_slot_creative_brief(plan, seed, db_path=project_db)
    assert built.brief.opening_key == "D minor"
    assert built.brief.instrumentation == ["violin", "cello", "french_horn"]
    soft = preferred_instruments_from_merge(
        resolve_pack_profile_merge(plan, db_path=project_db),
        profile_id=profile.id,
        db_path=project_db,
    )
    assert "Orchestra" in soft
    assert built.merge_applied is True
    assert built.profile_id == profile.id


def test_empty_profile_identity_brief(project_db: Path) -> None:
    plan = _plan(profile_id=None)
    seed = next(s for s in plan.slots if s.slot_id == "main_theme")
    built = build_slot_creative_brief(plan, seed, db_path=project_db)
    assert built.merge_applied is False
    assert built.brief.instrumentation == list(plan.constraints.instrumentation)
    assert built.brief.opening_key == plan.constraints.opening_key


def test_unknown_profile_raises(project_db: Path) -> None:
    plan = _plan(profile_id="cprof_deadbeefdeadbeef", strength="normal")
    with pytest.raises(AssetPackError) as exc:
        resolve_pack_profile_merge(plan, db_path=project_db)
    assert exc.value.code == "asset_pack_profile_unknown"


def test_never_logs_soft_fragment_at_info(
    project_db: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    profile = profile_store.create_profile(
        name="Quiet Log",
        explicit=PreferenceFields(
            preferred_instruments=["Harp"],
            tension_shape="arch",
        ),
        db_path=project_db,
    )
    plan = _plan(profile_id=profile.id, strength="normal")
    seed = next(s for s in plan.slots if s.slot_id == "main_theme")
    with caplog.at_level(logging.INFO, logger="app.services.asset_pack_brief"):
        built = build_slot_creative_brief(plan, seed, db_path=project_db)
    assert built.merge_applied is True
    for record in caplog.records:
        if record.name != "app.services.asset_pack_brief":
            continue
        message = record.getMessage()
        assert "SOFT composer-profile" not in message
        assert "Harp" not in message
