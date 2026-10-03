"""Partial regenerate leaves untouched slots byte-identical."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ai_agents import registry as agent_registry
from app.ai_runtime import registry as model_registry
from app.db import reset_database_initialization_cache
from app.main import app
from app.services.composition_snapshot_encoding import composition_snapshot_fingerprint
from app.services.project_store import get_project
from app.composition_schemas import CompositionV2


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PROJECT_DB_PATH", str(tmp_path / "projects.db"))
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    monkeypatch.delenv("FAKE_CRITIC_REVISE_PASSES", raising=False)
    monkeypatch.delenv("MUSIC_TRANSFORMER_CHECKPOINT", raising=False)
    monkeypatch.delenv("COLLABORATION_ENABLED", raising=False)
    reset_database_initialization_cache()
    agent_registry.clear_registry_for_tests()
    model_registry.clear_registry_for_tests()
    with TestClient(app) as test_client:
        yield test_client
    agent_registry.clear_registry_for_tests()
    model_registry.clear_registry_for_tests()


def _mini_brief() -> dict:
    return {
        "schema_version": "asset.pack.brief.v1",
        "title": "Regen Pack",
        "preset": "game_soundtrack_v1",
        "slots": [
            {"slot_id": "main_theme"},
            {"slot_id": "menu"},
            {"slot_id": "combat_low"},
        ],
        "include_rendering": False,
        "include_adaptive_scaffolds": True,
        "seed": 2,
    }


def _fingerprint(project_id: str) -> str:
    project = get_project(project_id)
    composition = CompositionV2.model_validate(json.loads(project.composition_json))
    return composition_snapshot_fingerprint(composition)


def test_partial_regenerate_changes_only_selected_slot(client: TestClient) -> None:
    preview = client.post("/asset-packs/plan/preview", json={"brief": _mini_brief()})
    assert preview.status_code == 200, preview.text
    plan = preview.json()["plan"]

    created = client.post("/asset-packs", json={"plan": plan})
    assert created.status_code == 201, created.text
    pack = created.json()["pack"]
    pack_id = pack["id"]

    generated = client.post(
        f"/asset-packs/{pack_id}/generate",
        json={
            "expected_plan_digest": plan["plan_digest"],
            "expected_revision": pack["document_revision"],
        },
    )
    assert generated.status_code == 200, generated.text
    pack_after = generated.json()["pack"]
    assert pack_after["status"] in {"completed", "partial"}

    slots = client.get(f"/asset-packs/{pack_id}/slots").json()["slots"]
    by_id = {s["slot_id"]: s for s in slots}
    assert by_id["main_theme"]["status"] == "completed"
    assert by_id["menu"]["status"] == "completed"
    assert by_id["combat_low"]["status"] == "completed"

    menu_fp_before = _fingerprint(by_id["menu"]["project_id"])
    combat_fp_before = _fingerprint(by_id["combat_low"]["project_id"])
    seed_fp_before = _fingerprint(by_id["main_theme"]["project_id"])
    menu_head_before = by_id["menu"]["head_revision_id"]
    combat_head_before = by_id["combat_low"]["head_revision_id"]

    unknown = client.post(
        f"/asset-packs/{pack_id}/regenerate",
        json={
            "slot_ids": ["not_a_slot"],
            "expected_revision": pack_after["document_revision"],
        },
    )
    assert unknown.status_code == 422

    regen = client.post(
        f"/asset-packs/{pack_id}/regenerate",
        json={
            "slot_ids": ["combat_low"],
            "expected_revision": pack_after["document_revision"],
        },
    )
    assert regen.status_code == 200, regen.text
    assert regen.json()["pack"]["status"] in {"completed", "partial"}

    slots_after = client.get(f"/asset-packs/{pack_id}/slots").json()["slots"]
    after = {s["slot_id"]: s for s in slots_after}

    assert after["menu"]["project_id"] == by_id["menu"]["project_id"]
    assert after["main_theme"]["project_id"] == by_id["main_theme"]["project_id"]
    assert after["combat_low"]["project_id"] == by_id["combat_low"]["project_id"]

    assert _fingerprint(after["menu"]["project_id"]) == menu_fp_before
    assert _fingerprint(after["main_theme"]["project_id"]) == seed_fp_before
    assert after["menu"]["head_revision_id"] == menu_head_before

    combat_fp_after = _fingerprint(after["combat_low"]["project_id"])
    assert combat_fp_after != combat_fp_before or after["combat_low"]["head_revision_id"] != (
        combat_head_before
    )
    assert after["combat_low"]["status"] == "completed"
    assert after["combat_low"].get("adaptive_score_id")


def test_architecture_forbid_covers_pack_modules() -> None:
    """Smoke: pack store modules remain on the ai_agents forbid list."""
    from pathlib import Path as P

    text = (P(__file__).resolve().parents[0] / "test_ai_agents_architecture.py").read_text(
        encoding="utf-8"
    )
    assert "from app.services.asset_pack_store" in text
    assert "from app.services.asset_pack_generate" in text
    assert "from app.services.asset_pack_brief" in text
