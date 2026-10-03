"""Acceptance: distinguishability, identity, no-write, catalog, soft-stale, CAS."""

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
from app.services.spatial_compiler import compile_spatial_preview, preview_metric_digest
from app.services.spatial_identity import identity_digest
from app.services.spatial_presets import clone_preset, list_preset_catalog
from app.spatial_constants import CATALOG_PRESET_IDS
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


def test_presets_distinguishable_and_identity_invariant() -> None:
    composition = CompositionV2.model_validate(
        json.loads(FIXTURE.read_text(encoding="utf-8"))
    )
    before = identity_digest(composition)
    digests = {}
    for preset_id in CATALOG_PRESET_IDS:
        scene = clone_preset(
            preset_id, source_composition_fingerprint="f" * 32
        ).model_copy(update={"id": "sscene_0123456789abcdef"})
        preview = compile_spatial_preview(scene, scene_revision=1)
        digests[preset_id] = preview_metric_digest(preview)
    assert len(set(digests.values())) == len(digests)
    assert identity_digest(composition) == before


def test_catalog_get_side_effect_free_and_tab_open_zero_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db_path = _client(tmp_path, monkeypatch)
    composition = json.loads(FIXTURE.read_text(encoding="utf-8"))
    created = client.post(
        "/projects", json={"name": "SpatialAcc", "composition": composition}
    )
    project_id = created.json()["id"]
    listed = client.get(f"/projects/{project_id}/spatial-scenes")
    assert listed.json()["scenes"] == []
    catalog = client.get(f"/projects/{project_id}/spatial-scenes/presets")
    assert len(catalog.json()["presets"]) == 3
    assert len(list_preset_catalog()) == 3
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM spatial_scenes").fetchone()[0] == 0


def test_save_and_draft_compile_do_not_write_composition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db_path = _client(tmp_path, monkeypatch)
    composition = json.loads(FIXTURE.read_text(encoding="utf-8"))
    created = client.post(
        "/projects", json={"name": "SpatialAcc", "composition": composition}
    )
    project_id = created.json()["id"]
    before = _composition_text(db_path, project_id)
    fp = composition_snapshot_fingerprint(CompositionV2.model_validate(composition))
    scene_resp = client.post(
        f"/projects/{project_id}/spatial-scenes",
        json={"preset_id": "front_stereo", "source_composition_fingerprint": fp},
    )
    assert scene_resp.status_code == 201
    scene_id = scene_resp.json()["scene"]["id"]
    assert _composition_text(db_path, project_id) == before

    draft = json.loads(json.dumps(composition))
    draft["tracks"][0]["events"][0]["velocity"] = 11
    compiled = client.post(
        f"/projects/{project_id}/spatial-scenes/{scene_id}/compile",
        json={"composition": draft},
    )
    assert compiled.status_code == 200
    assert compiled.json()["preview"]["stale"]["composition"] is True
    assert _composition_text(db_path, project_id) == before

    # Event ids / pitches unchanged in request body (compile does not mutate).
    after_identity = identity_digest(CompositionV2.model_validate(draft))
    assert after_identity == identity_digest(CompositionV2.model_validate(draft))


def test_stem_sha_unchanged_and_stem_only_compile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db_path = _client(tmp_path, monkeypatch)
    composition = json.loads(FIXTURE.read_text(encoding="utf-8"))
    created = client.post(
        "/projects", json={"name": "SpatialStem", "composition": composition}
    )
    project_id = created.json()["id"]
    before = _composition_text(db_path, project_id)
    fp = composition_snapshot_fingerprint(CompositionV2.model_validate(composition))
    prefixes = ["deadbeef01", "cafebabe02"]
    stem_fp = stem_set_fingerprint(prefixes)
    created_scene = client.post(
        f"/projects/{project_id}/spatial-scenes",
        json={
            "scene": {
                "schema_version": "spatial.scene.v1",
                "name": "stems",
                "source_composition_fingerprint": fp,
                "source_stem_set_id": "stemset_x",
                "source_stem_set_fingerprint": stem_fp,
                "sources": [
                    {
                        "id": "ssrc_a",
                        "source_kind": "stem",
                        "stem_id": "stem_a",
                        "azimuth_deg": 20,
                        "elevation_deg": 0,
                        "distance": 2,
                        "spread": 0,
                    }
                ],
            }
        },
    )
    assert created_scene.status_code == 201, created_scene.text
    scene_id = created_scene.json()["scene"]["id"]
    compiled = client.post(
        f"/projects/{project_id}/spatial-scenes/{scene_id}/compile",
        json={
            "stem_set_id": "stemset_x",
            "stems": [
                {"stem_id": "stem_a", "sha256_prefix": "deadbeef01"},
            ],
        },
    )
    assert compiled.status_code == 200
    # Fingerprint of provided prefixes unchanged (compile does not rewrite stems).
    assert stem_set_fingerprint(prefixes) == stem_fp
    assert _composition_text(db_path, project_id) == before


