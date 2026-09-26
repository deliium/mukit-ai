"""Flag-gated membership on durable project routes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import reset_database_initialization_cache
from app.db.connection import get_connection
from app.main import app
from app.services.collaboration_store import create_actor, grant_member


FIXTURE = (
    Path(__file__).resolve().parents[1] / "app" / "fixtures" / "composition_v2_expressive.json"
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.delenv("COLLABORATION_ENABLED", raising=False)
    reset_database_initialization_cache()
    with TestClient(app) as test_client:
        yield test_client, db_path


def _composition() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_flag_off_project_routes_ignore_the_actor_header(client):
    http, _db_path = client
    created = http.post(
        "/projects",
        headers={"X-Mukit-Actor": "someone-else"},
        json={"name": "Solo", "composition": _composition()},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["collaboration"] is None
    opened = http.get(f"/projects/{body['id']}")
    assert opened.status_code == 200
    assert opened.json()["collaboration"] is None


def test_non_member_commenter_and_stale_editor(client, monkeypatch):
    http, db_path = client
    monkeypatch.setenv("COLLABORATION_ENABLED", "1")
    created = http.post("/projects", json={"name": "Shared", "composition": _composition()})
    assert created.status_code == 201, created.text
    project = created.json()
    assert project["collaboration"]["role"] == "owner"
    project_id = project["id"]

    outsider = create_actor("Outsider", db_path=db_path)
    denied = http.get(f"/projects/{project_id}", headers={"X-Mukit-Actor": outsider.id})
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "collaboration_not_member"

    commenter = create_actor("Commenter", db_path=db_path)
    grant_member(project_id, commenter.id, "commenter", db_path=db_path)
    opened = http.get(f"/projects/{project_id}", headers={"X-Mukit-Actor": commenter.id})
    assert opened.status_code == 200
    commit_body = {
        "branch_id": opened.json()["active_branch_id"],
        "expected_active_branch_id": opened.json()["active_branch_id"],
        "expected_working_version": opened.json()["working_version"],
        "expected_head_revision_id": opened.json()["current_revision_id"],
        "expected_source_fingerprint": opened.json()["working_fingerprint"],
        "composition": _composition(),
        "operation_type": "manual-checkpoint",
    }
    comment_commit = http.post(
        f"/projects/{project_id}/revisions",
        headers={"X-Mukit-Actor": commenter.id},
        json=commit_body,
    )
    assert comment_commit.status_code == 403
    assert comment_commit.json()["detail"]["code"] == "collaboration_role_denied"

    editor = create_actor("Editor", db_path=db_path)
    grant_member(project_id, editor.id, "editor", db_path=db_path)
    first = http.post(
        f"/projects/{project_id}/revisions",
        headers={"X-Mukit-Actor": editor.id},
        json={
            **commit_body,
            "composition": {**_composition(), "tempo": 110},
            "operation_type": "manual-checkpoint",
            "ai": {"provider": "fake", "model": "fake-deterministic"},
        },
    )
    assert first.status_code == 200, first.text
    stale = http.post(
        f"/projects/{project_id}/revisions",
        headers={"X-Mukit-Actor": editor.id},
        json=commit_body,
    )
    assert stale.status_code == 409
    detail = stale.json()["detail"]
    assert detail["code"] == "project_revision_conflict"
    assert len(detail["expected_source_fingerprint_prefix"]) == 12
    assert len(detail["current_working_fingerprint_prefix"]) == 12

    with get_connection(db_path) as conn:
        row = conn.execute(
            """
            SELECT actor_id FROM project_revisions
            WHERE project_id = ? AND operation_type = 'manual-checkpoint'
            ORDER BY sequence DESC LIMIT 1
            """,
            (project_id,),
        ).fetchone()
    assert row["actor_id"] == editor.id

    listed = http.get("/projects", headers={"X-Mukit-Actor": outsider.id})
    assert listed.status_code == 200
    assert listed.json()["projects"] == []
