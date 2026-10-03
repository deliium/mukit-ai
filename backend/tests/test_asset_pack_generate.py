"""Synchronous asset-pack generate under LLM_FAKE_MODE."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ai_agents import registry as agent_registry
from app.ai_runtime import registry as model_registry
from app.db import reset_database_initialization_cache
from app.main import app
from app.services.project_store import get_project


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
        "title": "Mini Pack",
        "preset": "game_soundtrack_v1",
        "slots": [
            {"slot_id": "main_theme"},
            {"slot_id": "menu"},
            {"slot_id": "combat_low"},
        ],
        "include_rendering": False,
        "include_adaptive_scaffolds": True,
        "seed": 1,
    }


def test_generate_creates_projects_universe_and_theme(client: TestClient, tmp_path: Path) -> None:
    preview = client.post("/asset-packs/plan/preview", json={"brief": _mini_brief()})
    assert preview.status_code == 200, preview.text
    plan = preview.json()["plan"]
    assert len(plan["slots"]) == 3

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
    body = generated.json()
    assert body["pack"]["status"] in {"completed", "partial"}
    assert body["pack"]["universe_id"]

    slots = client.get(f"/asset-packs/{pack_id}/slots").json()["slots"]
    completed = [s for s in slots if s["status"] == "completed"]
    assert len(completed) >= 2
    project_ids = {s["project_id"] for s in completed if s["project_id"]}
    assert len(project_ids) >= 2

    for slot in completed:
        project = get_project(slot["project_id"])
        composition = json.loads(project.composition_json)
        assert composition["schema_version"] == "composition.v2"
        assert "events" in json.dumps(composition)

    seed = next(s for s in slots if s["slot_id"] == "main_theme")
    assert seed["status"] == "completed"
    seed_comp = json.loads(get_project(seed["project_id"]).composition_json)
    motifs = seed_comp.get("motifs") or []
    assert any(m.get("label") == "Theme A" for m in motifs)

    menu = next(s for s in slots if s["slot_id"] == "menu")
    if menu["status"] == "completed":
        menu_comp = json.loads(get_project(menu["project_id"]).composition_json)
        pinned = [
            m
            for m in (menu_comp.get("motifs") or [])
            if m.get("musical_universe_id") or m.get("theme_id")
        ]
        assert pinned or any(m.get("label") for m in (menu_comp.get("motifs") or []))

    # Pack body has no note keys
    pack_dump = json.dumps(body["pack"])
    assert '"events"' not in pack_dump
    assert '"notes"' not in pack_dump
    assert body["pack"].get("adaptive_score_id") is None  # on pack, not slots
    assert any(s.get("adaptive_score_id") for s in slots if s["status"] == "completed")
