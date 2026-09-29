"""Schema tests for adaptive.playback.runtime.v1."""

from __future__ import annotations

import logging

import pytest
from pydantic import ValidationError

from app.adaptive_playback_schemas import (
    ADAPTIVE_PLAYBACK_RUNTIME_SCHEMA,
    AdaptivePlaybackRuntimeV1,
    parse_adaptive_playback_command,
    parse_adaptive_playback_start,
)
from app.adaptive_score_schemas import ADAPTIVE_SCORE_ERROR_CODES, AdaptiveScoreError


def test_playback_not_running_is_a_domain_code() -> None:
    assert "playback_not_running" in ADAPTIVE_SCORE_ERROR_CODES


def test_boolean_tick_and_intensity_are_rejected(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    with pytest.raises(AdaptiveScoreError) as exc:
        parse_adaptive_playback_command({"op": "advance", "advance_ticks": True})
    assert exc.value.http_status == 422
    assert any(getattr(record, "field", None) == "advance_ticks" for record in caplog.records)
    with pytest.raises(AdaptiveScoreError):
        parse_adaptive_playback_command({"op": "set_intensity", "intensity": True})
    with pytest.raises(AdaptiveScoreError):
        parse_adaptive_playback_command({"op": "observe", "position_tick": False})


def test_start_requires_mode_and_revision() -> None:
    parsed = parse_adaptive_playback_start(
        {"expected_document_revision": 1, "mode": "simulation"}
    )
    assert parsed.mode == "simulation"
    with pytest.raises(AdaptiveScoreError):
        parse_adaptive_playback_start({"mode": "simulation"})


def test_snapshot_rejects_boolean_position() -> None:
    payload = {
        "schema_version": ADAPTIVE_PLAYBACK_RUNTIME_SCHEMA,
        "playback_id": "pbr_0123abcd",
        "mode": "simulation",
        "transport": "playing",
        "runtime_state_id": "state-exploration",
        "position_tick": True,
        "bar": 1,
        "beat": 1,
        "intensity": 0,
        "loop": {"enabled": False, "start_tick": 0, "end_tick": 0},
        "horizon_end_tick": 1920,
        "instructions": {
            "seek_tick": None,
            "loop": {"enabled": False, "start_tick": 0, "end_tick": 0},
            "stop": False,
        },
        "telemetry": {"step_count": 1, "rejected_request_count": 0, "last_event": "started"},
        "document_revision": 1,
    }
    with pytest.raises(ValidationError):
        AdaptivePlaybackRuntimeV1.model_validate(payload)
