"""HTTP context stream against a running adaptive playback session."""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import reset_database_initialization_cache
from app.main import app
from app.services.adaptive_musical_context_runtime import reset_default_context_registry
from app.services.adaptive_playback_runtime import reset_default_playback_registry
from app.services.adaptive_score_transition_pending import reset_default_registry


def setup_module() -> None:
    reset_default_playback_registry()
    reset_default_context_registry()
    reset_default_registry()


def teardown_module() -> None:
    reset_default_playback_registry()
    reset_default_context_registry()
    reset_default_registry()


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Path]:
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.delenv("COLLABORATION_ENABLED", raising=False)
    reset_database_initialization_cache()
    reset_default_registry()
    reset_default_playback_registry()
    reset_default_context_registry()
    return TestClient(app), db_path


def _composition() -> dict:
    sections = [
        {
            "id": "section-exploration",
            "type": "verse",
            "start_bar": 1,
            "bar_count": 4,
            "start_tick": 0,
            "duration_ticks": 7680,
        },
        {
            "id": "section-combat",
            "type": "chorus",
            "start_bar": 5,
            "bar_count": 4,
            "start_tick": 7680,
            "duration_ticks": 7680,
        },
    ]
    return {
        "schema_version": "composition.v2",
        "tempo": 120,
        "key": "C major",
        "time_signature": "4/4",
        "ticks_per_quarter": 480,
        "bar_count": 8,
        "duration_ticks": 15360,
        "sections": sections,
        "tracks": [
            {
                "id": "track-piano",
                "name": "Piano",
                "instrument": "piano",
                "role": "harmony",
                "midi_program": 0,
                "channel": 1,
                "events": [],
            }
        ],
        "harmony": [],
        "tempo_changes": [],
        "time_signature_changes": [],
        "key_changes": [],
        "markers": [],
    }


def _transition(transition_id: str, source: str, target: str) -> dict:
    return {
        "id": transition_id,
        "from_state_id": source,
        "to_state_id": target,
        "quantization": "immediate",
        "priority": 0,
        "conditions": [],
        "realization": {"kind": "cut"},
    }


def _score() -> dict:
    return {
        "schema_version": "adaptive.score.v1",
        "name": "Context fixture",
        "initial_state_id": "state-exploration",
        "default_state_id": "state-exploration",
        "states": [
            {
                "id": "state-exploration",
                "name": "Exploration",
                "intensity": 0.2,
                "material": {"kind": "section", "section_id": "section-exploration"},
                "transition_ids": ["tr-to-combat"],
            },
            {
                "id": "state-combat",
                "name": "Combat",
                "intensity": 0.8,
                "material": {"kind": "section", "section_id": "section-combat"},
                "transition_ids": ["tr-to-exploration"],
            },
        ],
        "transitions": [
            _transition("tr-to-combat", "state-exploration", "state-combat"),
            _transition("tr-to-exploration", "state-combat", "state-exploration"),
        ],
    }


def _locked_mapping(*, target: str = "state-combat") -> dict:
    return {
        "schema_version": "adaptive.context.mapping.v1",
        "baseline_state_id": "state-exploration",
        "bindings": [
            {
                "id": "bind-danger",
                "kind": "numeric",
                "external_key": "danger",
                "slot": "danger",
                "transform": "identity",
                "smooth_alpha": 1,
            }
        ],
        "state_rules": [
            {
                "id": "rule-combat",
                "kind": "numeric_band",
                "slot": "danger",
                "polarity": "high",
                "enter": 0.65,
                "exit": 0.35,
                "min_dwell_samples": 3,
                "target_state_id": target,
                "priority": 10,
            }
        ],
        "intensity": {"slot": "danger", "emit_epsilon": 0.02},
    }


def _column(db_path: Path, sql: str, project_id: str) -> str:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(sql, (project_id,)).fetchone()
    assert row is not None and row[0] is not None
    return str(row[0])


def _seed(client: TestClient) -> tuple[str, str, int]:
    created = client.post("/projects", json={"name": "Context", "composition": _composition()})
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    posted = client.post(
        f"/projects/{project_id}/adaptive-scores",
        json={"is_default": True, "score": _score()},
    )
    assert posted.status_code == 201, posted.text
    body = posted.json()
    return project_id, body["score"]["id"], body["document_revision"]


def _context(project_id: str, score_id: str) -> str:
    return f"/projects/{project_id}/adaptive-scores/{score_id}/context"


