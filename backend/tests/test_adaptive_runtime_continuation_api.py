"""HTTP continuation: the maintain response does not wait for the model."""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from app.composition_plan_schemas import CompositionPlan
from app.services.adaptive_playback_runtime import reset_default_playback_registry
from app.services.adaptive_runtime_continuation_runtime import (
    reset_default_continuation_registry,
)
from app.services.adaptive_runtime_continuation_service import (
    install_continuation_model,
    reset_continuation_model,
)
from app.services.adaptive_score_transition_pending import reset_default_registry
from app.services.fake_symbolic_composer import generate_fake_symbolic_composition
from app.services.symbolic_composition_generate import SymbolicCompositionGenerateError
from tests.test_adaptive_playback import scenario_score
from tests.test_adaptive_playback_api import _client, _composition

import app.db as db_module

BAR_TICKS = 1920
BAR_6_START = 5 * BAR_TICKS


def _reset(monkeypatch: pytest.MonkeyPatch, tmp_path):
    db_module.reset_database_initialization_cache()
    reset_default_registry()
    reset_default_playback_registry()
    reset_default_continuation_registry()
    reset_continuation_model()
    client, db_path = _client(tmp_path, monkeypatch)
    client.__enter__()
    return client, db_path


def _seed(client: TestClient, *, loop: bool = True) -> tuple[str, str, int]:
    composition = _composition()
    composition["harmony"] = [{"start_tick": 0, "duration_ticks": BAR_TICKS, "chord": "Am7"}]
    created = client.post("/projects", json={"name": "Continuation", "composition": composition})
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    score = scenario_score()
    if not loop:
        score = deepcopy(score)
        score["states"][0]["loop"]["enabled"] = False
        for transition in score["transitions"]:
            if transition["quantization"] == "loop_end" and transition["from_state_id"] == "state-exploration":
                transition["quantization"] = "bar"
    posted = client.post(
        f"/projects/{project_id}/adaptive-scores",
        json={"is_default": True, "score": score},
    )
    assert posted.status_code == 201, posted.text
    body = posted.json()
    score_id = body["score"]["id"]
    revision = body["document_revision"]
    started = client.post(
        f"/projects/{project_id}/adaptive-scores/{score_id}/playback",
        json={"expected_document_revision": revision, "mode": "simulation"},
    )
    assert started.status_code == 200, started.text
    assert started.json()["bar"] == 1
    assert started.json()["transport"] == "playing"
    return project_id, score_id, revision


def _local_piece():
    plan = CompositionPlan.model_validate(
        {
            "schema_version": "composition.plan.v1",
            "form": {
                "tempo": 120,
                "key": "C major",
                "time_signature": "4/4",
                "bar_count": 8,
                "sections": [{"type": "verse", "start_bar": 1, "bar_count": 8}],
                "instrumentation": ["piano"],
            },
        }
    )
    music, _report = generate_fake_symbolic_composition(plan, seed=1, prefix_composition=None)
    return music


def _column(db_path, sql: str, project_id: str) -> str:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(sql, (project_id,)).fetchone()
    assert row is not None
    return row[0]


def _wait(client: TestClient, project_id: str, score_id: str, predicate) -> dict:
    deadline = time.monotonic() + 2
    last = None
    while time.monotonic() < deadline:
        response = client.get(
            f"/projects/{project_id}/adaptive-scores/{score_id}/continuation"
        )
        assert response.status_code == 200, response.text
        last = response.json()
        if predicate(last):
            return last
        time.sleep(0.02)
    assert last is not None
    return last


