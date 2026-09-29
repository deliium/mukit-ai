"""External client for one adaptive engine session. No studio UI."""

from __future__ import annotations

import ast
import asyncio
import logging
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.db import reset_database_initialization_cache
from app.main import app
from app.services.adaptive_engine_service import (
    EngineFeed,
    _put_now,
    advance_engine_clock,
    reset_default_engine_registry,
)
from app.services.adaptive_musical_context_runtime import reset_default_context_registry
from app.services.adaptive_playback_runtime import reset_default_playback_registry
from app.services.adaptive_score_transition_pending import reset_default_registry

_TOKEN = "adaptive-engine-test-token"
_AUTH = {"Authorization": f"Bearer {_TOKEN}"}
_BANNED_SEGMENTS = frozenset(
    {
        "ai_runtime",
        "ai_agents",
        "fake_llm",
        "llm_chat_client",
        "music_transformer",
        "symbolic_composition_generate",
        "adaptive_runtime_continuation",
        "adaptive_runtime_continuation_service",
        "adaptive_playback",
    }
)


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("PROJECT_DB_PATH", str(tmp_path / "projects.db"))
    monkeypatch.setenv("ADAPTIVE_ENGINE_TOKEN", _TOKEN)
    monkeypatch.setenv("ADAPTIVE_ENGINE_TICKER", "manual")
    monkeypatch.setenv("ADAPTIVE_ENGINE_CONTEXT_BURST", "1")
    monkeypatch.delenv("COLLABORATION_ENABLED", raising=False)
    reset_database_initialization_cache()
    reset_default_registry()
    reset_default_playback_registry()
    reset_default_context_registry()
    reset_default_engine_registry()
    return TestClient(app)


