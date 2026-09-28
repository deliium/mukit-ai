"""HTTP tests for adaptive score CRUD, binding, and collaboration."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import reset_database_initialization_cache
from app.main import app
from app.services.collaboration_store import create_actor, grant_member
from tests.test_adaptive_score_schema import adventure_score
from tests.test_composition_schema import valid_composition

FIXTURE = (
    Path(__file__).resolve().parents[1] / "app" / "fixtures" / "composition_v2_expressive.json"
)
_SCORE_ID = re.compile(r"^ascore_[0-9a-f]{16}$")


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, collab: bool):
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    if collab:
        monkeypatch.setenv("COLLABORATION_ENABLED", "1")
    else:
        monkeypatch.delenv("COLLABORATION_ENABLED", raising=False)
    reset_database_initialization_cache()
    client = TestClient(app)
    return client, db_path


def _composition_text(db_path: Path, project_id: str) -> str:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT composition_json FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()
    assert row is not None and row[0] is not None
    return str(row[0])


def _section_score(section_a: str, section_b: str) -> dict:
    payload = adventure_score()
    payload["states"] = [
        {
            "id": "state-exploration",
            "name": "Exploration",
            "intensity": 0.2,
            "material": {"kind": "section", "section_id": section_a},
            "transition_ids": ["to-combat"],
        },
        {
            "id": "state-combat",
            "name": "Combat",
            "intensity": 0.8,
            "material": {"kind": "section", "section_id": section_b},
            "transition_ids": [],
        },
    ]
    payload["initial_state_id"] = "state-exploration"
    payload["default_state_id"] = "state-exploration"
    payload["variants"] = []
    payload["layers"] = []
    payload["stingers"] = []
    payload["transitions"] = [
        {
            "id": "to-combat",
            "from_state_id": "state-exploration",
            "to_state_id": "state-combat",
            "quantization": "bar",
            "conditions": [{"kind": "manual"}],
        }
    ]
    return payload


def test_api_section_refs_do_not_rewrite_v2_or_v1(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, db_path = _client(tmp_path, monkeypatch, collab=False)
    composition = json.loads(FIXTURE.read_text(encoding="utf-8"))
    created = client.post("/projects", json={"name": "Adaptive", "composition": composition})
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    before = _composition_text(db_path, project_id)
    payload = _section_score("section-1", "section-2")
    caplog.set_level(logging.INFO)
    posted = client.post(
        f"/projects/{project_id}/adaptive-scores",
        json={"is_default": True, "score": payload},
    )
    assert posted.status_code == 201, posted.text
    body = posted.json()
    assert _SCORE_ID.fullmatch(body["score"]["id"])
    assert body["score"]["id"] == body["score"]["id"]
    assert body["document_revision"] == 1
    assert "events" not in json.dumps(body["score"])
    assert "expected_document_revision" not in body["score"]
    assert _composition_text(db_path, project_id) == before
    listed = client.get(f"/projects/{project_id}/adaptive-scores")
    assert listed.status_code == 200, listed.text
    assert "body_json" not in listed.text
    assert listed.json()["scores"][0]["state_count"] == 2

    updated_score = body["score"]
    updated_score["name"] = "Main cue revised"
    put = client.put(
        f"/projects/{project_id}/adaptive-scores/{body['score']['id']}",
        json={"expected_document_revision": 1, "score": updated_score},
    )
    assert put.status_code == 200, put.text
    assert put.json()["document_revision"] == 2
    assert _composition_text(db_path, project_id) == before

    missing = _section_score("section-missing", "section-2")
    missing["id"] = body["score"]["id"]
    missing["project_id"] = project_id
    rejected = client.put(
        f"/projects/{project_id}/adaptive-scores/{body['score']['id']}",
        json={"expected_document_revision": 2, "score": missing},
    )
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["detail"]["code"] == "material_target_missing"
    assert _composition_text(db_path, project_id) == before

    conflict = client.put(
        f"/projects/{project_id}/adaptive-scores/{body['score']['id']}",
        json={"expected_document_revision": 1, "score": updated_score},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "adaptive_score_conflict"

    second = client.post(
        f"/projects/{project_id}/adaptive-scores",
        json={"is_default": True, "score": {**payload, "name": "Menu cue"}},
    )
    assert second.status_code == 201, second.text
    first = client.get(
        f"/projects/{project_id}/adaptive-scores/{body['score']['id']}"
    )
    assert first.status_code == 200
    assert first.json()["is_default"] is False
    assert second.json()["is_default"] is True

    raw_v1 = json.dumps(valid_composition(), ensure_ascii=False, separators=(",", ":"))
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE projects SET composition_json = ? WHERE id = ?",
            (raw_v1, project_id),
        )
        conn.commit()
    v1_score = {
        "schema_version": "adaptive.score.v1",
        "name": "Legacy bed",
        "initial_state_id": "state-exploration",
        "default_state_id": "state-exploration",
        "states": [
            {
                "id": "state-exploration",
                "name": "Exploration",
                "intensity": 0.2,
                "material": {"kind": "bar_range", "start_bar": 1, "end_bar": 2},
                "transition_ids": [],
            }
        ],
    }
    v1_post = client.post(
        f"/projects/{project_id}/adaptive-scores",
        json={"score": v1_score},
    )
    assert v1_post.status_code == 201, v1_post.text
    assert _composition_text(db_path, project_id) == raw_v1
    log_blob = " ".join(record.getMessage() for record in caplog.records)
    assert raw_v1 not in log_blob
    assert "C4" not in log_blob
    client.close()


def test_collaboration_viewer_denied_editor_allowed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db_path = _client(tmp_path, monkeypatch, collab=True)
    composition = json.loads(FIXTURE.read_text(encoding="utf-8"))
    created = client.post("/projects", json={"name": "Roles", "composition": composition})
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    viewer = create_actor("Viewer", db_path=db_path)
    editor = create_actor("Editor", db_path=db_path)
    grant_member(project_id, viewer.id, "viewer", db_path=db_path)
    grant_member(project_id, editor.id, "editor", db_path=db_path)
    payload = _section_score("section-1", "section-2")
    denied = client.post(
        f"/projects/{project_id}/adaptive-scores",
        headers={"X-Mukit-Actor": viewer.id},
        json={"score": payload},
    )
    assert denied.status_code == 403
    allowed = client.post(
        f"/projects/{project_id}/adaptive-scores",
        headers={"X-Mukit-Actor": editor.id},
        json={"score": payload},
    )
    assert allowed.status_code == 201, allowed.text
    client.close()
