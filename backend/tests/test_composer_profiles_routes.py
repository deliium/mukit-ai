"""HTTP route tests for /composer-profiles CRUD, CAS, promote, derive, export."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import initialize_database, reset_database_initialization_cache
from app.main import app
from app.services import project_store


FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "fixtures"
    / "composition_v2_expressive.json"
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    reset_database_initialization_cache()
    initialize_database()
    return TestClient(app)


def test_crud_cas_promote_delete(client):
    created = client.post("/composer-profiles", json={"name": "My Style"}).json()
    assert created["id"].startswith("prof_")
    profile_id = created["id"]

    listed = client.get("/composer-profiles").json()
    assert len(listed) == 1

    got = client.get(f"/composer-profiles/{profile_id}").json()
    assert got["name"] == "My Style"

    # CAS miss
    conflict = client.put(
        f"/composer-profiles/{profile_id}",
        json={"expected_updated_at": "1999-01-01T00:00:00Z", "name": "Nope"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "composer_profile_conflict"

    # Seed derived via direct update of body fields
    updated = client.put(
        f"/composer-profiles/{profile_id}",
        json={
            "expected_updated_at": got["updated_at"],
            "derived": {"midi_mean_band": "high", "preferred_instruments": ["Strings"]},
        },
    ).json()
    assert updated["derived"]["midi_mean_band"] == "high"

    promoted = client.post(
        f"/composer-profiles/{profile_id}/promote",
        json={"expected_updated_at": updated["updated_at"]},
    ).json()
    assert promoted["explicit"]["midi_mean_band"] == "high"

    deleted = client.delete(f"/composer-profiles/{profile_id}")
    assert deleted.status_code == 204
    assert client.get(f"/composer-profiles/{profile_id}").status_code == 404


def test_derive_save_preview_export_import(client, tmp_path, monkeypatch):
    monkeypatch.setenv("PROJECT_DB_PATH", str(tmp_path / "projects.db"))
    composition = json.loads(FIXTURE.read_text(encoding="utf-8"))
    project = project_store.create_project("Src", composition=composition)

    preview = client.post(
        "/composer-profiles/derive",
        json={"sources": [{"project_id": project.id}]},
    )
    assert preview.status_code == 200
    assert preview.json()["persisted"] is False
    assert preview.json()["profile"]["derived"]["stats_meta"]["source_count"] == 1

    saved = client.post(
        "/composer-profiles/derive",
        json={
            "sources": [{"project_id": project.id}],
            "save_as": "My Cinematic Style",
        },
    )
    assert saved.status_code == 200
    body = saved.json()
    assert body["persisted"] is True
    profile_id = body["profile"]["id"]
    assert body["profile"]["name"] == "My Cinematic Style"
    assert len(body["profile"]["source_projects"]) == 1

    frag = client.post(
        f"/composer-profiles/{profile_id}/preview",
        json={"strength": "normal"},
    ).json()
    assert frag["applied_field_count"] > 0
    assert "SOFT composer-profile" in frag["soft_fragment"]

    exported = client.get(f"/composer-profiles/{profile_id}/export").json()
    assert exported["schema_version"] == "composer.profile.export.v1"

    imported = client.post(
        "/composer-profiles/import",
        json={"envelope": exported, "name": "Imported Cinematic"},
    )
    assert imported.status_code == 200
    assert imported.json()["profile"]["name"] == "Imported Cinematic"


def test_compare_profiles(client):
    a = client.post("/composer-profiles", json={"name": "Left"}).json()
    b = client.post(
        "/composer-profiles",
        json={"name": "Right", "explicit": {"midi_mean_band": "high"}},
    ).json()
    result = client.post(
        "/composer-profiles/compare",
        json={"left_id": a["id"], "right_id": b["id"]},
    ).json()
    assert result["equal"] is False
    assert any(d["path"] == "name" for d in result["diffs"])