def test_maintain_returns_while_the_model_blocks_and_a_zero_deadline_discards(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    client, db_path = _reset(monkeypatch, tmp_path)
    project_id, score_id, revision = _seed(client)
    composition_before = _column(
        db_path, "SELECT composition_json FROM projects WHERE id = ?", project_id
    )
    body_before = _column(
        db_path, "SELECT body_json FROM adaptive_scores WHERE project_id = ?", project_id
    )
    missing = client.post(
        f"/projects/{project_id}/adaptive-scores/{score_id}/continuation/maintain"
    )
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "continuation_not_running"
    started = client.post(
        f"/projects/{project_id}/adaptive-scores/{score_id}/continuation",
        json={"expected_document_revision": revision, "mode": "continuation"},
    )
    assert started.status_code == 200, started.text
    release = threading.Event()

    def blocked(plan, *, prefix_composition, seed):
        release.wait(timeout=2)
        return _local_piece()

    install_continuation_model(blocked)
    monkeypatch.setenv("ADAPTIVE_CONTINUATION_DEADLINE_MS", "10000")
    path = f"/projects/{project_id}/adaptive-scores/{score_id}/continuation/maintain"
    begin = time.perf_counter()
    first = client.post(path)
    elapsed_ms = (time.perf_counter() - begin) * 1000
    try:
        assert first.status_code == 200, first.text
        assert elapsed_ms < 50
        body = first.json()
        assert body["source"] == "fallback"
        assert body["job_status"] == "pending"
        second = client.post(path)
        assert second.status_code == 200
        assert second.json()["job_id"] == body["job_id"]
        playback = client.get(
            f"/projects/{project_id}/adaptive-scores/{score_id}/playback"
        )
        assert playback.json()["transport"] == "playing"
        assert playback.json()["instructions"]["stop"] is False
        assert playback.json()["instructions"]["loop"]["enabled"] is True
        monkeypatch.setenv("ADAPTIVE_CONTINUATION_DEADLINE_MS", "0")
        release.set()
        late = _wait(
            client,
            project_id,
            score_id,
            lambda row: row["job_status"] == "discarded",
        )
        assert late["source"] == "fallback"
        assert late["telemetry"]["late_discard_count"] == 1
        assert any(item["code"] == "continuation_late" for item in late["warnings"])
        assert _column(
            db_path, "SELECT composition_json FROM projects WHERE id = ?", project_id
        ) == composition_before
        assert _column(
            db_path, "SELECT body_json FROM adaptive_scores WHERE project_id = ?", project_id
        ) == body_before
        score = client.get(f"/projects/{project_id}/adaptive-scores/{score_id}")
        assert score.json()["document_revision"] == revision
        deleted = client.delete(
            f"/projects/{project_id}/adaptive-scores/{score_id}/continuation"
        )
        assert deleted.status_code == 204
        gone = client.get(f"/projects/{project_id}/adaptive-scores/{score_id}/continuation")
        assert gone.status_code == 204
    finally:
        release.set()
        reset_continuation_model()
        client.__exit__(None, None, None)

    maintain_logs = [
        record
        for record in caplog.records
        if getattr(record, "adaptive_runtime_continuation", False) is True
    ]
    assert maintain_logs
    blob = " ".join(record.getMessage() for record in maintain_logs)
    assert "adaptive.runtime.buffer" not in blob
    assert "Am7" not in blob


def test_model_apply_stays_inaudible_inside_the_exploration_loop(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _db = _reset(monkeypatch, tmp_path)
    project_id, score_id, revision = _seed(client)
    client.post(
        f"/projects/{project_id}/adaptive-scores/{score_id}/continuation",
        json={"expected_document_revision": revision, "mode": "continuation"},
    )
    monkeypatch.setenv("ADAPTIVE_CONTINUATION_DEADLINE_MS", "10000")
    install_continuation_model(lambda plan, *, prefix_composition, seed: _local_piece())
    try:
        posted = client.post(
            f"/projects/{project_id}/adaptive-scores/{score_id}/continuation/maintain"
        )
        assert posted.status_code == 200
        applied = _wait(
            client,
            project_id,
            score_id,
            lambda row: row["job_status"] in {"applied", "discarded", "failed"},
        )
        assert applied["source"] == "model"
        assert applied["job_status"] == "applied"
        assert applied["audible"] is False
        assert any(item["code"] == "continuation_outside_loop" for item in applied["warnings"])
        buffer = client.get(
            f"/projects/{project_id}/adaptive-scores/{score_id}/continuation/buffer"
        )
        assert buffer.status_code == 200
        starts = [event["start_tick"] for event in buffer.json()["events"]]
        assert starts
        assert min(starts) >= BAR_6_START
        assert max(starts) < 13 * BAR_TICKS
        playback = client.get(
            f"/projects/{project_id}/adaptive-scores/{score_id}/playback"
        )
        assert playback.json()["transport"] == "playing"
    finally:
        reset_continuation_model()
        client.__exit__(None, None, None)


def test_raised_model_and_state_change_leave_playback_playing(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _db = _reset(monkeypatch, tmp_path)
    project_id, score_id, revision = _seed(client)
    base = f"/projects/{project_id}/adaptive-scores/{score_id}"
    client.post(
        f"{base}/continuation",
        json={"expected_document_revision": revision, "mode": "continuation"},
    )

    def broken(plan, *, prefix_composition, seed):
        raise SymbolicCompositionGenerateError("blocked", code="symbolic_generate_failed")

    install_continuation_model(broken)
    monkeypatch.setenv("ADAPTIVE_CONTINUATION_DEADLINE_MS", "10000")
    release = threading.Event()
    try:
        client.post(f"{base}/continuation/maintain")
        failed = _wait(
            client,
            project_id,
            score_id,
            lambda row: any(item["code"] == "continuation_model_failed" for item in row["warnings"]),
        )
        assert failed["source"] == "fallback"
        playback = client.get(f"{base}/playback")
        assert playback.json()["transport"] == "playing"

        def blocked(plan, *, prefix_composition, seed):
            release.wait(timeout=2)
            return _local_piece()

        install_continuation_model(blocked)
        armed = client.post(f"{base}/continuation/maintain")
        assert armed.status_code == 200
        assert armed.json()["job_status"] == "pending"
        client.post(
            f"{base}/playback/commands",
            json={"op": "request_state", "to_state_id": "state-suspense"},
        )
        client.post(f"{base}/playback/commands", json={"op": "advance", "advance_ticks": 7680})
        moved = client.get(f"{base}/playback")
        assert moved.json()["runtime_state_id"] == "state-suspense"
        release.set()
        stale = _wait(
            client,
            project_id,
            score_id,
            lambda row: any(item["code"] == "continuation_stale" for item in row["warnings"]),
        )
        assert stale["source"] == "fallback"
        assert client.get(f"{base}/playback").json()["transport"] == "playing"
    finally:
        release.set()
        reset_continuation_model()
        client.__exit__(None, None, None)


def test_linear_playback_makes_the_model_buffer_audible(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _db = _reset(monkeypatch, tmp_path)
    project_id, score_id, revision = _seed(client, loop=False)
    base = f"/projects/{project_id}/adaptive-scores/{score_id}"
    client.post(
        f"{base}/continuation",
        json={"expected_document_revision": revision, "mode": "continuation"},
    )
    monkeypatch.setenv("ADAPTIVE_CONTINUATION_DEADLINE_MS", "10000")
    install_continuation_model(lambda plan, *, prefix_composition, seed: _local_piece())
    try:
        client.post(f"{base}/continuation/maintain")
        applied = _wait(
            client,
            project_id,
            score_id,
            lambda row: row["source"] == "model",
        )
        assert applied["audible"] is True
        buffer = client.get(f"{base}/continuation/buffer")
        starts = [event["start_tick"] for event in buffer.json()["events"]]
        assert BAR_6_START in starts
    finally:
        reset_continuation_model()
        client.__exit__(None, None, None)
