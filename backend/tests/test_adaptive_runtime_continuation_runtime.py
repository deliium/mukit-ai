"""Session tests: the arm path does not wait for the symbolic model."""

from __future__ import annotations

import asyncio
import logging
import time

import pytest
from fastapi.testclient import TestClient

from app.adaptive_playback_schemas import parse_adaptive_playback_command
from app.adaptive_runtime_continuation_schemas import parse_adaptive_runtime_continuation_start
from app.services.adaptive_playback_runtime import reset_default_playback_registry
from app.services.adaptive_playback_service import command_adaptive_playback
from app.services.adaptive_runtime_continuation import continuation_seed
from app.services.adaptive_runtime_continuation_runtime import (
    reset_default_continuation_registry,
)
from app.services.adaptive_runtime_continuation_service import (
    get_adaptive_runtime_continuation,
    install_continuation_model,
    maintain_adaptive_runtime_continuation,
    reset_continuation_model,
    start_adaptive_runtime_continuation,
    stop_adaptive_runtime_continuation,
)
from app.services.adaptive_score_transition_pending import reset_default_registry
from app.composition_plan_schemas import CompositionPlan
from app.services.fake_symbolic_composer import generate_fake_symbolic_composition
from app.services.symbolic_composition_generate import SymbolicCompositionGenerateError
from tests.test_adaptive_playback import scenario_score
from tests.test_adaptive_playback_api import _client, _composition

import app.db as db_module


def _reset(monkeypatch: pytest.MonkeyPatch, tmp_path) -> TestClient:
    reset_database_initialization_cache = db_module.reset_database_initialization_cache
    reset_database_initialization_cache()
    reset_default_registry()
    reset_default_playback_registry()
    reset_default_continuation_registry()
    reset_continuation_model()
    client, _db = _client(tmp_path, monkeypatch)
    return client


