"""HTTP role matrix for comment, commit, share, and approve."""

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


def test_role_matrix(client):
    http, db_path = client
    composition = json.loads(FIXTURE.read_text(encoding="utf-8"))
    created = http.post("/projects", json={"name": "Matrix", "composition": composition})
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    state = created.json()
    edited = json.loads(json.dumps(composition))
    edited["tracks"][0]["events"][0]["pitch"] = "D4"
    commit = http.post(
        f"/projects/{project_id}/revisions",
        json={
            "branch_id": state["active_branch_id"],
            "expected_active_branch_id": state["active_branch_id"],
            "expected_working_version": state["working_version"],
            "expected_head_revision_id": state["current_revision_id"],
            "expected_source_fingerprint": state["working_fingerprint"],
            "composition": edited,
            "operation_type": "generate-apply",
            "ai": {"provider": "fake", "model": "tiny"},
        },
    )
    assert commit.status_code == 200, commit.text
    revision_id = commit.json()["current_revision_id"]
    opened = http.post(f"/projects/{project_id}/revisions/{revision_id}/reviews")
    assert opened.status_code == 201, opened.text
    review_id = opened.json()["id"]

    commenter = create_actor("Commenter", db_path=db_path)
    viewer = create_actor("Viewer", db_path=db_path)
    editor = create_actor("Editor", db_path=db_path)
    outsider = create_actor("Outsider", db_path=db_path)
    grant_member(project_id, commenter.id, "commenter", db_path=db_path)
    grant_member(project_id, viewer.id, "viewer", db_path=db_path)
    grant_member(project_id, editor.id, "editor", db_path=db_path)

    comment = http.post(
        f"/projects/{project_id}/comments",
        headers={"X-Mukit-Actor": commenter.id},
        json={"target_kind": "project", "body": "noted"},
    )
    assert comment.status_code == 201, comment.text
    denied_commit = http.post(
        f"/projects/{project_id}/revisions",
        headers={"X-Mukit-Actor": commenter.id},
        json={
            "branch_id": commit.json()["active_branch_id"],
            "expected_active_branch_id": commit.json()["active_branch_id"],
            "expected_working_version": commit.json()["working_version"],
            "expected_head_revision_id": commit.json()["current_revision_id"],
            "expected_source_fingerprint": commit.json()["working_fingerprint"],
            "composition": composition,
            "operation_type": "manual-checkpoint",
        },
    )
    assert denied_commit.status_code == 403
    assert denied_commit.json()["detail"]["code"] == "collaboration_role_denied"
    denied_approve = http.post(
        f"/projects/{project_id}/reviews/{review_id}/approve",
        headers={"X-Mukit-Actor": commenter.id},
        json={},
    )
    assert denied_approve.status_code == 403

    viewer_comment = http.post(
        f"/projects/{project_id}/comments",
        headers={"X-Mukit-Actor": viewer.id},
        json={"target_kind": "project", "body": "no"},
    )
    assert viewer_comment.status_code == 403
    assert viewer_comment.json()["detail"]["code"] == "collaboration_role_denied"

    editor_approve = http.post(
        f"/projects/{project_id}/reviews/{review_id}/approve",
        headers={"X-Mukit-Actor": editor.id},
        json={},
    )
    assert editor_approve.status_code == 403
    editor_share = http.post(
        f"/projects/{project_id}/members",
        headers={"X-Mukit-Actor": editor.id},
        json={"actor_id": outsider.id, "role": "viewer"},
    )
    assert editor_share.status_code == 403
    assert editor_share.json()["detail"]["code"] == "collaboration_role_denied"

    hidden = http.get(f"/projects/{project_id}", headers={"X-Mukit-Actor": outsider.id})
    assert hidden.status_code == 403
    assert hidden.json()["detail"]["code"] == "collaboration_not_member"
