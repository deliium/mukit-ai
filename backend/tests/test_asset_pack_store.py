"""Store tests: CAS, embedded-note reject, no composition_json writes."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.asset_pack_schemas import AssetPackBriefV1, AssetPackError
from app.db import reset_database_initialization_cache
from app.services.asset_pack_plan import compile_asset_pack_plan
from app.services.asset_pack_store import (
    create_pack,
    get_pack,
    update_pack_cas,
    upsert_slot_status,
)


def _db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.delenv("COLLABORATION_ENABLED", raising=False)
    reset_database_initialization_cache()
    return db_path


def _plan():
    brief = AssetPackBriefV1.model_validate(
        {
            "schema_version": "asset.pack.brief.v1",
            "title": "Store Pack",
            "preset": "game_soundtrack_v1",
        }
    )
    return compile_asset_pack_plan(brief)


def test_create_and_cas(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = _db(tmp_path, monkeypatch)
    record = create_pack(_plan(), db_path=db_path)
    assert record.status == "planned"
    assert record.document_revision == 1
    assert len(record.slots) == 10

    with pytest.raises(AssetPackError) as conflict:
        update_pack_cas(
            record.id,
            expected_revision=9,
            status="generating",
            db_path=db_path,
        )
    assert conflict.value.code == "asset_pack_conflict"

    updated = update_pack_cas(
        record.id,
        expected_revision=1,
        status="generating",
        db_path=db_path,
    )
    assert updated.document_revision == 2
    assert updated.status == "generating"
    assert get_pack(record.id, db_path=db_path).status == "generating"


def test_slot_status_update(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = _db(tmp_path, monkeypatch)
    record = create_pack(_plan(), db_path=db_path)
    slot = upsert_slot_status(
        record.id,
        "main_theme",
        status="running",
        project_id="proj_test",
        db_path=db_path,
    )
    assert slot.status == "running"
    assert slot.project_id == "proj_test"

    with pytest.raises(AssetPackError) as unknown:
        upsert_slot_status(
            record.id,
            "not_a_slot",
            status="failed",
            db_path=db_path,
        )
    assert unknown.value.code == "asset_pack_slot_unknown"


def test_store_does_not_create_projects_table_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = _db(tmp_path, monkeypatch)
    create_pack(_plan(), db_path=db_path)
    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    assert count == 0
