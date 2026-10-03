"""Plan compiler and HTTP preview tests for asset packs."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.asset_pack_schemas import (
    GAME_SOUNDTRACK_V1_PROPAGATE,
    AssetPackBriefV1,
)
from app.db import reset_database_initialization_cache
from app.main import app
from app.services.asset_pack_plan import compile_asset_pack_plan


def _db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.delenv("COLLABORATION_ENABLED", raising=False)
    reset_database_initialization_cache()
    return db_path


def test_compile_game_preset_locked_defaults() -> None:
    brief = AssetPackBriefV1.model_validate(
        {
            "schema_version": "asset.pack.brief.v1",
            "title": "Forest Quest",
            "preset": "game_soundtrack_v1",
        }
    )
    plan = compile_asset_pack_plan(brief)
    assert plan.schema_version == "asset.pack.plan.v1"
    assert len(plan.slots) == 10
    seed = next(s for s in plan.slots if s.slot_id == "main_theme")
    menu = next(s for s in plan.slots if s.slot_id == "menu")
    assert seed.duration_seconds == 90
    assert menu.duration_seconds == 60
    assert set(plan.theme_policy.propagate.keys()) == set(GAME_SOUNDTRACK_V1_PROPAGATE.keys())
    assert plan.theme_policy.propagate["boss"].operation == "transpose"
    assert plan.theme_policy.propagate["boss"].transpose_semitones == 12
    assert plan.production.guarantee is False
    assert plan.production.master_target == "cinematic"
    dumped = plan.model_dump(mode="json")
    assert "tracks" not in dumped
    assert "events" not in dumped
    assert "notes" not in dumped


def test_preview_http_no_pack_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _db(tmp_path, monkeypatch)
    client = TestClient(app)
    response = client.post(
        "/asset-packs/plan/preview",
        json={
            "brief": {
                "schema_version": "asset.pack.brief.v1",
                "title": "Preview Only",
                "preset": "game_soundtrack_v1",
            }
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["plan"]["schema_version"] == "asset.pack.plan.v1"
    assert len(body["plan"]["slots"]) == 10
    listed = client.get("/asset-packs")
    assert listed.status_code == 200
    assert listed.json()["packs"] == []


def test_create_pack_planned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _db(tmp_path, monkeypatch)
    client = TestClient(app)
    created = client.post(
        "/asset-packs",
        json={
            "brief": {
                "schema_version": "asset.pack.brief.v1",
                "title": "Persist Me",
                "preset": "game_soundtrack_v1",
            }
        },
    )
    assert created.status_code == 201
    payload = created.json()
    assert payload["pack"]["status"] == "planned"
    assert payload["pack"]["id"].startswith("apack_")
    pack_id = payload["pack"]["id"]
    detail = client.get(f"/asset-packs/{pack_id}")
    assert detail.status_code == 200
    slots = client.get(f"/asset-packs/{pack_id}/slots")
    assert slots.status_code == 200
    assert len(slots.json()["slots"]) == 10
    assert all(s["status"] == "pending" for s in slots.json()["slots"])
