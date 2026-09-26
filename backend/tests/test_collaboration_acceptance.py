"""Two collaborators review, comment, branch a stale save, and approve."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import reset_database_initialization_cache
from app.main import app
from app.services.collaboration_store import create_actor

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


def _pitch(composition, pitch: str) -> dict:
    edited = json.loads(json.dumps(composition))
    edited["tracks"][0]["events"][0]["pitch"] = pitch
    return edited


def _commit(http, project_id, state, composition, operation, *, headers=None, ai=None):
    payload = {
        "branch_id": state["active_branch_id"],
        "expected_active_branch_id": state["active_branch_id"],
        "expected_working_version": state["working_version"],
        "expected_head_revision_id": state["current_revision_id"],
        "expected_source_fingerprint": state["working_fingerprint"],
        "composition": composition,
        "operation_type": operation,
    }
    if ai is not None:
        payload["ai"] = ai
    return http.post(
        f"/projects/{project_id}/revisions",
        headers=headers or {},
        json=payload,
    )


def test_two_collaborators_review_comment_branch_and_approve(client):
    http, db_path = client
    composition = json.loads(FIXTURE.read_text(encoding="utf-8"))
    created = http.post("/projects", json={"name": "Together", "composition": composition})
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    state = created.json()

    generated = _commit(
        http,
        project_id,
        state,
        _pitch(composition, "D4"),
        "generate-apply",
        ai={"provider": "fake", "model": "tiny"},
    )
    assert generated.status_code == 200, generated.text
    assert generated.json()["revision_created"] is True
    ai_revision_id = generated.json()["current_revision_id"]
    shared = generated.json()
    shared_fingerprint = shared["working_fingerprint"]

    editor = create_actor("Editor B", db_path=db_path)
    granted = http.post(
        f"/projects/{project_id}/members",
        json={"actor_id": editor.id, "role": "editor"},
    )
    assert granted.status_code == 201, granted.text
    editor_headers = {"X-Mukit-Actor": editor.id}

    comment = http.post(
        f"/projects/{project_id}/comments",
        headers=editor_headers,
        json={
            "target_kind": "section",
            "section_id": "section-1",
            "revision_id": ai_revision_id,
            "body": "listen to the opening",
        },
    )
    assert comment.status_code == 201, comment.text

    winner = _commit(
        http,
        project_id,
        shared,
        _pitch(composition, "E4"),
        "manual-checkpoint",
    )
    assert winner.status_code == 200, winner.text
    winning_revision_id = winner.json()["current_revision_id"]
    assert winner.json()["working_fingerprint"] != shared_fingerprint

    loser = _commit(
        http,
        project_id,
        shared,
        _pitch(composition, "F4"),
        "manual-checkpoint",
        headers=editor_headers,
    )
    assert loser.status_code == 409
    assert loser.json()["detail"]["code"] == "project_revision_conflict"
    prefixes = json.dumps(loser.json()["detail"])
    assert shared_fingerprint not in prefixes

    reloaded = http.get(f"/projects/{project_id}", headers=editor_headers)
    assert reloaded.status_code == 200
    fresh = reloaded.json()
    assert fresh["working_fingerprint"] == winner.json()["working_fingerprint"]
    original_branch_id = shared["active_branch_id"]

    branched = http.post(
        f"/projects/{project_id}/branches/apply-as-branch",
        headers=editor_headers,
        json={
            "name": "Editor draft",
            "source_branch_id": fresh["active_branch_id"],
            "expected_active_branch_id": fresh["active_branch_id"],
            "expected_working_version": fresh["working_version"],
            "expected_head_revision_id": fresh["current_revision_id"],
            "expected_source_fingerprint": fresh["working_fingerprint"],
            "composition": _pitch(composition, "F4"),
            "operation_type": "manual-checkpoint",
        },
    )
    assert branched.status_code == 200, branched.text
    branch_revision_id = branched.json()["current_revision_id"]
    assert branch_revision_id not in {ai_revision_id, winning_revision_id}

    branches = http.get(f"/projects/{project_id}/branches")
    assert branches.status_code == 200
    rows = {row["id"]: row for row in branches.json()["branches"]}
    assert rows[original_branch_id]["head_revision_id"] == winning_revision_id
    assert branched.json()["active_branch_id"] != original_branch_id
    assert rows[branched.json()["active_branch_id"]]["is_active"] is True

    listed = http.get(f"/projects/{project_id}/revisions")
    revision_ids = [row["id"] for row in listed.json()["revisions"]]
    assert ai_revision_id in revision_ids
    assert winning_revision_id in revision_ids
    assert branch_revision_id in revision_ids

    ai_review = http.post(f"/projects/{project_id}/revisions/{ai_revision_id}/reviews")
    assert ai_review.status_code == 201, ai_review.text
    assert ai_review.json()["origin"] == "ai"
    branch_review = http.post(f"/projects/{project_id}/revisions/{branch_revision_id}/reviews")
    assert branch_review.status_code == 201, branch_review.text
    approved = http.post(
        f"/projects/{project_id}/reviews/{branch_review.json()['id']}/approve",
        json={},
    )
    assert approved.status_code == 200, approved.text

    final = http.get(f"/projects/{project_id}")
    assert final.json()["collaboration"]["accepted_revision_id"] == branch_revision_id
    remaining = [row["id"] for row in http.get(f"/projects/{project_id}/revisions").json()["revisions"]]
    assert ai_revision_id in remaining
    assert winning_revision_id in remaining
    assert branch_revision_id in remaining

    activity = http.get(f"/projects/{project_id}/activity")
    kinds = {row["kind"] for row in activity.json()}
    assert {"ai_edit", "user_edit", "comment", "approval"} <= kinds
    assert "listen to the opening" not in activity.text
