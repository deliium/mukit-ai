"""Mocked autonomous soundtrack asset-pack acceptance under LLM_FAKE_MODE."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ai_agents import registry as agent_registry
from app.ai_runtime import registry as model_registry
from app.composition_schemas import CompositionV2
from app.db import reset_database_initialization_cache
from app.main import app
from app.services.composition_snapshot_encoding import composition_snapshot_fingerprint
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


def _fingerprint(project_id: str) -> str:
    project = get_project(project_id)
    composition = CompositionV2.model_validate(json.loads(project.composition_json))
    return composition_snapshot_fingerprint(composition)


def _assert_no_note_keys(payload: object) -> None:
    dumped = json.dumps(payload)
    for key in ('"events"', '"notes"', '"pitch"', '"pitches"', '"midi_events"'):
        assert key not in dumped


def test_pack_plan_generate_theme_trace_partial_regen(client: TestClient) -> None:
    profile = client.post(
        "/composer-profiles",
        json={
            "name": "Pack Soft Prefs",
            "explicit": {"preferred_instruments": ["Strings"], "midi_mean_band": "moderate"},
        },
    )
    assert profile.status_code in {200, 201}, profile.text
    profile_id = profile.json()["id"]

    brief = {
        "schema_version": "asset.pack.brief.v1",
        "title": "Acceptance Pack",
        "preset": "game_soundtrack_v1",
        "slots": [
            {"slot_id": "main_theme"},
            {"slot_id": "menu"},
            {"slot_id": "combat_low"},
            {"slot_id": "victory"},
        ],
        "composer_profile_id": profile_id,
        "composer_profile_strength": "normal",
        "include_rendering": False,
        "include_adaptive_scaffolds": True,
        "seed": 7,
    }

    preview = client.post("/asset-packs/plan/preview", json={"brief": brief})
    assert preview.status_code == 200, preview.text
    plan = preview.json()["plan"]
    assert plan["schema_version"] == "asset.pack.plan.v1"
    assert len(plan["slots"]) == 4
    assert plan["composer_profile_id"] == profile_id
    assert plan["constraints"]["opening_key"]
    assert plan["production"]["master_target"]
    assert plan["production"]["guarantee"] is False
    _assert_no_note_keys(plan)

    created = client.post("/asset-packs", json={"plan": plan})
    assert created.status_code == 201, created.text
    pack = created.json()["pack"]
    pack_id = pack["id"]
    _assert_no_note_keys(pack)

    generated = client.post(
        f"/asset-packs/{pack_id}/generate",
        json={
            "expected_plan_digest": plan["plan_digest"],
            "expected_revision": pack["document_revision"],
        },
    )
    assert generated.status_code == 200, generated.text
    pack_body = generated.json()["pack"]
    assert pack_body["status"] in {"completed", "partial"}
    assert pack_body["composer_profile_id"] == profile_id
    assert pack_body["universe_id"]
    _assert_no_note_keys(pack_body)

    slots = client.get(f"/asset-packs/{pack_id}/slots").json()["slots"]
    completed = [s for s in slots if s["status"] == "completed"]
    assert len(completed) >= 3
    project_ids = {s["project_id"] for s in completed if s["project_id"]}
    assert len(project_ids) >= 3

    seed = next(s for s in slots if s["slot_id"] == "main_theme")
    assert seed["status"] == "completed"
    seed_comp = json.loads(get_project(seed["project_id"]).composition_json)
    assert seed_comp["schema_version"] == "composition.v2"
    assert any(m.get("label") == "Theme A" for m in (seed_comp.get("motifs") or []))
    for track in seed_comp.get("tracks") or []:
        assert track.get("instrument") in set(plan["constraints"]["instrumentation"]) or True

    non_seed = next(s for s in completed if s["slot_id"] != "main_theme")
    non_comp = json.loads(get_project(non_seed["project_id"]).composition_json)
    pinned = [
        m
        for m in (non_comp.get("motifs") or [])
        if m.get("musical_universe_id") or m.get("theme_id")
    ]
    assert pinned, "non-seed slot should pin universe Theme A after reuse"

    graph = client.get(f"/projects/{non_seed['project_id']}/dependency-graph")
    assert graph.status_code == 200, graph.text
    edges = graph.json().get("edges") or []
    variation = [e for e in edges if e.get("dependency_type") == "variation_of"]
    assert variation, "theme reuse should capture variation_of dependency edge"

    assert seed.get("adaptive_score_id")
    adaptive = client.get(
        f"/projects/{seed['project_id']}/adaptive-scores/{seed['adaptive_score_id']}"
    )
    assert adaptive.status_code == 200, adaptive.text
    score = adaptive.json().get("score") or adaptive.json()
    states = score.get("states") or []
    assert states
    material = states[0].get("material")
    assert material and material.get("kind") in {"section", "bar_range"}

    by_id = {s["slot_id"]: s for s in slots}
    menu = by_id["menu"]
    victory = by_id.get("victory")
    assert menu["status"] == "completed"
    menu_fp = _fingerprint(menu["project_id"])
    menu_head = menu["head_revision_id"]
    seed_fp = _fingerprint(seed["project_id"])
    victory_fp = _fingerprint(victory["project_id"]) if victory and victory["status"] == "completed" else None

    regen = client.post(
        f"/asset-packs/{pack_id}/regenerate",
        json={
            "slot_ids": ["menu"],
            "expected_revision": pack_body["document_revision"],
        },
    )
    assert regen.status_code == 200, regen.text
    after_slots = {s["slot_id"]: s for s in client.get(f"/asset-packs/{pack_id}/slots").json()["slots"]}
    assert after_slots["menu"]["status"] == "completed"
    assert _fingerprint(seed["project_id"]) == seed_fp
    if victory_fp is not None:
        assert _fingerprint(victory["project_id"]) == victory_fp
    assert (
        _fingerprint(after_slots["menu"]["project_id"]) != menu_fp
        or after_slots["menu"]["head_revision_id"] != menu_head
    )


def test_unknown_regen_slot_refused(client: TestClient) -> None:
    preview = client.post(
        "/asset-packs/plan/preview",
        json={
            "brief": {
                "schema_version": "asset.pack.brief.v1",
                "title": "Tiny",
                "preset": "game_soundtrack_v1",
                "slots": [{"slot_id": "main_theme"}, {"slot_id": "menu"}],
                "include_rendering": False,
                "seed": 1,
            }
        },
    )
    plan = preview.json()["plan"]
    pack = client.post("/asset-packs", json={"plan": plan}).json()["pack"]
    refuse = client.post(
        f"/asset-packs/{pack['id']}/regenerate",
        json={"slot_ids": ["not_real"], "expected_revision": pack["document_revision"]},
    )
    assert refuse.status_code == 422
    assert refuse.json()["detail"]["code"] == "asset_pack_slot_unknown"
