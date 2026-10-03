"""HTTP tests for spatial scene CRUD, catalog, and compile."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.composition_schemas import CompositionV2
from app.db import reset_database_initialization_cache
from app.main import app
from app.services.composition_snapshot_encoding import composition_snapshot_fingerprint
from app.spatial_schemas import stem_set_fingerprint

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
    created = client.post(
        "/projects", json={"name": "Spatial", "composition": composition}
    )
    assert created.status_code == 201, created.text
    return created.json()["id"], composition


def test_catalog_side_effect_free_and_empty_list(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db_path = _client(tmp_path, monkeypatch)
    project_id, _ = _create_project(client)
    listed = client.get(f"/projects/{project_id}/spatial-scenes")
    assert listed.status_code == 200
    assert listed.json()["scenes"] == []
    catalog = client.get(f"/projects/{project_id}/spatial-scenes/presets")
    assert catalog.status_code == 200
    assert len(catalog.json()["presets"]) == 3
    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM spatial_scenes").fetchone()[0]
    assert count == 0


def test_clone_compile_and_no_composition_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db_path = _client(tmp_path, monkeypatch)
    project_id, composition = _create_project(client)
    before = _composition_text(db_path, project_id)
    fp = composition_snapshot_fingerprint(CompositionV2.model_validate(composition))
    created = client.post(
        f"/projects/{project_id}/spatial-scenes",
        json={
            "preset_id": "front_stereo",
            "name": "Front A",
            "source_composition_fingerprint": fp,
        },
    )
    assert created.status_code == 201, created.text
    scene_id = created.json()["scene"]["id"]
    assert created.json()["document_revision"] == 1

    listed = client.get(f"/projects/{project_id}/spatial-scenes")
    assert len(listed.json()["scenes"]) == 1

    compiled = client.post(
        f"/projects/{project_id}/spatial-scenes/{scene_id}/compile",
        json={"composition": composition, "at_tick": 0},
    )
    assert compiled.status_code == 200, compiled.text
    body = compiled.json()["preview"]
    assert body["stale"]["composition"] is False
    assert body["metrics"]["source_count"] >= 2
    assert "pcm" not in body
    assert all("foa" in s for s in body["sources"])

    # Unsaved draft fingerprint → soft-stale; still no project write.
    draft = json.loads(json.dumps(composition))
    draft["tracks"][0]["events"][0]["velocity"] = 40
    stale = client.post(
        f"/projects/{project_id}/spatial-scenes/{scene_id}/compile",
        json={"composition": draft},
    )
    assert stale.status_code == 200
    assert stale.json()["preview"]["stale"]["composition"] is True
    assert _composition_text(db_path, project_id) == before


def test_track_compile_requires_composition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = _client(tmp_path, monkeypatch)
    project_id, composition = _create_project(client)
    fp = composition_snapshot_fingerprint(CompositionV2.model_validate(composition))
    created = client.post(
        f"/projects/{project_id}/spatial-scenes",
        json={"preset_id": "close_intimate", "source_composition_fingerprint": fp},
    )
    scene_id = created.json()["scene"]["id"]
    missing = client.post(
        f"/projects/{project_id}/spatial-scenes/{scene_id}/compile",
        json={},
    )
    assert missing.status_code == 422
    assert missing.json()["detail"]["code"] == "composition_required"


def test_stem_only_compile_without_composition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db_path = _client(tmp_path, monkeypatch)
    project_id, composition = _create_project(client)
    before = _composition_text(db_path, project_id)
    fp = composition_snapshot_fingerprint(CompositionV2.model_validate(composition))
    prefixes = ["aaaa1111", "bbbb2222"]
    stem_fp = stem_set_fingerprint(prefixes)
    created = client.post(
        f"/projects/{project_id}/spatial-scenes",
        json={
            "name": "Stem scene",
            "scene": {
                "schema_version": "spatial.scene.v1",
                "name": "Stem scene",
                "source_composition_fingerprint": fp,
                "source_stem_set_id": "stemset_fake",
                "source_stem_set_fingerprint": stem_fp,
                "sources": [
                    {
                        "id": "ssrc_stem01",
                        "source_kind": "stem",
                        "stem_id": "stem_a",
                        "azimuth_deg": 40.0,
                        "elevation_deg": 0.0,
                        "distance": 2.0,
                        "spread": 0.1,
                    },
                    {
                        "id": "ssrc_stem02",
                        "source_kind": "stem",
                        "stem_id": "stem_b",
                        "azimuth_deg": -40.0,
                        "elevation_deg": 0.0,
                        "distance": 2.0,
                        "spread": 0.1,
                    },
                ],
            },
        },
    )
    assert created.status_code == 201, created.text
    scene_id = created.json()["scene"]["id"]
    compiled = client.post(
        f"/projects/{project_id}/spatial-scenes/{scene_id}/compile",
        json={
            "stem_set_id": "stemset_fake",
            "stems": [
                {"stem_id": "stem_a", "sha256_prefix": "aaaa1111"},
                {"stem_id": "stem_b", "sha256_prefix": "bbbb2222"},
            ],
        },
    )
    assert compiled.status_code == 200, compiled.text
    assert compiled.json()["preview"]["metrics"]["stem_count"] == 2
    assert _composition_text(db_path, project_id) == before


def test_cas_conflict_409(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = _client(tmp_path, monkeypatch)
    project_id, composition = _create_project(client)
    fp = composition_snapshot_fingerprint(CompositionV2.model_validate(composition))
    created = client.post(
        f"/projects/{project_id}/spatial-scenes",
        json={"preset_id": "circle_ensemble", "source_composition_fingerprint": fp},
    )
    scene = created.json()["scene"]
    scene_id = scene["id"]
    conflict = client.put(
        f"/projects/{project_id}/spatial-scenes/{scene_id}",
        json={"scene": {**scene, "name": "Nope"}, "expected_document_revision": 99},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "spatial_scene_conflict"
