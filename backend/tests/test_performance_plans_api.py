"""HTTP tests for performance plan CRUD, catalog, realize, compare."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import reset_database_initialization_cache
from app.main import app
from app.services.composition_snapshot_encoding import composition_snapshot_fingerprint
from app.composition_schemas import CompositionV2

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "fixtures"
    / "composition_v2_expressive.json"
)


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.delenv("COLLABORATION_ENABLED", raising=False)
    monkeypatch.delenv("PERFORMANCE_CONDUCTOR_AI_ENABLED", raising=False)
    reset_database_initialization_cache()
    return TestClient(app), db_path


def _composition_text(db_path: Path, project_id: str) -> str:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT composition_json FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()
    assert row is not None
    return str(row[0])


def _create_project(client: TestClient) -> tuple[str, dict]:
    composition = json.loads(FIXTURE.read_text(encoding="utf-8"))
    created = client.post("/projects", json={"name": "Perf", "composition": composition})
    assert created.status_code == 201, created.text
    return created.json()["id"], composition


def test_catalog_side_effect_free_and_empty_list(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db_path = _client(tmp_path, monkeypatch)
    project_id, _ = _create_project(client)
    listed = client.get(f"/projects/{project_id}/performance-plans")
    assert listed.status_code == 200
    assert listed.json()["plans"] == []
    catalog = client.get(f"/projects/{project_id}/performance-plans/presets")
    assert catalog.status_code == 200
    assert len(catalog.json()["presets"]) == 4
    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM performance_plans").fetchone()[0]
    assert count == 0


def test_clone_realize_compare_and_no_composition_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db_path = _client(tmp_path, monkeypatch)
    project_id, composition = _create_project(client)
    before = _composition_text(db_path, project_id)
    fp = composition_snapshot_fingerprint(CompositionV2.model_validate(composition))
    created = client.post(
        f"/projects/{project_id}/performance-plans",
        json={
            "preset_id": "dramatic",
            "name": "Dramatic A",
            "source_composition_fingerprint": fp,
        },
    )
    assert created.status_code == 201, created.text
    plan_id = created.json()["plan"]["id"]
    assert created.json()["document_revision"] == 1

    listed = client.get(f"/projects/{project_id}/performance-plans")
    assert len(listed.json()["plans"]) == 1

    realized = client.post(
        f"/projects/{project_id}/performance-plans/{plan_id}/realize",
        json={"composition": composition},
    )
    assert realized.status_code == 200, realized.text
    body = realized.json()
    assert body["stale"] is False
    assert body["realization"]["metrics"]["note_count"] > 0
    assert all("pitch" not in note for note in body["realization"]["notes"])

    compared = client.post(
        f"/projects/{project_id}/performance-plans/{plan_id}/compare",
        json={"composition": composition},
    )
    assert compared.status_code == 200, compared.text
    assert compared.json()["performed_metrics"]["mean_abs_tick_delta"] > 0

    # Unsaved draft fingerprint → soft-stale; still no project write.
    draft = json.loads(json.dumps(composition))
    draft["tracks"][0]["events"][0]["velocity"] = 40
    stale = client.post(
        f"/projects/{project_id}/performance-plans/{plan_id}/realize",
        json={"composition": draft},
    )
    assert stale.status_code == 200
    assert stale.json()["stale"] is True
    assert _composition_text(db_path, project_id) == before


def test_realize_requires_composition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = _client(tmp_path, monkeypatch)
    project_id, composition = _create_project(client)
    fp = composition_snapshot_fingerprint(CompositionV2.model_validate(composition))
    created = client.post(
        f"/projects/{project_id}/performance-plans",
        json={"preset_id": "intimate", "source_composition_fingerprint": fp},
    )
    plan_id = created.json()["plan"]["id"]
    missing = client.post(
        f"/projects/{project_id}/performance-plans/{plan_id}/realize",
        json={},
    )
    assert missing.status_code == 422


def test_ai_flag_off_refuses_ai_engine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = _client(tmp_path, monkeypatch)
    project_id, composition = _create_project(client)
    fp = composition_snapshot_fingerprint(CompositionV2.model_validate(composition))
    created = client.post(
        f"/projects/{project_id}/performance-plans",
        json={
            "preset_id": "restrained",
            "source_composition_fingerprint": fp,
            "engine": "ai_augmented",
        },
    )
    assert created.status_code == 201
    plan_id = created.json()["plan"]["id"]
    realized = client.post(
        f"/projects/{project_id}/performance-plans/{plan_id}/realize",
        json={"composition": composition},
    )
    assert realized.status_code == 422
    assert realized.json()["detail"]["code"] == "ai_augment_disabled"
