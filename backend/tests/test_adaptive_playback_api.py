"""HTTP simulation of the locked adaptive playback script."""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import reset_database_initialization_cache
from app.main import app
from app.services.adaptive_playback_runtime import reset_default_playback_registry
from app.services.adaptive_score_transition_pending import reset_default_registry
from tests.test_adaptive_playback import scenario_score

_SCRIPT = [
    {"op": "advance", "advance_ticks": 7680},
    {"op": "advance", "advance_ticks": 2400},
    {"op": "request_state", "to_state_id": "state-missing"},
    {"op": "request_state", "to_state_id": "state-suspense"},
    {"op": "request_state", "to_state_id": "state-combat"},
    {"op": "request_state", "to_state_id": "state-victory"},
    {"op": "advance", "advance_ticks": 5280},
    {"op": "advance", "advance_ticks": 480},
    {"op": "set_intensity", "intensity": 0.5},
    {"op": "advance", "advance_ticks": 7200},
    {"op": "advance", "advance_ticks": 1920},
    {"op": "advance", "advance_ticks": 720},
    {"op": "set_intensity", "intensity": 1},
    {"op": "advance", "advance_ticks": 5040},
    {"op": "advance", "advance_ticks": 1920},
]


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Path]:
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.delenv("COLLABORATION_ENABLED", raising=False)
    reset_database_initialization_cache()
    reset_default_registry()
    reset_default_playback_registry()
    return TestClient(app), db_path


def _track(track_id: str, role: str, channel: int) -> dict:
    return {
        "id": track_id,
        "name": track_id,
        "instrument": "piano",
        "role": role,
        "midi_program": 0,
        "channel": channel,
        "events": [],
    }


def _composition() -> dict:
    sections = []
    types = ("verse", "chorus", "bridge", "outro")
    ids = (
        "section-exploration",
        "section-suspense",
        "section-combat",
        "section-victory",
    )
    for index, (section_id, section_type) in enumerate(zip(ids, types, strict=True)):
        sections.append(
            {
                "id": section_id,
                "type": section_type,
                "start_bar": 1 + index * 4,
                "bar_count": 4,
                "start_tick": index * 7680,
                "duration_ticks": 7680,
            }
        )
    return {
        "schema_version": "composition.v2",
        "tempo": 120,
        "key": "C major",
        "time_signature": "4/4",
        "ticks_per_quarter": 480,
        "bar_count": 16,
        "duration_ticks": 30720,
        "sections": sections,
        "tracks": [
            _track("track-pad", "pad", 1),
            _track("track-piano", "harmony", 2),
            _track("track-bass", "bass", 3),
            _track("track-strings", "other", 4),
            _track("track-perc", "percussion", 5),
            _track("track-brass", "other", 6),
            _track("track-orch", "other", 7),
            _track("track-stinger", "other", 8),
        ],
        "harmony": [],
        "tempo_changes": [],
        "time_signature_changes": [],
        "key_changes": [],
        "markers": [],
    }


def _column(db_path: Path, sql: str, project_id: str) -> str:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(sql, (project_id,)).fetchone()
    assert row is not None and row[0] is not None
    return str(row[0])


def _seed(client: TestClient) -> tuple[str, str, int]:
    created = client.post("/projects", json={"name": "Playback", "composition": _composition()})
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    posted = client.post(
        f"/projects/{project_id}/adaptive-scores",
        json={"is_default": True, "score": scenario_score()},
    )
    assert posted.status_code == 201, posted.text
    body = posted.json()
    return project_id, body["score"]["id"], body["document_revision"]


def _run(client: TestClient, project_id: str, score_id: str, revision: int) -> list[dict]:
    started = client.post(
        f"/projects/{project_id}/adaptive-scores/{score_id}/playback",
        json={"expected_document_revision": revision, "mode": "simulation"},
    )
    assert started.status_code == 200, started.text
    rows = [_fingerprint(started.json())]
    for command in _SCRIPT:
        response = client.post(
            f"/projects/{project_id}/adaptive-scores/{score_id}/playback/commands",
            json=command,
        )
        assert response.status_code == 200, response.text
        rows.append(_fingerprint(response.json()))
    return rows


