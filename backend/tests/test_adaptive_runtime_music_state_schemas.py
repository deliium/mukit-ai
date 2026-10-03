"""Schema tests for adaptive.runtime.music_state.v1 and continuous latch fields."""

from __future__ import annotations

import logging

import pytest

from app.adaptive_runtime_continuation_schemas import (
    AdaptiveScoreError,
    parse_adaptive_runtime_continuation,
    parse_adaptive_runtime_continuation_start,
)
from app.adaptive_runtime_continuation_settings import (
    load_adaptive_runtime_continuation_settings,
)
from app.adaptive_runtime_music_state_schemas import (
    empty_music_state,
    parse_adaptive_runtime_music_state,
    project_legacy_runtime_context,
)


def _music_state(**overrides: object) -> dict:
    body: dict = {
        "schema_version": "adaptive.runtime.music_state.v1",
        "active_theme_ids": ["theme-a"],
        "motif_usage": [
            {"motif_id": "motif-a", "use_count": 1, "last_virtual_bar": 2},
        ],
        "harmony_trajectory": ["Cmaj7"],
        "repetition_history": [{"digest16": "0123456789abcdef", "count": 1}],
        "energy": [0.4],
        "tension": [0.3],
        "orchestration_history": ["fedcba9876543210"],
        "runtime_state_id": "state-explore",
        "summary_digest": None,
        "virtual_bar": 2,
        "guard_flags": [],
    }
    body.update(overrides)
    return body


def _context() -> dict:
    return {
        "schema_version": "adaptive.runtime.context.v1",
        "theme_ids": ["theme-a"],
        "harmony_tail": "Cmaj7",
        "harmony_chord_count": 1,
        "recent_start_bar": 1,
        "recent_end_bar": 1,
        "repetition_count": 0,
        "energy": [0.2],
    }


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
        "runtime_state_id": "state-explore",
        "intensity": 0.4,
        "context": _context(),
        "continuous": False,
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


def test_minimal_music_state_accepted() -> None:
    parsed = parse_adaptive_runtime_music_state(
        {"schema_version": "adaptive.runtime.music_state.v1"}
    )
    assert parsed.virtual_bar == 1
    assert parsed.active_theme_ids == []
    assert parsed.guard_flags == []
    empty = empty_music_state(virtual_bar=3)
    assert empty.virtual_bar == 3


def test_oversized_rings_and_events_key_rejected(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    with pytest.raises(AdaptiveScoreError) as themes:
        parse_adaptive_runtime_music_state(
            _music_state(active_theme_ids=[f"t{i}" for i in range(17)])
        )
    assert themes.value.code == "adaptive_score_too_large"
    assert themes.value.details["field"] == "active_theme_ids"
    with pytest.raises(AdaptiveScoreError) as events:
        parse_adaptive_runtime_music_state(_music_state(events=[{"type": "note"}]))
    assert events.value.code == "embedded_note_material"
    assert events.value.details["field"] == "events"
    debug_blob = " ".join(record.getMessage() for record in caplog.records)
    assert "Cmaj7" not in debug_blob


def test_projection_fills_legacy_context() -> None:
    state = parse_adaptive_runtime_music_state(
        _music_state(
            active_theme_ids=["theme-a", "theme-b"],
            harmony_trajectory=["Am", "Cmaj7"],
            energy=[0.1, 0.5],
            repetition_history=[{"digest16": "0123456789abcdef", "count": 4}],
            virtual_bar=9,
        )
    )
    context = project_legacy_runtime_context(state)
    assert context.theme_ids == ["theme-a", "theme-b"]
    assert context.harmony_tail == "Cmaj7"
    assert context.harmony_chord_count == 2
    assert context.repetition_count == 4
    assert context.energy == [0.1, 0.5]
    assert context.recent_start_bar == 9
    assert context.recent_end_bar == 9
    assert not hasattr(context, "virtual_bar")
    dumped = context.model_dump()
    assert "motif_usage" not in dumped
    assert "guard_flags" not in dumped


def test_snapshot_accepts_sibling_music_state_and_refuses_unknown() -> None:
    parsed = parse_adaptive_runtime_continuation(
        _snapshot(music_state=_music_state(), continuous=True)
    )
    assert parsed.continuous is True
    assert parsed.music_state is not None
    assert parsed.music_state.virtual_bar == 2
    assert parsed.context.theme_ids == ["theme-a"]
    with pytest.raises(AdaptiveScoreError) as unknown:
        parse_adaptive_runtime_continuation(_snapshot(scene="forest"))
    assert unknown.value.code == "adaptive_score_invalid"
    with pytest.raises(AdaptiveScoreError) as nested_events:
        parse_adaptive_runtime_continuation(
            _snapshot(music_state=_music_state(events=[{"type": "note"}]))
        )
    assert nested_events.value.code == "embedded_note_material"


def test_start_body_accepts_continuous_bool() -> None:
    defaulted = parse_adaptive_runtime_continuation_start(
        {"expected_document_revision": 1, "mode": "continuation"}
    )
    assert defaulted.continuous is False
    latched = parse_adaptive_runtime_continuation_start(
        {
            "expected_document_revision": 1,
            "mode": "continuation",
            "continuous": True,
        }
    )
    assert latched.continuous is True
    with pytest.raises(AdaptiveScoreError) as captured:
        parse_adaptive_runtime_continuation_start(
            {
                "expected_document_revision": 1,
                "mode": "continuation",
                "continuous": "true",
            }
        )
    assert captured.value.code == "adaptive_score_invalid"
    assert captured.value.details["field"] == "continuous"


def test_continuous_settings_default_off_and_log_booleans(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    off = load_adaptive_runtime_continuation_settings({})
    assert off.adaptive_continuous_enabled is False
    assert off.adaptive_engine_continuous_enabled is False
    on = load_adaptive_runtime_continuation_settings(
        {
            "ADAPTIVE_CONTINUOUS_ENABLED": "1",
            "ADAPTIVE_ENGINE_CONTINUOUS_ENABLED": "yes",
        }
    )
    assert on.adaptive_continuous_enabled is True
    assert on.adaptive_engine_continuous_enabled is True
    info = [record for record in caplog.records if record.levelno == logging.INFO]
    assert info
    latest = info[-1]
    assert getattr(latest, "adaptive_continuous_enabled") is True
    assert getattr(latest, "adaptive_engine_continuous_enabled") is True
