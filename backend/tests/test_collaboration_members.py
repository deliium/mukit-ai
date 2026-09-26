"""Share routes: actors, roles, and the collaboration flag."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import reset_database_initialization_cache
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.setenv("COLLABORATION_ENABLED", "1")
    reset_database_initialization_cache()
    with TestClient(app) as test_client:
        yield test_client


def test_status_off_hides_actor_routes(tmp_path, monkeypatch):
    monkeypatch.setenv("PROJECT_DB_PATH", str(tmp_path / "off.db"))
    monkeypatch.delenv("COLLABORATION_ENABLED", raising=False)
    reset_database_initialization_cache()
    with TestClient(app) as http:
        status = http.get("/collaboration/status")
        assert status.status_code == 200
        assert status.json() == {"enabled": False, "actor_id": None}
        missing = http.post("/collaboration/actors", json={"display_name": "Ada"})
        assert missing.status_code == 404
        assert missing.json()["detail"]["code"] == "collaboration_disabled"


def test_owner_shares_changes_and_revokes(client):
    http = client
    status = http.get("/collaboration/status")
    assert status.status_code == 200
    assert status.json()["enabled"] is True
    assert status.json()["actor_id"] == "local"

    created = http.post("/projects", json={"name": "Shared"})
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]

    unknown = http.post(
        f"/projects/{project_id}/members",
        json={"actor_id": "missing-person", "role": "editor"},
    )
    assert unknown.status_code == 401
    assert unknown.json()["detail"]["code"] == "collaboration_actor_unknown"

    secret = http.post(
        "/collaboration/actors",
        json={"display_name": "sk-abcdefghijklmnopqrstuvwxyz"},
    )
    assert secret.status_code == 422
    assert secret.json()["detail"]["code"] == "persistence_secret_rejected"

    ada = http.post("/collaboration/actors", json={"display_name": "Ada"})
    assert ada.status_code == 201, ada.text
    actor_id = ada.json()["id"]
    assert "Ada" not in str(ada.json().keys()) or ada.json()["display_name"] == "Ada"

    owner_grant = http.post(
        f"/projects/{project_id}/members",
        json={"actor_id": actor_id, "role": "owner"},
    )
    assert owner_grant.status_code == 422

    granted = http.post(
        f"/projects/{project_id}/members",
        json={"actor_id": actor_id, "role": "editor"},
    )
    assert granted.status_code == 201, granted.text
    duplicate = http.post(
        f"/projects/{project_id}/members",
        json={"actor_id": actor_id, "role": "viewer"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "collaboration_member_exists"

    patched = http.patch(
        f"/projects/{project_id}/members/{actor_id}",
        json={"role": "commenter"},
    )
    assert patched.status_code == 200
    assert patched.json()["role"] == "commenter"
    viewer = http.patch(
        f"/projects/{project_id}/members/{actor_id}",
        json={"role": "viewer"},
    )
    assert viewer.status_code == 200

    owner_patch = http.patch(
        f"/projects/{project_id}/members/local",
        json={"role": "editor"},
    )
    assert owner_patch.status_code == 422
    assert owner_patch.json()["detail"]["code"] == "collaboration_owner_required"
    owner_delete = http.delete(f"/projects/{project_id}/members/local")
    assert owner_delete.status_code == 422
    assert owner_delete.json()["detail"]["code"] == "collaboration_owner_required"

    removed = http.delete(f"/projects/{project_id}/members/{actor_id}")
    assert removed.status_code == 204
    listed = http.get(f"/projects/{project_id}/members")
    assert listed.status_code == 200
    assert [row["actor_id"] for row in listed.json()] == ["local"]