def _seed(client: TestClient, *, chord: str = "Am7") -> tuple[str, str, int]:
    composition = _composition()
    composition["harmony"] = [
        {"start_tick": 0, "duration_ticks": 1920, "chord": chord},
    ]
    created = client.post("/projects", json={"name": "Continuation", "composition": composition})
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    posted = client.post(
        f"/projects/{project_id}/adaptive-scores",
        json={"is_default": True, "score": scenario_score()},
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


def test_maintain_returns_before_a_blocking_model_and_reuses_the_job(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    client = _reset(monkeypatch, tmp_path)
    project_id, score_id, revision = _seed(client)
    release = __import__("threading").Event()
    calls = {"count": 0}

    def blocked(plan, *, prefix_composition, seed):
        calls["count"] += 1
        calls["seed"] = seed
        release.wait(timeout=2)
        return _local_piece()

    install_continuation_model(blocked)
    stored: list = []

    def scheduler(coro):
        stored.append(coro)
        return coro

    start_adaptive_runtime_continuation(
        project_id,
        score_id,
        parse_adaptive_runtime_continuation_start(
            {"expected_document_revision": revision, "mode": "continuation"}
        ),
    )
    started = time.perf_counter()
    first = maintain_adaptive_runtime_continuation(
        project_id,
        score_id,
        scheduler=scheduler,
        now_ms=lambda: 0,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    try:
        assert elapsed_ms < 50
        assert calls["count"] == 0
        assert first.source == "fallback"
        assert first.job_status == "pending"
        assert len(stored) == 1
        second = maintain_adaptive_runtime_continuation(
            project_id,
            score_id,
            scheduler=scheduler,
            now_ms=lambda: 0,
        )
        assert second.job_id == first.job_id
        assert len(stored) == 1
        playback = client.get(
            f"/projects/{project_id}/adaptive-scores/{score_id}/playback"
        )
        assert playback.status_code == 200
        assert playback.json()["transport"] == "playing"
    finally:
        release.set()
        stored[0].close()
        reset_continuation_model()


def test_finished_model_applies_only_while_the_job_is_still_current(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    client = _reset(monkeypatch, tmp_path)
    project_id, score_id, revision = _seed(client, chord="Am7")
    seen: dict = {}

    def model(plan, *, prefix_composition, seed):
        seen["seed"] = seed
        return _local_piece()

    install_continuation_model(model)
    stored: list = []

    class Handle:
        def __init__(self) -> None:
            self.cancelled = False

        def cancel(self) -> None:
            self.cancelled = True

    def scheduler(coro):
        stored.append(coro)
        return Handle()

    try:
        start_adaptive_runtime_continuation(
            project_id,
            score_id,
            parse_adaptive_runtime_continuation_start(
                {"expected_document_revision": revision, "mode": "continuation"}
            ),
        )
        first = maintain_adaptive_runtime_continuation(
            project_id,
            score_id,
            scheduler=scheduler,
            now_ms=lambda: 0,
        )
        expected = continuation_seed(1, "continuation", "state-exploration")
        monkeypatch.setenv("ADAPTIVE_CONTINUATION_DEADLINE_MS", "0")
        asyncio.run(stored[0])
        late = get_adaptive_runtime_continuation(project_id, score_id)
        assert late is not None
        assert late.source == "fallback"
        assert late.job_status == "discarded"
        assert late.telemetry.late_discard_count == 1
        assert any(item.code == "continuation_late" for item in late.warnings)
        assert seen["seed"] == expected

        monkeypatch.setenv("ADAPTIVE_CONTINUATION_DEADLINE_MS", "10000")

        def fast(plan, *, prefix_composition, seed):
            return _local_piece()

        install_continuation_model(fast)
        ready = maintain_adaptive_runtime_continuation(
            project_id,
            score_id,
            scheduler=scheduler,
            now_ms=lambda: 0,
        )
        assert ready.job_id != first.job_id
        command_adaptive_playback(
            project_id,
            score_id,
            parse_adaptive_playback_command({"op": "advance", "advance_ticks": 1920}),
        )
        moved = maintain_adaptive_runtime_continuation(
            project_id,
            score_id,
            scheduler=scheduler,
            now_ms=lambda: 0,
        )
        assert moved.job_id != ready.job_id
        asyncio.run(stored[1])
        current = get_adaptive_runtime_continuation(project_id, score_id)
        assert current is not None
        assert current.job_id == moved.job_id
        assert current.source == "fallback"

        def broken(plan, *, prefix_composition, seed):
            raise SymbolicCompositionGenerateError("blocked", code="symbolic_generate_failed")

        install_continuation_model(broken)
        monkeypatch.setenv("ADAPTIVE_CONTINUATION_DEADLINE_MS", "0")
        asyncio.run(stored[2])
        monkeypatch.setenv("ADAPTIVE_CONTINUATION_DEADLINE_MS", "10000")
        maintain_adaptive_runtime_continuation(
            project_id,
            score_id,
            scheduler=scheduler,
            now_ms=lambda: 1,
        )
        asyncio.run(stored[3])
        failed = get_adaptive_runtime_continuation(project_id, score_id)
        assert failed is not None
        assert failed.source == "fallback"
        assert any(item.code == "continuation_model_failed" for item in failed.warnings)
        playback = client.get(
            f"/projects/{project_id}/adaptive-scores/{score_id}/playback"
        )
        assert playback.json()["transport"] == "playing"
        assert playback.json()["instructions"]["stop"] is False
    finally:
        reset_continuation_model()

    messages = [
        record.getMessage()
        for record in caplog.records
        if record.name.startswith("app.services.adaptive_runtime_continuation")
    ]
    blob = " ".join(messages)
    assert "Am7" not in blob
    assert "events" not in blob
    stop_adaptive_runtime_continuation(project_id, score_id)
    assert get_adaptive_runtime_continuation(project_id, score_id) is None
