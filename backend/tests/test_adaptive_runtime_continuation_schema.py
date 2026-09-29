"""Schema tests for adaptive runtime continuation documents."""

from __future__ import annotations

import logging

import pytest

from app.adaptive_runtime_continuation_schemas import (
    AdaptiveScoreError,
    continuation_not_running_error,
    parse_adaptive_runtime_buffer,
    parse_adaptive_runtime_context,
    parse_adaptive_runtime_continuation,
    parse_adaptive_runtime_continuation_start,
)
from app.adaptive_runtime_continuation_settings import (
    load_adaptive_runtime_continuation_settings,
)


def _context(**overrides: object) -> dict:
    body: dict = {
        "schema_version": "adaptive.runtime.context.v1",
        "theme_ids": ["motif-a"],
        "harmony_tail": "Cmaj7",
        "harmony_chord_count": 1,
        "recent_start_bar": 1,
        "recent_end_bar": 1,
        "repetition_count": 0,
        "energy": [0.2],
    }
    body.update(overrides)
    return body


def _snapshot(**overrides: object) -> dict:
    body: dict = {
        "schema_version": "adaptive.runtime.continuation.v1",
        "continuation_id": "arcn_0123abcd",
        "job_id": "arcj_0123abcd",
        "mode": "continuation",
        "anchor_bar": 1,
        "reserved_start_bar": 1,
        "reserved_end_bar": 5,
        "target_start_bar": 6,
        "target_end_bar": 13,
        "deadline_tick": 1920,
        "fallback_kind": "reuse_loop",
        "source": "fallback",
        "job_status": "pending",
        "applicable": True,
        "audible": False,
        "runtime_state_id": "state-exploration",
        "intensity": 0.4,
        "context": _context(),
        "warnings": [],
        "telemetry": {
            "arm_count": 1,
            "model_apply_count": 0,
            "late_discard_count": 0,
            "failure_count": 0,
            "fallback_count": 1,
        },
        "document_revision": 1,
    }
    body.update(overrides)
    return body


def _note(index: int) -> dict:
    return {
        "type": "note",
        "pitch": "C4",
        "start_tick": index,
        "duration_ticks": 1,
        "velocity": 80,
    }


def test_extra_field_boolean_intensity_and_sixth_mode(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    with pytest.raises(AdaptiveScoreError) as extra:
        parse_adaptive_runtime_continuation(_snapshot(scene="forest"))
    assert extra.value.code == "adaptive_score_invalid"
    assert extra.value.http_status == 422
    with pytest.raises(AdaptiveScoreError) as boolean:
        parse_adaptive_runtime_continuation(_snapshot(intensity=True))
    assert boolean.value.code == "adaptive_score_invalid"
    assert boolean.value.details["field"] == "intensity"
    with pytest.raises(AdaptiveScoreError) as mode:
        parse_adaptive_runtime_continuation_start(
            {"expected_document_revision": 1, "mode": "improvisation"}
        )
    assert mode.value.code == "adaptive_score_invalid"
    parsed = parse_adaptive_runtime_continuation(_snapshot())
    assert parsed.audible is False
    assert parsed.context.harmony_tail == "Cmaj7"


def test_event_list_on_the_snapshot_is_forbidden(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    with pytest.raises(AdaptiveScoreError) as captured:
        parse_adaptive_runtime_continuation(_snapshot(events=[_note(0)]))
    assert captured.value.code == "embedded_note_material"
    assert captured.value.details["field"] == "events"
    blob = " ".join(
        f"{record.getMessage()} {getattr(record, 'code', '')} {getattr(record, 'field', '')} {getattr(record, 'model', '')}"
        for record in caplog.records
    )
    assert "events" not in blob or "field" in blob
    assert "[" not in blob
    assert "Cmaj7" not in blob
    assert "C4" not in blob
    debug_records = [record for record in caplog.records if record.levelno == logging.DEBUG]
    assert debug_records
    assert all(not hasattr(record, "events") or getattr(record, "events") in {None, "events"} for record in debug_records)


def test_buffer_events_over_512_are_rejected() -> None:
    body = {
        "schema_version": "adaptive.runtime.buffer.v1",
        "continuation_id": "arcn_0123abcd",
        "job_id": "arcj_0123abcd",
        "source": "fallback",
        "fallback_kind": "accompaniment",
        "events": [_note(index) for index in range(513)],
        "harmony": [],
    }
    with pytest.raises(AdaptiveScoreError) as captured:
        parse_adaptive_runtime_buffer(body)
    assert captured.value.code == "adaptive_score_too_large"
    assert captured.value.details["field"] == "events"


def test_context_energy_over_eight_and_duplicate_theme_ids() -> None:
    with pytest.raises(AdaptiveScoreError) as energy:
        parse_adaptive_runtime_context(_context(energy=[0.1] * 9))
    assert energy.value.code == "adaptive_score_too_large"
    assert energy.value.details["field"] == "energy"
    with pytest.raises(AdaptiveScoreError) as themes:
        parse_adaptive_runtime_context(_context(theme_ids=["motif-a", "motif-a"]))
    assert themes.value.code == "adaptive_score_invalid"
    assert themes.value.details["field"] == "theme_ids"


def test_deadline_env_that_is_not_an_integer_keeps_the_defaults(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    settings = load_adaptive_runtime_continuation_settings(
        {"ADAPTIVE_CONTINUATION_DEADLINE_MS": "soon"}
    )
    assert settings.play_bars == 5
    assert settings.generate_bars == 8
    assert settings.deadline_ms == 250
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert warnings
    assert getattr(warnings[0], "setting") == "ADAPTIVE_CONTINUATION_DEADLINE_MS"
    assert "ADAPTIVE_CONTINUATION_DEADLINE_MS" in warnings[0].getMessage() or getattr(
        warnings[0], "setting"
    ) == "ADAPTIVE_CONTINUATION_DEADLINE_MS"


def test_continuation_not_running_is_http_404() -> None:
    error = continuation_not_running_error()
    assert error.code == "continuation_not_running"
    assert error.http_status == 404


def test_future_schema_version_on_start_is_422() -> None:
    with pytest.raises(AdaptiveScoreError) as captured:
        parse_adaptive_runtime_continuation_start(
            {
                "schema_version": "adaptive.runtime.continuation.v2",
                "expected_document_revision": 1,
                "mode": "continuation",
            }
        )
    assert captured.value.code == "unsupported_schema_version"
    assert captured.value.http_status == 422