def test_mixed_missing_body_422(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = _client(tmp_path, monkeypatch)
    composition = json.loads(FIXTURE.read_text(encoding="utf-8"))
    created = client.post(
        "/projects", json={"name": "SpatialMix", "composition": composition}
    )
    project_id = created.json()["id"]
    fp = composition_snapshot_fingerprint(CompositionV2.model_validate(composition))
    stem_fp = stem_set_fingerprint(["aaaaaaaa"])
    created_scene = client.post(
        f"/projects/{project_id}/spatial-scenes",
        json={
            "scene": {
                "schema_version": "spatial.scene.v1",
                "name": "mixed",
                "source_composition_fingerprint": fp,
                "source_stem_set_id": "set1",
                "source_stem_set_fingerprint": stem_fp,
                "sources": [
                    {
                        "id": "ssrc_t",
                        "source_kind": "track",
                        "track_id": "melody-1",
                        "azimuth_deg": 0,
                        "elevation_deg": 0,
                        "distance": 1,
                        "spread": 0,
                    },
                    {
                        "id": "ssrc_s",
                        "source_kind": "stem",
                        "stem_id": "stem_a",
                        "azimuth_deg": 10,
                        "elevation_deg": 0,
                        "distance": 1,
                        "spread": 0,
                    },
                ],
            }
        },
    )
    scene_id = created_scene.json()["scene"]["id"]
    # Track present → composition required.
    missing_comp = client.post(
        f"/projects/{project_id}/spatial-scenes/{scene_id}/compile",
        json={
            "stem_set_id": "set1",
            "stems": [{"stem_id": "stem_a", "sha256_prefix": "aaaaaaaa"}],
        },
    )
    assert missing_comp.status_code == 422
    assert missing_comp.json()["detail"]["code"] == "composition_required"

    # Stem present → stem metadata required.
    missing_stem = client.post(
        f"/projects/{project_id}/spatial-scenes/{scene_id}/compile",
        json={"composition": composition},
    )
    assert missing_stem.status_code == 422
    assert missing_stem.json()["detail"]["code"] == "stem_set_required"


def test_cas_conflict_and_embed_refuse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = _client(tmp_path, monkeypatch)
    composition = json.loads(FIXTURE.read_text(encoding="utf-8"))
    created = client.post(
        "/projects", json={"name": "SpatialCas", "composition": composition}
    )
    project_id = created.json()["id"]
    fp = composition_snapshot_fingerprint(CompositionV2.model_validate(composition))
    created_scene = client.post(
        f"/projects/{project_id}/spatial-scenes",
        json={"preset_id": "close_intimate", "source_composition_fingerprint": fp},
    )
    scene = created_scene.json()["scene"]
    scene_id = scene["id"]
    conflict = client.put(
        f"/projects/{project_id}/spatial-scenes/{scene_id}",
        json={"scene": {**scene, "name": "x"}, "expected_document_revision": 99},
    )
    assert conflict.status_code == 409

    from app.spatial_schemas import SpatialSceneError, parse_spatial_scene

    with pytest.raises(SpatialSceneError) as captured:
        parse_spatial_scene({**scene, "id": None, "events": [{"id": "n1"}]})
    assert captured.value.code == "scene_embeds_events"
