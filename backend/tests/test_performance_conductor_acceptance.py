"""Acceptance: distinguishable presets, identity, ties, unsaved draft, catalog."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.composition_schemas import CompositionV2
from app.db import reset_database_initialization_cache
from app.main import app
from app.performance_conductor_constants import CATALOG_PRESET_IDS
from app.services.composition_snapshot_encoding import composition_snapshot_fingerprint
from app.services.performance_conductor import realize_performance
from app.services.performance_identity import identity_digest, metrics_digest
from app.services.performance_presets import clone_preset, list_preset_catalog

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "fixtures"
    / "composition_v2_expressive.json"
)


def _composition() -> CompositionV2:
    return CompositionV2.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.delenv("COLLABORATION_ENABLED", raising=False)
    monkeypatch.delenv("PERFORMANCE_CONDUCTOR_AI_ENABLED", raising=False)
    reset_database_initialization_cache()
    return TestClient(app), db_path


def test_four_presets_distinguishable_identity_holds() -> None:
    composition = _composition()
    before = identity_digest(composition)
    digests: set[str] = set()
    for preset_id in CATALOG_PRESET_IDS:
        plan = clone_preset(preset_id, source_composition_fingerprint="x" * 32)
        plan = plan.model_copy(update={"id": f"pplan_{'0'*16}"})
        realization = realize_performance(composition, plan, plan_revision=1)
        digests.add(metrics_digest(realization.metrics))
        assert identity_digest(composition) == before
        assert all("pitch" not in n.model_dump() for n in realization.notes)
    assert len(digests) == 4


def test_tie_chain_shared_tick_delta() -> None:
    composition = _composition()
    plan = clone_preset("dramatic", source_composition_fingerprint="y" * 32)
    plan = plan.model_copy(update={"id": f"pplan_{'1'*16}"})
    realization = realize_performance(composition, plan, plan_revision=1)
    by_id = {n.event_id: n for n in realization.notes}
    assert by_id["m3"].tick_delta == by_id["m4"].tick_delta


def test_unsaved_draft_realize_does_not_write_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db_path = _client(tmp_path, monkeypatch)
    composition = json.loads(FIXTURE.read_text(encoding="utf-8"))
    created = client.post("/projects", json={"name": "A", "composition": composition})
    project_id = created.json()["id"]
    before = sqlite3.connect(db_path).execute(
        "SELECT composition_json FROM projects WHERE id = ?",
        (project_id,),
    ).fetchone()[0]
    fp = composition_snapshot_fingerprint(CompositionV2.model_validate(composition))
    plan = client.post(
        f"/projects/{project_id}/performance-plans",
        json={"preset_id": "intimate", "composition": composition},
    ).json()
    plan_id = plan["plan"]["id"]
    draft = json.loads(json.dumps(composition))
    draft["tempo"] = 111
    realized = client.post(
        f"/projects/{project_id}/performance-plans/{plan_id}/realize",
        json={"composition": draft},
    )
    assert realized.status_code == 200
    assert realized.json()["stale"] is True
    after = sqlite3.connect(db_path).execute(
        "SELECT composition_json FROM projects WHERE id = ?",
        (project_id,),
    ).fetchone()[0]
    assert after == before
    # Saving the plan also must not rewrite composition.
    put = client.put(
        f"/projects/{project_id}/performance-plans/{plan_id}",
        json={
            "expected_document_revision": plan["document_revision"],
            "plan": {
                **plan["plan"],
                "name": "Renamed",
                "source_composition_fingerprint": fp,
            },
        },
    )
    assert put.status_code == 200
    after2 = sqlite3.connect(db_path).execute(
        "SELECT composition_json FROM projects WHERE id = ?",
        (project_id,),
    ).fetchone()[0]
    assert after2 == before


def test_catalog_get_side_effect_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db_path = _client(tmp_path, monkeypatch)
    composition = json.loads(FIXTURE.read_text(encoding="utf-8"))
    project_id = client.post(
        "/projects", json={"name": "A", "composition": composition}
    ).json()["id"]
    assert len(list_preset_catalog()) == 4
    for _ in range(3):
        assert client.get(f"/projects/{project_id}/performance-plans/presets").status_code == 200
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM performance_plans").fetchone()[0] == 0


def test_ai_off_no_model_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = _client(tmp_path, monkeypatch)
    composition = json.loads(FIXTURE.read_text(encoding="utf-8"))
    project_id = client.post(
        "/projects", json={"name": "A", "composition": composition}
    ).json()["id"]
    plan_id = client.post(
        f"/projects/{project_id}/performance-plans",
        json={
            "preset_id": "restrained",
            "composition": composition,
            "engine": "ai_augmented",
        },
    ).json()["plan"]["id"]
    response = client.post(
        f"/projects/{project_id}/performance-plans/{plan_id}/realize",
        json={"composition": composition},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "ai_augment_disabled"
