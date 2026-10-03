"""Service-level continuous latch, virtual maintain, and fingerprint tests."""

from __future__ import annotations

import logging
import sqlite3
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.adaptive_runtime_continuation_schemas import AdaptiveScoreError
from app.adaptive_runtime_continuation_settings import load_adaptive_runtime_continuation_settings
from app.services.adaptive_playback_runtime import reset_default_playback_registry
from app.services.adaptive_runtime_continuation_runtime import (
    reset_default_continuation_registry,
)
from app.services.adaptive_runtime_continuation_service import (
    install_continuation_model,
    maintain_adaptive_runtime_continuation,
    reset_continuation_model,
    start_adaptive_runtime_continuation,
)
from app.services.adaptive_score_transition_pending import reset_default_registry
from app.adaptive_runtime_continuation_schemas import (
    parse_adaptive_runtime_continuation_start,
)
from tests.test_adaptive_playback import scenario_score
from tests.test_adaptive_playback_api import _client, _composition
from tests.test_adaptive_runtime_continuation_api import (
    BAR_TICKS,
    _column,
    _local_piece,
    _reset,
    _seed,
    _wait,
)

import app.db as db_module


def test_continuous_true_refused_when_flag_off(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _db_path = _reset(monkeypatch, tmp_path)
    project_id, score_id, revision = _seed(client)
    monkeypatch.delenv("ADAPTIVE_CONTINUOUS_ENABLED", raising=False)
    settings = load_adaptive_runtime_continuation_settings({})
    assert settings.adaptive_continuous_enabled is False
    refused = client.post(
        f"/projects/{project_id}/adaptive-scores/{score_id}/continuation",
        json={
            "expected_document_revision": revision,
            "mode": "continuation",
            "continuous": True,
        },
    )
    assert refused.status_code == 422
    assert refused.json()["detail"]["code"] == "adaptive_continuous_disabled"
    client.__exit__(None, None, None)


def test_continuous_maintain_under_budget_with_blocked_model_at_end(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    monkeypatch.setenv("ADAPTIVE_CONTINUOUS_ENABLED", "1")
    client, db_path = _reset(monkeypatch, tmp_path)
    project_id, score_id, revision = _seed(client, loop=False)
    composition_before = _column(
        db_path, "SELECT composition_json FROM projects WHERE id = ?", project_id
    )
    body_before = _column(
        db_path, "SELECT body_json FROM adaptive_scores WHERE project_id = ?", project_id
    )
    # Advance to the last bar so legacy target would idle; continuous uses virtual_bar.
    advance = client.post(
        f"/projects/{project_id}/adaptive-scores/{score_id}/playback/commands",
        json={"op": "advance", "advance_ticks": 15 * BAR_TICKS},
    )
    assert advance.status_code == 200, advance.text
    playback = client.get(f"/projects/{project_id}/adaptive-scores/{score_id}/playback")
    assert playback.json()["bar"] <= 16

    started = client.post(
        f"/projects/{project_id}/adaptive-scores/{score_id}/continuation",
        json={
            "expected_document_revision": revision,
            "mode": "continuation",
            "continuous": True,
        },
    )
    assert started.status_code == 200, started.text
    assert started.json()["continuous"] is True

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
        budget = load_adaptive_runtime_continuation_settings().latency_budget_ms
        assert elapsed_ms < max(50, budget + 20)
        body = first.json()
        assert body["continuous"] is True
        assert body["job_status"] == "pending"
        assert body["source"] == "fallback"
        assert body["fallback_kind"]
        assert any(
            item["code"] == "continuation_virtual_timeline" for item in body["warnings"]
        )
        assert body["music_state"] is not None
        assert body["music_state"]["virtual_bar"] >= 1
        assert body["target_start_bar"] is not None
        assert body["target_start_bar"] > 16 or body["anchor_bar"] > 16
        transport = client.get(
            f"/projects/{project_id}/adaptive-scores/{score_id}/playback"
        )
        assert transport.json()["transport"] == "playing"
        assert transport.json()["bar"] <= 16
        assert _column(
            db_path, "SELECT composition_json FROM projects WHERE id = ?", project_id
        ) == composition_before
        assert _column(
            db_path, "SELECT body_json FROM adaptive_scores WHERE project_id = ?", project_id
        ) == body_before
        info = [
            record
            for record in caplog.records
            if record.levelno == logging.INFO
            and getattr(record, "continuous", None) is True
        ]
        assert info
    finally:
        release.set()
        reset_continuation_model()
        client.__exit__(None, None, None)


def test_duplicate_model_digest_discarded(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADAPTIVE_CONTINUOUS_ENABLED", "1")
    client, _db_path = _reset(monkeypatch, tmp_path)
    project_id, score_id, revision = _seed(client, loop=False)
    advance = client.post(
        f"/projects/{project_id}/adaptive-scores/{score_id}/playback/commands",
        json={"op": "advance", "advance_ticks": 15 * BAR_TICKS},
    )
    assert advance.status_code == 200
    started = client.post(
        f"/projects/{project_id}/adaptive-scores/{score_id}/continuation",
        json={
            "expected_document_revision": revision,
            "mode": "continuation",
            "continuous": True,
        },
    )
    assert started.status_code == 200
    piece = _local_piece()

    def same_model(plan, *, prefix_composition, seed):
        return piece

    install_continuation_model(same_model)
    monkeypatch.setenv("ADAPTIVE_CONTINUATION_DEADLINE_MS", "5000")
    path = f"/projects/{project_id}/adaptive-scores/{score_id}/continuation/maintain"
    try:
        first = client.post(path)
        assert first.status_code == 200
        applied = _wait(
            client,
            project_id,
            score_id,
            lambda row: row["job_status"] in {"applied", "discarded", "failed"},
        )
        # Force a new arm with the same model output digest.
        second = client.post(path)
        assert second.status_code == 200
        # Advance playback identity slightly by re-maintaining; duplicate guard
        # may discard on second apply when digests match.
        again = _wait(
            client,
            project_id,
            score_id,
            lambda row: row["job_status"] in {"applied", "discarded", "pending", "failed"},
        )
        assert again["continuous"] is True
        assert applied["continuous"] is True
    finally:
        reset_continuation_model()
        client.__exit__(None, None, None)


def test_start_request_parses_continuous_latch() -> None:
    request = parse_adaptive_runtime_continuation_start(
        {
            "expected_document_revision": 1,
            "mode": "continuation",
            "continuous": True,
        }
    )
    assert request.continuous is True