def _composition() -> dict:
    return {
        "schema_version": "composition.v2",
        "tempo": 120,
        "key": "C major",
        "time_signature": "4/4",
        "ticks_per_quarter": 480,
        "bar_count": 8,
        "duration_ticks": 15360,
        "sections": [
            {
                "id": "section-a",
                "type": "verse",
                "start_bar": 1,
                "bar_count": 4,
                "start_tick": 0,
                "duration_ticks": 7680,
            },
            {
                "id": "section-b",
                "type": "chorus",
                "start_bar": 5,
                "bar_count": 4,
                "start_tick": 7680,
                "duration_ticks": 7680,
            },
        ],
        "tracks": [
            {
                "id": "track-pad",
                "name": "Pad",
                "instrument": "piano",
                "role": "pad",
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


def _score() -> dict:
    return {
        "schema_version": "adaptive.score.v1",
        "name": "Engine fixture",
        "initial_state_id": "explore",
        "default_state_id": "explore",
        "states": [
            {
                "id": "explore",
                "name": "Explore",
                "intensity": 0.2,
                "material": {"kind": "section", "section_id": "section-a"},
                "transition_ids": ["tr-now"],
            },
            {
                "id": "tension",
                "name": "Tension",
                "intensity": 0.6,
                "material": {"kind": "section", "section_id": "section-b"},
                "transition_ids": ["tr-bar"],
            },
        ],
        "transitions": [
            {
                "id": "tr-now",
                "from_state_id": "explore",
                "to_state_id": "tension",
                "quantization": "immediate",
                "priority": 0,
                "conditions": [],
                "realization": {"kind": "cut"},
            },
            {
                "id": "tr-bar",
                "from_state_id": "tension",
                "to_state_id": "explore",
                "quantization": "bar",
                "priority": 0,
                "conditions": [],
                "realization": {"kind": "cut"},
            },
        ],
    }


def _mapping() -> dict:
    return {
        "schema_version": "adaptive.context.mapping.v1",
        "baseline_state_id": "explore",
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
        "state_rules": [],
    }


def _seed(client: TestClient) -> tuple[str, str, int]:
    created = client.post("/projects", json={"name": "Engine", "composition": _composition()})
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    posted = client.post(
        f"/projects/{project_id}/adaptive-scores",
        json={"is_default": True, "score": _score()},
    )
    assert posted.status_code == 201, posted.text
    body = posted.json()
    return project_id, body["score"]["id"], body["document_revision"]


def _column(db_path: Path, sql: str, project_id: str) -> str:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(sql, (project_id,)).fetchone()
    assert row is not None and row[0] is not None
    return str(row[0])


def test_external_client_drives_one_score(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    client = _client(tmp_path, monkeypatch)
    project_id, score_id, revision = _seed(client)
    db_path = tmp_path / "projects.db"
    composition_before = _column(
        db_path, "SELECT composition_json FROM projects WHERE id = ?", project_id
    )
    missing = client.post(
        "/adaptive/session",
        json={
            "project_id": project_id,
            "score_id": score_id,
            "expected_document_revision": revision,
        },
    )
    assert missing.status_code == 401
    assert missing.json()["detail"]["code"] == "engine_unauthorized"
    forwarded = client.post(
        "/adaptive/session",
        json={
            "project_id": project_id,
            "score_id": score_id,
            "expected_document_revision": revision,
        },
        headers={"X-Forwarded-For": "127.0.0.1"},
    )
    assert forwarded.status_code == 401
    query = client.post(
        "/adaptive/session?token=present",
        json={
            "project_id": project_id,
            "score_id": score_id,
            "expected_document_revision": revision,
        },
        headers=_AUTH,
    )
    assert query.status_code == 401

    with pytest.raises(WebSocketDisconnect) as missing_socket:
        with client.websocket_connect(
            "/adaptive/events?session_id=aeng_00000000",
            headers=_AUTH,
        ) as early:
            early.receive_json()
    assert missing_socket.value.code == 4404

    started = client.post(
        "/adaptive/session",
        json={
            "project_id": project_id,
            "score_id": score_id,
            "expected_document_revision": revision,
            "mapping": _mapping(),
        },
        headers=_AUTH,
    )
    assert started.status_code == 201, started.text
    session = started.json()
    assert session["transport"] == "playing"
    assert session["clock_owner"] == "engine"
    assert session["schema_version"] == "adaptive.engine.session.v1"
    forbidden = {"events", "prompt", "model_id", "composition", "api_key", "instructions", "queue"}
    assert forbidden.isdisjoint(session)
    session_id = session["session_id"]
    with client.websocket_connect(
        f"/adaptive/status?session_id={session_id}",
        headers=_AUTH,
    ) as status:
        snapshot = status.receive_json()
    assert snapshot["session_id"] == session_id
    assert set(snapshot) <= {
        "schema_version",
        "session_id",
        "project_id",
        "score_id",
        "clock_owner",
        "transport",
        "runtime_state_id",
        "bar",
        "beat",
        "intensity",
        "phase",
        "active_stinger_id",
        "pending_transition_id",
        "pending_to_state_id",
        "warnings",
        "document_revision",
        "context_attached",
        "telemetry",
    }

    again = client.post(
        "/adaptive/session",
        json={
            "project_id": project_id,
            "score_id": score_id,
            "expected_document_revision": revision,
        },
        headers=_AUTH,
    )
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "engine_session_exists"

    with client.websocket_connect(
        f"/adaptive/events?session_id={session_id}",
        headers=_AUTH,
    ) as events:
        changed = client.post(
            f"/adaptive/session/{session_id}/state",
            json={"to_state_id": "tension", "transition_id": "tr-now", "request_id": "req_now1"},
            headers=_AUTH,
        )
        assert changed.status_code == 200, changed.text
        body = changed.json()
        assert body["disposition"] == "committed"
        assert body["session"]["runtime_state_id"] == "tension"
        ack = events.receive_json()
        assert ack["schema_version"] == "adaptive.engine.ack.v1"
        assert ack["request_id"] == "req_now1"
        assert set(ack) <= {
            "schema_version",
            "session_id",
            "request_id",
            "kind",
            "disposition",
            "runtime_state_id",
            "detail_code",
        }

    score = client.get(f"/projects/{project_id}/adaptive-scores/{score_id}")
    assert score.status_code == 200
    assert score.json()["document_revision"] == revision
    assert (
        _column(db_path, "SELECT composition_json FROM projects WHERE id = ?", project_id)
        == composition_before
    )

    intensity = client.post(
        f"/adaptive/session/{session_id}/intensity",
        json={"intensity": 0.8, "request_id": "req_level1"},
        headers=_AUTH,
    )
    assert intensity.status_code == 200, intensity.text
    assert intensity.json()["session"]["intensity"] == 0.8

    unwired = client.post(
        f"/adaptive/session/{session_id}/event",
        json={"kind": "stinger", "stinger_id": "stinger-missing", "request_id": "req_sting1"},
        headers=_AUTH,
    )
    assert unwired.status_code == 422
    assert unwired.json()["detail"]["code"] == "engine_stinger_unwired"
    current = client.get(f"/adaptive/session/{session_id}", headers=_AUTH)
    assert current.json()["runtime_state_id"] == "tension"

    with client.websocket_connect(
        f"/adaptive/events?session_id={session_id}",
        headers=_AUTH,
    ) as events:
        advance_engine_clock(session_id, 1)
        queued = client.post(
            f"/adaptive/session/{session_id}/state",
            json={"to_state_id": "explore", "transition_id": "tr-bar", "request_id": "req_bar01"},
            headers=_AUTH,
        )
        assert queued.status_code == 200, queued.text
        assert queued.json()["disposition"] == "queued"
        queued_ack = events.receive_json()
        assert queued_ack["disposition"] == "queued"
        assert queued_ack["request_id"] == "req_bar01"
        landed = None
        for _ in range(40):
            landed = advance_engine_clock(session_id, 5)
            if landed.runtime_state_id == "explore":
                break
        assert landed is not None
        assert landed.runtime_state_id == "explore"
        finished = events.receive_json()
        assert finished["disposition"] == "finished"
        assert finished["request_id"] == "req_bar01"
        after = client.get(f"/adaptive/session/{session_id}", headers=_AUTH)
        assert after.json()["runtime_state_id"] == "explore"
        assert after.json()["document_revision"] == revision

    deleted = client.delete(f"/adaptive/session/{session_id}", headers=_AUTH)
    assert deleted.status_code == 204
    gone = client.post(
        f"/adaptive/session/{session_id}/state",
        json={"to_state_id": "tension", "request_id": "req_after1"},
        headers=_AUTH,
    )
    assert gone.status_code == 404
    assert gone.json()["detail"]["code"] == "engine_session_missing"

    playback = client.post(
        f"/projects/{project_id}/adaptive-scores/{score_id}/playback",
        json={"expected_document_revision": revision, "mode": "simulation"},
    )
    assert playback.status_code == 200, playback.text
    bound = client.post(
        "/adaptive/session",
        json={
            "project_id": project_id,
            "score_id": score_id,
            "expected_document_revision": revision,
        },
        headers=_AUTH,
    )
    assert bound.status_code == 201, bound.text
    assert bound.json()["clock_owner"] == "existing"
    bound_id = bound.json()["session_id"]
    held_bar = bound.json()["bar"]
    pumped = advance_engine_clock(bound_id, 5)
    assert pumped.bar == held_bar
    assert "engine_clock_not_owned" in pumped.warnings
    client.delete(f"/adaptive/session/{bound_id}", headers=_AUTH)
    client.delete(f"/projects/{project_id}/adaptive-scores/{score_id}/playback")

    calls: list[int] = []
    from app.services import adaptive_engine_service as engine_service

    original = engine_service.sample_adaptive_musical_context

    def _spy(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(engine_service, "sample_adaptive_musical_context", _spy)
    mapped = client.post(
        "/adaptive/session",
        json={
            "project_id": project_id,
            "score_id": score_id,
            "expected_document_revision": revision,
            "mapping": _mapping(),
        },
        headers=_AUTH,
    )
    assert mapped.status_code == 201, mapped.text
    mapped_id = mapped.json()["session_id"]
    sample = {"schema_version": "adaptive.context.external.v1", "values": {"danger": 0.4}}
    first = client.post(f"/adaptive/session/{mapped_id}/context", json=sample, headers=_AUTH)
    assert first.status_code == 200, first.text
    assert first.json()["applied"] is True
    assert len(calls) == 1
    second = client.post(
        f"/adaptive/session/{mapped_id}/context",
        json={"schema_version": "adaptive.context.external.v1", "values": {"danger": 0.9}},
        headers=_AUTH,
    )
    assert second.status_code == 202, second.text
    assert second.json()["coalesced"] is True
    assert second.json()["applied"] is False
    assert len(calls) == 1
    advance_engine_clock(mapped_id, 1)
    assert len(calls) == 2
    assert _TOKEN not in caplog.text


def test_full_ack_queue_closes_without_dropping_queued_frames(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    loop = asyncio.new_event_loop()
    try:
        feed = EngineFeed(loop=loop, queue=asyncio.Queue(maxsize=1), kind="events")
        _put_now(feed, {"frame": "ack", "body": {"request_id": "req_keep"}})
        _put_now(feed, {"frame": "ack", "body": {"request_id": "req_over"}})
        assert feed.close_code == 1013
        kept = feed.queue.get_nowait()
        assert kept["body"]["request_id"] == "req_keep"
        assert feed.queue.empty()
        _put_now(feed, {"frame": "close", "code": 4404})
        assert feed.close_code == 1013
    finally:
        loop.close()
    assert "req_over" not in caplog.text
    assert "[FIX]" in caplog.text


def test_status_frame_is_replaced_before_an_ack_closes_the_queue() -> None:
    loop = asyncio.new_event_loop()
    try:
        feed = EngineFeed(loop=loop, queue=asyncio.Queue(maxsize=1), kind="status")
        _put_now(feed, {"frame": "status", "body": {"bar": 1}})
        _put_now(feed, {"frame": "ack", "body": {"request_id": "req_keep"}})
        assert feed.close_code is None
        kept = feed.queue.get_nowait()
        assert kept["frame"] == "ack"
    finally:
        loop.close()


def test_engine_modules_stay_outside_agents_and_models() -> None:
    root = Path(__file__).resolve().parents[1]
    targets = [
        root / "app" / "services" / "adaptive_engine_service.py",
        root / "app" / "routers" / "adaptive_engine.py",
        root / "app" / "services" / "adaptive_engine_auth.py",
        root / "app" / "services" / "adaptive_engine_backpressure.py",
    ]
    for path in targets:
        for module in _imported_modules(path):
            assert _BANNED_SEGMENTS.isdisjoint(module.split("."))
    agents = root / "app" / "ai_agents"
    for path in agents.rglob("*.py"):
        for module in _imported_modules(path):
            segments = set(module.split("."))
            assert "adaptive_engine_service" not in segments
            assert "adaptive_engine_schemas" not in segments
    frontend = root.parent / "frontend" / "src"
    if frontend.is_dir():
        blob = "\n".join(
            item.read_text(encoding="utf-8", errors="ignore")
            for item in frontend.rglob("*")
            if item.is_file() and item.suffix in {".js", ".jsx", ".ts", ".tsx"}
        )
        assert "/adaptive/session" not in blob


def _imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules
