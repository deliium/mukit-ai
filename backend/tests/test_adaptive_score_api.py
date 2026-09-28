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
from app.services.adaptive_score_transition_pending import reset_default_registry
from tests.test_adaptive_score_schema import adventure_score
from tests.test_composition_schema import valid_composition
from tests.test_composition_v2_schema import minimal_v2

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
    reset_default_registry()
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


def _empty_score() -> dict:
    return {"schema_version": "adaptive.score.v1", "name": "Exploration cue"}


def _command(client: TestClient, project_id: str, score_id: str, revision: int, op: str, payload: dict, **kwargs):
    return client.post(
        f"/projects/{project_id}/adaptive-scores/{score_id}/commands",
        json={"expected_document_revision": revision, "op": op, "payload": payload},
        **kwargs,
    )


def _document_revision(db_path: Path, score_id: str) -> int:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT document_revision FROM adaptive_scores WHERE id = ?",
            (score_id,),
        ).fetchone()
    assert row is not None
    return int(row[0])


def test_commands_build_exploration_combat_victory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db_path = _client(tmp_path, monkeypatch, collab=False)
    composition = json.loads(FIXTURE.read_text(encoding="utf-8"))
    created = client.post("/projects", json={"name": "Adaptive cues", "composition": composition})
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    before = _composition_text(db_path, project_id)
    posted = client.post(
        f"/projects/{project_id}/adaptive-scores",
        json={"is_default": True, "score": _empty_score()},
    )
    assert posted.status_code == 201, posted.text
    score_id = posted.json()["score"]["id"]
    revision = 1
    for name, state_id in (
        ("Exploration", "state-exploration"),
        ("Combat", "state-combat"),
        ("Victory", "state-victory"),
    ):
        response = _command(
            client,
            project_id,
            score_id,
            revision,
            "create_state",
            {"name": name, "id": state_id},
        )
        assert response.status_code == 200, response.text
        revision = response.json()["document_revision"]
    for transition_id, source, destination in (
        ("to-combat", "state-exploration", "state-combat"),
        ("to-victory", "state-combat", "state-victory"),
    ):
        response = _command(
            client,
            project_id,
            score_id,
            revision,
            "create_transition",
            {
                "id": transition_id,
                "from_state_id": source,
                "to_state_id": destination,
                "quantization": "bar",
                "conditions": [{"kind": "manual"}],
            },
        )
        assert response.status_code == 200, response.text
        revision = response.json()["document_revision"]
    body = response.json()
    names = [state["name"] for state in body["score"]["states"]]
    assert names == ["Exploration", "Combat", "Victory"]
    assert body["score"]["initial_state_id"] == "state-exploration"
    assert body["score"]["default_state_id"] == "state-exploration"
    validated = client.post(f"/projects/{project_id}/adaptive-scores/{score_id}/validate")
    assert validated.status_code == 200, validated.text
    assert validated.json()["error_count"] == 0
    assert validated.json()["ready"] is True
    assert _composition_text(db_path, project_id) == before
    conflict = _command(
        client,
        project_id,
        score_id,
        1,
        "create_state",
        {"name": "Extra"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "adaptive_score_conflict"
    assert _document_revision(db_path, score_id) == revision
    assert len(client.get(f"/projects/{project_id}/adaptive-scores/{score_id}").json()["score"]["states"]) == 3
    client.close()


def test_bad_loop_command_returns_findings_and_does_not_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db_path = _client(tmp_path, monkeypatch, collab=False)
    composition = json.loads(FIXTURE.read_text(encoding="utf-8"))
    created = client.post("/projects", json={"name": "Loop", "composition": composition})
    project_id = created.json()["id"]
    before = _composition_text(db_path, project_id)
    posted = client.post(
        f"/projects/{project_id}/adaptive-scores",
        json={"score": _empty_score()},
    )
    score_id = posted.json()["score"]["id"]
    created_state = _command(
        client,
        project_id,
        score_id,
        1,
        "create_state",
        {"name": "Combat", "id": "state-combat"},
    )
    assert created_state.status_code == 200, created_state.text
    rejected = _command(
        client,
        project_id,
        score_id,
        2,
        "assign_loop",
        {"state_id": "state-combat", "enabled": True, "start_bar": 4, "end_bar": 2},
    )
    assert rejected.status_code == 422, rejected.text
    detail = rejected.json()["detail"]
    assert detail["code"] == "loop_bounds"
    findings = detail["details"]["findings"]
    assert findings
    assert findings[0]["code"] == "loop_bounds"
    assert "state-combat" in findings[0]["message"]
    assert "2" in findings[0]["message"]
    assert _document_revision(db_path, score_id) == 2
    assert _composition_text(db_path, project_id) == before
    client.close()


def test_command_viewer_denied(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db_path = _client(tmp_path, monkeypatch, collab=True)
    composition = json.loads(FIXTURE.read_text(encoding="utf-8"))
    created = client.post("/projects", json={"name": "Roles", "composition": composition})
    project_id = created.json()["id"]
    editor = create_actor("Editor", db_path=db_path)
    viewer = create_actor("Viewer", db_path=db_path)
    grant_member(project_id, editor.id, "editor", db_path=db_path)
    grant_member(project_id, viewer.id, "viewer", db_path=db_path)
    posted = client.post(
        f"/projects/{project_id}/adaptive-scores",
        headers={"X-Mukit-Actor": editor.id},
        json={"score": _empty_score()},
    )
    assert posted.status_code == 201, posted.text
    score_id = posted.json()["score"]["id"]
    denied = _command(
        client,
        project_id,
        score_id,
        1,
        "create_state",
        {"name": "Exploration"},
        headers={"X-Mukit-Actor": viewer.id},
    )
    assert denied.status_code == 403
    client.close()


def _clock_composition() -> dict:
    return minimal_v2(
        tempo=120,
        bar_count=4,
        duration_ticks=7680,
        sections=[
            {
                "id": "section-explore",
                "type": "intro",
                "start_bar": 1,
                "bar_count": 4,
                "start_tick": 0,
                "duration_ticks": 7680,
            }
        ],
    )


def _bar_pair() -> dict:
    return {
        "schema_version": "adaptive.score.v1",
        "name": "Main cue",
        "initial_state_id": "state-exploration",
        "default_state_id": "state-exploration",
        "states": [
            {
                "id": "state-exploration",
                "name": "Exploration",
                "intensity": 0.2,
                "material": {"kind": "bar_range", "start_bar": 1, "end_bar": 4},
                "transition_ids": ["to-combat"],
            },
            {
                "id": "state-combat",
                "name": "Combat",
                "intensity": 0.8,
                "material": {"kind": "bar_range", "start_bar": 1, "end_bar": 2},
                "transition_ids": [],
            },
        ],
        "transitions": [
            {
                "id": "to-combat",
                "from_state_id": "state-exploration",
                "to_state_id": "state-combat",
                "quantization": "bar",
                "conditions": [{"kind": "manual"}],
            }
        ],
    }


def _schedule_body(revision: int, position_tick: int = 2400) -> dict:
    return {
        "expected_document_revision": revision,
        "from_state_id": "state-exploration",
        "to_state_id": "state-combat",
        "transition_id": None,
        "position_tick": position_tick,
        "runtime": {"intensity": 0, "flags": {}, "bars_in_state": 0},
    }


def test_bar_schedule_replaces_and_does_not_write_the_score(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db_path = _client(tmp_path, monkeypatch, collab=False)
    created = client.post("/projects", json={"name": "Clock", "composition": _clock_composition()})
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    before = _composition_text(db_path, project_id)
    posted = client.post(
        f"/projects/{project_id}/adaptive-scores",
        json={"score": _bar_pair()},
    )
    assert posted.status_code == 201, posted.text
    score_id = posted.json()["score"]["id"]
    revision = posted.json()["document_revision"]
    path = f"/projects/{project_id}/adaptive-scores/{score_id}/transition-requests"
    first = client.post(path, json=_schedule_body(revision))
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["schema_version"] == "adaptive.transition.schedule.v1"
    assert body["boundary_tick"] == 3840
    assert body["latency_ticks"] == 1440
    assert body["latency_ms"] == 1500
    assert body["quantization"] == "bar"
    second = client.post(path, json=_schedule_body(revision))
    assert second.status_code == 200, second.text
    assert second.json()["replaced_request_id"] == body["request_id"]
    assert second.json()["boundary_tick"] == 3840
    mismatch = client.post(path, json=_schedule_body(revision + 9))
    assert mismatch.status_code == 409
    assert mismatch.json()["detail"]["code"] == "adaptive_score_conflict"
    current = client.get(f"{path}/current")
    assert current.status_code == 200
    assert current.json()["request_id"] == second.json()["request_id"]
    stored = client.get(f"/projects/{project_id}/adaptive-scores/{score_id}")
    assert stored.json()["document_revision"] == revision
    assert _composition_text(db_path, project_id) == before
    cancelled = client.delete(f"{path}/{second.json()['request_id']}")
    assert cancelled.status_code == 204
    empty = client.get(f"{path}/current")
    assert empty.status_code == 204
    assert empty.content == b""
    client.close()


def test_viewer_can_schedule_and_asset_only_uses_the_working_clock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db_path = _client(tmp_path, monkeypatch, collab=True)
    created = client.post("/projects", json={"name": "Roles", "composition": _clock_composition()})
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    editor = create_actor("Editor", db_path=db_path)
    viewer = create_actor("Viewer", db_path=db_path)
    grant_member(project_id, editor.id, "editor", db_path=db_path)
    grant_member(project_id, viewer.id, "viewer", db_path=db_path)
    posted = client.post(
        f"/projects/{project_id}/adaptive-scores",
        headers={"X-Mukit-Actor": editor.id},
        json={"score": _bar_pair()},
    )
    assert posted.status_code == 201, posted.text
    score_id = posted.json()["score"]["id"]
    scheduled = client.post(
        f"/projects/{project_id}/adaptive-scores/{score_id}/transition-requests",
        headers={"X-Mukit-Actor": viewer.id},
        json=_schedule_body(1),
    )
    assert scheduled.status_code == 201, scheduled.text
    assert scheduled.json()["boundary_tick"] == 3840

    asset = _bar_pair()
    asset["name"] = "Asset bed"
    for state in asset["states"]:
        state["material"] = {"kind": "asset", "asset_kind": "neural_mix", "asset_id": "mix-1"}
    asset_score = client.post(
        f"/projects/{project_id}/adaptive-scores",
        headers={"X-Mukit-Actor": editor.id},
        json={"score": asset, "is_default": False},
    )
    assert asset_score.status_code == 201, asset_score.text
    asset_id = asset_score.json()["score"]["id"]
    asset_schedule = client.post(
        f"/projects/{project_id}/adaptive-scores/{asset_id}/transition-requests",
        headers={"X-Mukit-Actor": viewer.id},
        json=_schedule_body(1),
    )
    assert asset_schedule.status_code == 201, asset_schedule.text
    assert asset_schedule.json()["boundary_tick"] == 3840
    assert asset_schedule.json()["quantization"] == "bar"
    client.close()

