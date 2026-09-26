"""Revision review does not rewrite history or restore the score."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import reset_database_initialization_cache
from app.main import app
from app.services.collaboration_store import create_actor, grant_member

FIXTURE = (
    Path(__file__).resolve().parents[1] / "app" / "fixtures" / "composition_v2_expressive.json"
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.setenv("COLLABORATION_ENABLED", "1")
    reset_database_initialization_cache()
    with TestClient(app) as test_client:
        yield test_client, db_path


def test_owner_approves_ai_revision_without_moving_head(client):
    http, db_path = client
    composition = json.loads(FIXTURE.read_text(encoding="utf-8"))
    created = http.post("/projects", json={"name": "Review", "composition": composition})
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    create_revision = created.json()["current_revision_id"]
    editor = create_actor("Editor", db_path=db_path)
    grant_member(project_id, editor.id, "editor", db_path=db_path)

    blocked = http.post(
        f"/projects/{project_id}/revisions/{create_revision}/reviews",
        headers={"X-Mukit-Actor": editor.id},
    )
    assert blocked.status_code == 422
    assert blocked.json()["detail"]["code"] == "collaboration_review_not_allowed"

    edited = json.loads(json.dumps(composition))
    edited["tracks"][0]["events"][0]["pitch"] = "D4"
    detail = created.json()
    commit = http.post(
        f"/projects/{project_id}/revisions",
        headers={"X-Mukit-Actor": editor.id},
        json={
            "branch_id": detail["active_branch_id"],
            "expected_active_branch_id": detail["active_branch_id"],
            "expected_working_version": detail["working_version"],
            "expected_head_revision_id": detail["current_revision_id"],
            "expected_source_fingerprint": detail["working_fingerprint"],
            "composition": edited,
            "operation_type": "generate-apply",
            "ai": {"provider": "fake", "model": "tiny"},
        },
    )
    assert commit.status_code == 200, commit.text
    assert commit.json()["revision_created"] is True
    ai_revision = commit.json()["current_revision_id"]
    head_before = ai_revision

    opened = http.post(
        f"/projects/{project_id}/revisions/{ai_revision}/reviews",
        headers={"X-Mukit-Actor": editor.id},
    )
    assert opened.status_code == 201, opened.text
    assert opened.json()["origin"] == "ai"
    assert opened.json()["status"] == "open"
    review_id = opened.json()["id"]

    denied = http.post(
        f"/projects/{project_id}/reviews/{review_id}/approve",
        headers={"X-Mukit-Actor": editor.id},
        json={},
    )
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "collaboration_role_denied"

    approved = http.post(
        f"/projects/{project_id}/reviews/{review_id}/approve",
        json={"note": "keep this"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert "note" not in approved.json()

    again = http.post(f"/projects/{project_id}/reviews/{review_id}/reject", json={})
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "collaboration_review_decided"

    loaded = http.get(f"/projects/{project_id}")
    assert loaded.json()["current_revision_id"] == head_before
    assert loaded.json()["collaboration"]["accepted_revision_id"] == ai_revision
    listed = http.get(f"/projects/{project_id}/revisions")
    ids = [row["id"] for row in listed.json()["revisions"]]
    assert ai_revision in ids
    assert create_revision in ids
