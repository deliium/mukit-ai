"""Part A acceptance: continuous generative music Goal criteria."""

from __future__ import annotations

import logging
import threading
import time

import pytest

from app.adaptive_runtime_continuation_settings import (
    load_adaptive_runtime_continuation_settings,
)
from app.adaptive_runtime_music_state_schemas import (
    MUSIC_STATE_CAP_THEME_IDS,
    parse_adaptive_runtime_music_state,
)
from app.adaptive_score_schemas import AdaptiveScoreError
from app.composition_plan_schemas import CompositionPlan
from app.composition_schemas import CompositionV2Track
from app.services.adaptive_runtime_continuation import (
    continuation_seed,
    virtual_bar_deadline_tick,
)
from app.services.adaptive_runtime_continuation_fallback import realize_fallback
from app.services.adaptive_runtime_continuation_service import (
    install_continuation_model,
    reset_continuation_model,
)
from tests.test_adaptive_runtime_continuation_api import (
    BAR_TICKS,
    _column,
    _local_piece,
    _reset,
    _seed,
    _wait,
)


def _pitches(events) -> list[str]:
    return [str(event.pitch) for event in events]


def test_continuous_at_end_virtual_bar_sibling_and_fingerprints(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Goal: continuous at end uses virtual_bar; MusicState sibling; no score write."""
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

    advance = client.post(
        f"/projects/{project_id}/adaptive-scores/{score_id}/playback/commands",
        json={"op": "advance", "advance_ticks": 15 * BAR_TICKS},
    )
    assert advance.status_code == 200, advance.text
    playback = client.get(
        f"/projects/{project_id}/adaptive-scores/{score_id}/playback"
    ).json()
    assert playback["bar"] <= 16
    assert playback["transport"] == "playing"

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
        assert body["source"] == "fallback"
        assert body["fallback_kind"]
        assert body["job_status"] == "pending"
        assert "music_state" in body
        assert body["music_state"] is not None
        assert body["music_state"]["schema_version"] == "adaptive.runtime.music_state.v1"
        assert body["music_state"]["virtual_bar"] >= 1
        assert "events" not in body["music_state"]
        assert "context" in body
        assert body["context"]["schema_version"] == "adaptive.runtime.context.v1"
        assert any(
            item["code"] == "continuation_virtual_timeline" for item in body["warnings"]
        )
        # Idle past-end code must not be the sole story when continuous.
        idle_only = (
            body["target_start_bar"] is None
            and any(
                item["code"] == "continuation_window_past_end"
                for item in body["warnings"]
            )
        )
        assert idle_only is False
        transport = client.get(
            f"/projects/{project_id}/adaptive-scores/{score_id}/playback"
        ).json()
        assert transport["transport"] == "playing"
        assert transport["bar"] <= 16
        assert _column(
            db_path, "SELECT composition_json FROM projects WHERE id = ?", project_id
        ) == composition_before
        assert _column(
            db_path, "SELECT body_json FROM adaptive_scores WHERE project_id = ?", project_id
        ) == body_before
        for record in caplog.records:
            extras = getattr(record, "__dict__", {})
            assert "events" not in extras or extras.get("events") in (None, [], ())
    finally:
        release.set()
        reset_continuation_model()
        client.__exit__(None, None, None)


def test_continuous_loop_wrapping_at_end_uses_virtual_bar() -> None:
    """Looping at end anchors on virtual_bar (playback.bar stays ≤ bar_count)."""
    from app.adaptive_runtime_continuation_schemas import AdaptiveRuntimeContextV1
    from app.services.adaptive_runtime_continuation import (
        WARNING_VIRTUAL_TIMELINE,
        plan_runtime_window,
    )

    context = AdaptiveRuntimeContextV1(
        schema_version="adaptive.runtime.context.v1",
        theme_ids=[],
        harmony_tail=None,
        harmony_chord_count=0,
        recent_start_bar=4,
        recent_end_bar=4,
        repetition_count=0,
        energy=[0.2],
    )
    window = plan_runtime_window(
        bar=4,
        bar_count=16,
        loop_start_bar=1,
        loop_end_bar=4,
        loop_enabled=True,
        intensity=0.2,
        state_id="state-exploration",
        context=context,
        deadline_tick=4 * BAR_TICKS,
        continuous=True,
        virtual_bar=17,
        playback_at_end=True,
        loop_wrapping_at_end=True,
        last_compiled_bar_end_tick=16 * BAR_TICKS,
        ticks_per_bar_assumed=BAR_TICKS,
    )
    assert window.anchor_bar == 17
    assert window.target_start_bar is not None
    assert window.target_start_bar > 16
    assert WARNING_VIRTUAL_TIMELINE in window.warnings
    assert window.job_status != "idle"


def test_flag_off_preserves_legacy_past_end_idle(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ADAPTIVE_CONTINUOUS_ENABLED", raising=False)
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
            "continuous": False,
        },
    )
    assert started.status_code == 200, started.text
    maintained = client.post(
        f"/projects/{project_id}/adaptive-scores/{score_id}/continuation/maintain"
    )
    try:
        assert maintained.status_code == 200, maintained.text
        body = maintained.json()
        assert body["continuous"] is False
        assert body.get("music_state") in (None, {})
        assert any(
            item["code"] == "continuation_window_past_end" for item in body["warnings"]
        )
        refused = client.post(
            f"/projects/{project_id}/adaptive-scores/{score_id}/continuation",
            json={
                "expected_document_revision": revision,
                "mode": "continuation",
                "continuous": True,
            },
        )
        # Session already running — stop first then refuse continuous latch.
        client.delete(
            f"/projects/{project_id}/adaptive-scores/{score_id}/continuation"
        )
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
    finally:
        client.__exit__(None, None, None)


def test_music_state_caps_and_note_refuse() -> None:
    with pytest.raises(AdaptiveScoreError) as exc:
        parse_adaptive_runtime_music_state(
            {
                "schema_version": "adaptive.runtime.music_state.v1",
                "active_theme_ids": [f"t{i}" for i in range(MUSIC_STATE_CAP_THEME_IDS + 1)],
                "motif_usage": [],
                "harmony_trajectory": [],
                "repetition_history": [],
                "energy": [],
                "tension": [],
                "orchestration_history": [],
                "virtual_bar": 1,
                "guard_flags": [],
            }
        )
    assert exc.value.code == "adaptive_score_too_large"
    with pytest.raises(AdaptiveScoreError) as notes:
        parse_adaptive_runtime_music_state(
            {
                "schema_version": "adaptive.runtime.music_state.v1",
                "active_theme_ids": [],
                "motif_usage": [],
                "harmony_trajectory": [],
                "repetition_history": [],
                "energy": [],
                "tension": [],
                "orchestration_history": [],
                "virtual_bar": 1,
                "guard_flags": [],
                "events": [{"pitch": "C4"}],
            }
        )
    assert notes.value.code == "embedded_note_material"


def test_seed_stability_same_accompaniment_pitches() -> None:
    plan = CompositionPlan.model_validate(
        {
            "schema_version": "composition.plan.v1",
            "form": {
                "tempo": 120,
                "key": "C major",
                "time_signature": "4/4",
                "bar_count": 4,
                "sections": [{"type": "verse", "start_bar": 1, "bar_count": 4}],
                "instrumentation": ["piano"],
            },
        }
    )
    seed = continuation_seed(17, "continuation", "state-exploration")
    track = CompositionV2Track(
        id="track-1",
        name="Piano",
        instrument="piano",
        role="melody",
        channel=1,
        midi_program=0,
        events=[],
    )
    first = realize_fallback(
        ("accompaniment",),
        loop_enabled=False,
        loop_start_bar=None,
        loop_end_bar=None,
        plan=plan,
        seed=seed,
        relative_notes=None,
        anchor_midi=60,
        destination_track=track,
        composition=None,
        job_id="arcj_aaaaaaaaaaaaaaaa",
        deadline_tick=16 * BAR_TICKS,
        ticks_per_bar=BAR_TICKS,
        generate_bars=4,
        target_span_ticks=4 * BAR_TICKS,
    )
    second = realize_fallback(
        ("accompaniment",),
        loop_enabled=False,
        loop_start_bar=None,
        loop_end_bar=None,
        plan=plan,
        seed=seed,
        relative_notes=None,
        anchor_midi=60,
        destination_track=track,
        composition=None,
        job_id="arcj_bbbbbbbbbbbbbbbb",
        deadline_tick=16 * BAR_TICKS,
        ticks_per_bar=BAR_TICKS,
        generate_bars=4,
        target_span_ticks=4 * BAR_TICKS,
    )
    assert first.fallback_kind == "accompaniment"
    assert _pitches(first.events) == _pitches(second.events)
    assert _pitches(first.events)


def test_spa_past_end_tick_mapping_parity() -> None:
    """Constant-tempo virtual deadline matches SPA past-end seconds formula."""
    last_end = 16 * BAR_TICKS
    ticks_per_bar = BAR_TICKS
    target_start = 17
    deadline = virtual_bar_deadline_tick(
        target_start_bar=target_start,
        bar_count=16,
        last_compiled_bar_end_tick=last_end,
        ticks_per_bar_assumed=ticks_per_bar,
    )
    assert deadline == last_end  # first virtual bar starts at authored end
    later = virtual_bar_deadline_tick(
        target_start_bar=19,
        bar_count=16,
        last_compiled_bar_end_tick=last_end,
        ticks_per_bar_assumed=ticks_per_bar,
    )
    assert later == last_end + 2 * ticks_per_bar
    # SPA: seconds beyond duration = (tick - duration) * (60 / tempo / tpq)
    tempo = 120
    tpq = 480
    duration_ticks = last_end
    virtual_tick = later
    seconds_past = (virtual_tick - duration_ticks) * (60 / tempo / tpq)
    assert abs(seconds_past - (2 * ticks_per_bar / tpq * 60 / tempo)) < 1e-9


def test_blocked_model_late_discard_keeps_transport(
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
    release = threading.Event()

    def blocked(plan, *, prefix_composition, seed):
        release.wait(timeout=3)
        return _local_piece()

    install_continuation_model(blocked)
    monkeypatch.setenv("ADAPTIVE_CONTINUATION_DEADLINE_MS", "10000")
    path = f"/projects/{project_id}/adaptive-scores/{score_id}/continuation/maintain"
    try:
        first = client.post(path)
        assert first.status_code == 200
        assert first.json()["job_status"] == "pending"
        monkeypatch.setenv("ADAPTIVE_CONTINUATION_DEADLINE_MS", "0")
        release.set()
        discarded = _wait(
            client,
            project_id,
            score_id,
            lambda row: row["job_status"] == "discarded",
        )
        assert discarded["job_status"] == "discarded"
        assert any(item["code"] == "continuation_late" for item in discarded["warnings"])
        transport = client.get(
            f"/projects/{project_id}/adaptive-scores/{score_id}/playback"
        ).json()
        assert transport["transport"] == "playing"
        assert transport["bar"] <= 16
    finally:
        release.set()
        reset_continuation_model()
        client.__exit__(None, None, None)
