"""Activity rows follow the flag and never store comment bodies."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import reset_database_initialization_cache
from app.db.connection import get_connection
from app.main import app
from app.project_history_schemas import DurableCommitRequest
from app.services.collaboration_store import create_actor, grant_member
from app.services.neural_audio_render_store import update_job_status
from app.services.project_history import commit_revision

FIXTURE = (
    Path(__file__).resolve().parents[1] / "app" / "fixtures" / "composition_v2_expressive.json"
)


def _client(tmp_path, monkeypatch, enabled: str):
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.setenv("COLLABORATION_ENABLED", enabled)
    reset_database_initialization_cache()
    return db_path


@pytest.fixture
def enabled_client(tmp_path, monkeypatch):
    db_path = _client(tmp_path, monkeypatch, "1")
    with TestClient(app) as test_client:
        yield test_client, db_path


@pytest.fixture
def disabled_client(tmp_path, monkeypatch):
    db_path = _client(tmp_path, monkeypatch, "0")
    with TestClient(app) as test_client:
        yield test_client, db_path


def _commit(http, project_id, project, composition, operation, *, ai=None, headers=None):
    edited = json.loads(json.dumps(composition))
    edited["tracks"][0]["events"][0]["pitch"] = "D4" if operation == "manual-checkpoint" else "F4"
    payload = {
        "branch_id": project["active_branch_id"],
        "expected_active_branch_id": project["active_branch_id"],
        "expected_working_version": project["working_version"],
        "expected_head_revision_id": project["current_revision_id"],
        "expected_source_fingerprint": project["working_fingerprint"],
        "composition": edited,
        "operation_type": operation,
    }
    if ai is not None:
        payload["ai"] = ai
    response = http.post(
        f"/projects/{project_id}/revisions",
        headers=headers or {},
        json=payload,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    body["id"] = project_id
    return body


def test_flag_on_records_kinds(enabled_client):
    http, db_path = enabled_client
    composition = json.loads(FIXTURE.read_text(encoding="utf-8"))
    created = http.post("/projects", json={"name": "Feed", "composition": composition})
    assert created.status_code == 201, created.text
    project = created.json()
    project_id = project["id"]
    editor = create_actor("Editor", db_path=db_path)
    grant_member(project_id, editor.id, "editor", db_path=db_path)
    headers = {"X-Mukit-Actor": editor.id}

    human = _commit(http, project_id, project, composition, "manual-checkpoint", headers=headers)
    generated = _commit(
        http,
        project_id,
        human,
        composition,
        "generate-apply",
        ai={"provider": "fake", "model": "tiny"},
        headers=headers,
    )
    comment = http.post(
        f"/projects/{project_id}/comments",
        headers=headers,
        json={
            "target_kind": "section",
            "section_id": "section-1",
            "revision_id": generated["current_revision_id"],
            "body": "do not log this sentence",
        },
    )
    assert comment.status_code == 201, comment.text
    review = http.post(
        f"/projects/{project_id}/revisions/{generated['current_revision_id']}/reviews",
        headers=headers,
    )
    assert review.status_code == 201, review.text
    approved = http.post(
        f"/projects/{project_id}/reviews/{review.json()['id']}/approve",
        json={},
    )
    assert approved.status_code == 200, approved.text

    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO neural_audio_renders (
                id, project_id, source_fingerprint, status, model_id, adapter_kind,
                fidelity_class, instructions, created_at
            ) VALUES ('render-act', ?, 'fp', 'queued', 'fake-model', 'text_prompt',
                      'deterministic', '', '2026-09-26T00:00:00Z')
            """,
            (project_id,),
        )
        update_job_status(conn, "render-act", status="complete")
    omitted = commit_revision(
            project_id,
            DurableCommitRequest(
                branch_id=generated["active_branch_id"],
                expected_active_branch_id=generated["active_branch_id"],
                expected_working_version=generated["working_version"],
                expected_head_revision_id=generated["current_revision_id"],
                expected_source_fingerprint=generated["working_fingerprint"],
                composition=composition,
                operation_type="manual-checkpoint",
            ),
            actor_id=None,
            db_path=db_path,
        )
    assert omitted.revision_created is True

    listed = http.get(f"/projects/{project_id}/activity")
    assert listed.status_code == 200, listed.text
    kinds = {row["kind"] for row in listed.json()}
    assert {"user_edit", "ai_edit", "comment", "approval", "render"} <= kinds
    ai_row = next(row for row in listed.json() if row["kind"] == "ai_edit")
    assert ai_row["ai_provider"] == "fake"
    assert ai_row["ai_model"] == "tiny"
    comment_row = next(row for row in listed.json() if row["kind"] == "comment")
    assert comment_row["summary"] == "comment on section"
    assert "do not log this sentence" not in listed.text
    null_actor = [
        row
        for row in listed.json()
        if row["kind"] == "user_edit" and row["actor_id"] is None
    ]
    assert null_actor
    render_row = next(row for row in listed.json() if row["kind"] == "render")
    assert render_row["ai_model"] == "fake-model"
    assert render_row["actor_id"] is None


def test_flag_off_writes_no_activity(disabled_client):
    http, db_path = disabled_client
    composition = json.loads(FIXTURE.read_text(encoding="utf-8"))
    created = http.post("/projects", json={"name": "Quiet", "composition": composition})
    assert created.status_code == 201, created.text
    project = created.json()
    _commit(http, project["id"], project, composition, "manual-checkpoint")
    latest = http.get(f"/projects/{project['id']}").json()
    _commit(
        http,
        project["id"],
        latest,
        composition,
        "generate-apply",
        ai={"provider": "fake", "model": "tiny"},
    )
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO neural_audio_renders (
                id, project_id, source_fingerprint, status, model_id, adapter_kind,
                fidelity_class, instructions, created_at
            ) VALUES ('render-off', ?, 'fp', 'queued', 'fake-model', 'text_prompt',
                      'deterministic', '', '2026-09-26T00:00:00Z')
            """,
            (project["id"],),
        )
        update_job_status(conn, "render-off", status="complete")
        count = conn.execute("SELECT COUNT(*) AS n FROM project_activity").fetchone()["n"]
    assert count == 0
    missing = http.get(f"/projects/{project['id']}/activity")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "collaboration_disabled"
