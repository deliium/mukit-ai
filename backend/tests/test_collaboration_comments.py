"""Anchored comments on the expressive fixture."""

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
    monkeypatch.setenv("COLLABORATION_ENABLED", "1")
    reset_database_initialization_cache()
    with TestClient(app) as test_client:
        yield test_client, db_path


def test_comment_kinds_and_role_gate(client):
    http, db_path = client
    composition = json.loads(FIXTURE.read_text(encoding="utf-8"))
    created = http.post("/projects", json={"name": "Notes", "composition": composition})
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    revision_id = created.json()["current_revision_id"]

    viewer = create_actor("Viewer", db_path=db_path)
    grant_member(project_id, viewer.id, "viewer", db_path=db_path)
    commenter = create_actor("Commenter", db_path=db_path)
    grant_member(project_id, commenter.id, "commenter", db_path=db_path)

    denied = http.post(
        f"/projects/{project_id}/comments",
        headers={"X-Mukit-Actor": viewer.id},
        json={"target_kind": "project", "body": "no"},
    )
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "collaboration_role_denied"

    missing = http.post(
        f"/projects/{project_id}/comments",
        headers={"X-Mukit-Actor": commenter.id},
        json={
            "target_kind": "section",
            "section_id": "missing-section",
            "revision_id": revision_id,
            "body": "where",
        },
    )
    assert missing.status_code == 422
    assert missing.json()["detail"]["code"] == "comment_anchor_missing"

    section = http.post(
        f"/projects/{project_id}/comments",
        headers={"X-Mukit-Actor": commenter.id},
        json={
            "target_kind": "section",
            "section_id": "section-1",
            "revision_id": revision_id,
            "body": "opening",
        },
    )
    assert section.status_code == 201, section.text
    bars = http.post(
        f"/projects/{project_id}/comments",
        headers={"X-Mukit-Actor": commenter.id},
        json={"target_kind": "bar_range", "start_bar": 1, "end_bar": 2, "body": "first phrase"},
    )
    assert bars.status_code == 201, bars.text
    track = http.post(
        f"/projects/{project_id}/comments",
        headers={"X-Mukit-Actor": commenter.id},
        json={"target_kind": "track", "track_id": "melody-1", "body": "melody"},
    )
    assert track.status_code == 201, track.text
    revision = http.post(
        f"/projects/{project_id}/comments",
        headers={"X-Mukit-Actor": commenter.id},
        json={"target_kind": "revision", "revision_id": revision_id, "body": "root"},
    )
    assert revision.status_code == 201, revision.text
    project = http.post(
        f"/projects/{project_id}/comments",
        headers={"X-Mukit-Actor": commenter.id},
        json={"target_kind": "project", "body": "overall"},
    )
    assert project.status_code == 201, project.text

    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO neural_audio_renders (
                id, project_id, source_fingerprint, status, model_id, adapter_kind,
                fidelity_class, instructions, created_at
            ) VALUES ('render-1', ?, 'fp', 'complete', 'fake', 'text_prompt',
                      'deterministic', '', '2026-09-26T00:00:00Z')
            """,
            (project_id,),
        )
    rendered = http.post(
        f"/projects/{project_id}/comments",
        headers={"X-Mukit-Actor": commenter.id},
        json={"target_kind": "render", "render_id": "render-1", "body": "mix"},
    )
    assert rendered.status_code == 201, rendered.text

    listed = http.get(f"/projects/{project_id}/comments", headers={"X-Mukit-Actor": viewer.id})
    assert listed.status_code == 200
    kinds = [row["target_kind"] for row in listed.json()]
    assert set(kinds) == {"section", "bar_range", "track", "revision", "project", "render"}
    assert "composition" not in listed.text
    assert listed.json()[0]["body"]