def _fingerprint(body: dict) -> dict:
    pending = body["pending_transition"]
    return {
        "position_tick": body["position_tick"],
        "bar": body["bar"],
        "beat": body["beat"],
        "runtime_state_id": body["runtime_state_id"],
        "phase": body["phase"],
        "transport": body["transport"],
        "stop": body["instructions"]["stop"],
        "last_event": body["telemetry"]["last_event"],
        "rejected": body["telemetry"]["rejected_request_count"],
        "boundary": None if pending is None else pending["boundary_tick"],
        "stinger": body["active_stinger_id"],
        "active": body["active_layer_ids"],
        "phrase": None if body["phrase"] is None else [body["phrase"]["start_tick"], body["phrase"]["end_tick"]],
        "seek": body["instructions"]["seek_tick"],
        "stinger_gain": next(
            row["target_gain"]
            for row in body["instructions"]["track_gains"]
            if row["track_id"] == "track-stinger"
        ),
    }


def test_simulation_script_keeps_the_score(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, db_path = _client(tmp_path, monkeypatch)
    project_id, score_id, revision = _seed(client)
    composition_before = _column(
        db_path,
        "SELECT composition_json FROM projects WHERE id = ?",
        project_id,
    )
    body_before = _column(
        db_path,
        "SELECT body_json FROM adaptive_scores WHERE project_id = ?",
        project_id,
    )
    caplog.set_level(logging.INFO)
    first = _run(client, project_id, score_id, revision)
    rejected = next(row for row in first if row["last_event"] == "request_rejected")
    assert rejected["transport"] == "playing"
    assert rejected["stop"] is False
    assert rejected["rejected"] == 1
    assert first[-1]["position_tick"] == 24960
    assert first[-1]["runtime_state_id"] == "state-victory"
    assert first[-1]["stinger"] is None
    assert first[-1]["stinger_gain"] == 0
    committed = next(row for row in first if row["runtime_state_id"] == "state-suspense" and row["position_tick"] == 7680)
    assert committed["bar"] == 5 and committed["beat"] == 1
    assert client.delete(
        f"/projects/{project_id}/adaptive-scores/{score_id}/playback"
    ).status_code == 204
    second = _run(client, project_id, score_id, revision)
    assert first == second
    score = client.get(f"/projects/{project_id}/adaptive-scores/{score_id}")
    assert score.status_code == 200
    assert score.json()["document_revision"] == revision
    assert _column(db_path, "SELECT composition_json FROM projects WHERE id = ?", project_id) == composition_before
    assert _column(db_path, "SELECT body_json FROM adaptive_scores WHERE project_id = ?", project_id) == body_before
    playback_logs = [record for record in caplog.records if getattr(record, "adaptive_playback", None) is True]
    assert any(getattr(record, "last_event", None) == "state_committed" for record in playback_logs)
    for record in playback_logs:
        blob = record.getMessage() + json.dumps(
            {key: getattr(record, key, None) for key in record.__dict__},
            default=str,
        )
        assert "pitch" not in blob
        assert "events" not in blob


def test_invalid_body_does_not_move_and_missing_session_is_404(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _db_path = _client(tmp_path, monkeypatch)
    project_id, score_id, revision = _seed(client)
    missing = client.post(
        f"/projects/{project_id}/adaptive-scores/{score_id}/playback/commands",
        json={"op": "advance", "advance_ticks": 10},
    )
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "playback_not_running"
    started = client.post(
        f"/projects/{project_id}/adaptive-scores/{score_id}/playback",
        json={"expected_document_revision": revision, "mode": "simulation"},
    )
    assert started.status_code == 200
    bad = client.post(
        f"/projects/{project_id}/adaptive-scores/{score_id}/playback/commands",
        json={"op": "advance", "advance_ticks": True},
    )
    assert bad.status_code == 422
    current = client.get(f"/projects/{project_id}/adaptive-scores/{score_id}/playback")
    assert current.status_code == 200
    assert current.json()["position_tick"] == 0
    assert client.delete(
        f"/projects/{project_id}/adaptive-scores/{score_id}/playback"
    ).status_code == 204
    assert client.delete(
        f"/projects/{project_id}/adaptive-scores/{score_id}/playback"
    ).status_code == 204
    assert client.get(f"/projects/{project_id}/adaptive-scores/{score_id}/playback").status_code == 204