def test_streams_drive_playback_without_writing_the_score(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, db_path = _client(tmp_path, monkeypatch)
    project_id, score_id, revision = _seed(client)
    composition_before = _column(db_path, "SELECT composition_json FROM projects WHERE id = ?", project_id)
    body_before = _column(db_path, "SELECT body_json FROM adaptive_scores WHERE project_id = ?", project_id)
    started = client.post(
        f"/projects/{project_id}/adaptive-scores/{score_id}/playback",
        json={"expected_document_revision": revision, "mode": "simulation"},
    )
    assert started.status_code == 200, started.text
    armed = client.post(
        _context(project_id, score_id),
        json={"expected_document_revision": revision, "mapping": _locked_mapping()},
    )
    assert armed.status_code == 200, armed.text
    assert armed.json()["sample_index"] == 0
    assert armed.json()["musical_state_id"] == "state-exploration"
    caplog.set_level(logging.INFO)
    stream_a = [0.49 if index % 2 == 0 else 0.51 for index in range(20)]
    for value in stream_a:
        response = client.post(
            f"{_context(project_id, score_id)}/samples",
            json={"schema_version": "adaptive.context.external.v1", "values": {"danger": value}},
        )
        assert response.status_code == 200, response.text
        assert response.json()["musical_state_id"] == "state-exploration"
        assert "request_state" not in [item["op"] for item in response.json()["emitted"]]
    playback = client.get(f"/projects/{project_id}/adaptive-scores/{score_id}/playback")
    assert playback.status_code == 200
    assert playback.json()["runtime_state_id"] == "state-exploration"
    fourth = None
    for value in (0.2, 0.7, 0.7, 0.7):
        fourth = client.post(
            f"{_context(project_id, score_id)}/samples",
            json={"schema_version": "adaptive.context.external.v1", "values": {"danger": value}},
        )
        assert fourth.status_code == 200, fourth.text
    assert fourth is not None
    assert fourth.json()["musical_state_id"] == "state-combat"
    followed = client.get(f"/projects/{project_id}/adaptive-scores/{score_id}/playback")
    assert followed.status_code == 200
    assert followed.json()["runtime_state_id"] == "state-combat"
    score = client.get(f"/projects/{project_id}/adaptive-scores/{score_id}")
    assert score.json()["document_revision"] == revision
    assert _column(db_path, "SELECT composition_json FROM projects WHERE id = ?", project_id) == composition_before
    assert _column(db_path, "SELECT body_json FROM adaptive_scores WHERE project_id = ?", project_id) == body_before
    sample_logs = [
        record
        for record in caplog.records
        if getattr(record, "adaptive_musical_context", None) is True and getattr(record, "sample_index", 0) >= 1
    ]
    assert len(sample_logs) == 24
    for record in sample_logs:
        blob = record.getMessage() + json.dumps(
            {key: getattr(record, key, None) for key in ("musical_state_id", "emitted_ops", "warning_codes")},
            default=str,
        )
        assert getattr(record, "musical_state_id", None)
        assert getattr(record, "emitted_ops", None) is not None
        assert "0.49" not in blob
        assert "values" not in blob


def test_missing_target_and_quiet_session_edges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _db_path = _client(tmp_path, monkeypatch)
    project_id, score_id, revision = _seed(client)
    missing = client.post(
        f"{_context(project_id, score_id)}/samples",
        json={"schema_version": "adaptive.context.external.v1", "values": {"danger": 0.7}},
    )
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "context_not_running"
    started = client.post(
        f"/projects/{project_id}/adaptive-scores/{score_id}/playback",
        json={"expected_document_revision": revision, "mode": "simulation"},
    )
    assert started.status_code == 200, started.text
    rejected = client.post(
        _context(project_id, score_id),
        json={
            "expected_document_revision": revision,
            "mapping": _locked_mapping(target="state-missing"),
        },
    )
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "dangling_state_ref"
    assert client.get(_context(project_id, score_id)).status_code == 204
    playback = client.get(f"/projects/{project_id}/adaptive-scores/{score_id}/playback")
    assert playback.json()["runtime_state_id"] == "state-exploration"
    quiet = client.post(
        _context(project_id, score_id),
        json={"expected_document_revision": revision, "mapping": _locked_mapping()},
    )
    assert quiet.status_code == 200, quiet.text
    client.delete(f"/projects/{project_id}/adaptive-scores/{score_id}/playback")
    sample = client.post(
        f"{_context(project_id, score_id)}/samples",
        json={"schema_version": "adaptive.context.external.v1", "values": {"danger": 0.2}},
    )
    assert sample.status_code == 200, sample.text
    assert sample.json()["warnings"][0]["code"] == "playback_not_running"
    assert client.get(f"/projects/{project_id}/adaptive-scores/{score_id}/playback").status_code == 204
    assert client.delete(_context(project_id, score_id)).status_code == 204
    assert client.delete(_context(project_id, score_id)).status_code == 204
    assert client.get(_context(project_id, score_id)).status_code == 204
