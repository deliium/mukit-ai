"""Pure MusicState update / compress / guard tests."""

from __future__ import annotations

import logging

import pytest

from app.adaptive_runtime_music_state_schemas import (
    MUSIC_STATE_CAP_HARMONY_TRAJECTORY,
    MUSIC_STATE_CAP_THEME_IDS,
    empty_music_state,
)
from app.services.adaptive_runtime_music_state import (
    FALLBACK_MOTIF,
    FALLBACK_REUSE_LOOP,
    compress_music_state,
    evaluate_continuous_guards,
    music_state_summary_digest,
    next_virtual_bar,
    update_music_state,
)


def test_rings_drop_oldest_at_caps() -> None:
    state = empty_music_state(virtual_bar=1)
    for index in range(MUSIC_STATE_CAP_THEME_IDS + 4):
        state = update_music_state(
            state,
            prefix_digest=None,
            theme_ids=[f"theme-{index:02d}"],
            harmony_label=None,
            intensity=0.2,
            orchestration_fingerprint=None,
            runtime_state_id="state-a",
            virtual_bar=index + 1,
        )
    assert len(state.active_theme_ids) <= MUSIC_STATE_CAP_THEME_IDS

    harmony_state = empty_music_state(virtual_bar=1)
    for index in range(MUSIC_STATE_CAP_HARMONY_TRAJECTORY + 3):
        harmony_state = update_music_state(
            harmony_state,
            prefix_digest=None,
            theme_ids=[],
            harmony_label=f"L{index % 10}",
            intensity=0.1,
            orchestration_fingerprint=None,
            runtime_state_id=None,
            virtual_bar=1,
        )
    assert len(harmony_state.harmony_trajectory) == MUSIC_STATE_CAP_HARMONY_TRAJECTORY
    assert harmony_state.harmony_trajectory[0] == "L3"


def test_repetition_pressure_at_threshold(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    state = empty_music_state(virtual_bar=5)
    digest = "0123456789abcdef"
    for _ in range(3):
        state = update_music_state(
            state,
            prefix_digest=digest,
            theme_ids=["theme-a"],
            harmony_label="Cmaj7",
            intensity=0.5,
            orchestration_fingerprint=None,
            runtime_state_id="state-a",
            virtual_bar=5,
        )
    assert "repetition_pressure" in state.guard_flags
    guards = evaluate_continuous_guards(state)
    assert guards.fallback_order[0] == FALLBACK_MOTIF
    assert guards.seed_bump >= 1
    assert any(
        "repetition_pressure" in getattr(record, "guard_flags", [])
        for record in caplog.records
    )


def test_duplicate_digest_hard_reject() -> None:
    state = empty_music_state(virtual_bar=2)
    guards = evaluate_continuous_guards(
        state,
        last_applied_digest="aaaaaaaaaaaaaaaa",
        candidate_digest="aaaaaaaaaaaaaaaa",
    )
    assert guards.hard_reject_duplicate is True
    ok = evaluate_continuous_guards(
        state,
        last_applied_digest="aaaaaaaaaaaaaaaa",
        candidate_digest="bbbbbbbbbbbbbbbb",
    )
    assert ok.hard_reject_duplicate is False


def test_summary_digest_stable_for_same_canonical_state() -> None:
    first = update_music_state(
        None,
        prefix_digest="0123456789abcdef",
        theme_ids=["theme-b", "theme-a"],
        motif_ids=["motif-a"],
        harmony_label="Am",
        intensity=0.4,
        tension=0.2,
        orchestration_fingerprint="fedcba9876543210",
        runtime_state_id="state-x",
        virtual_bar=3,
    )
    second = update_music_state(
        None,
        prefix_digest="0123456789abcdef",
        theme_ids=["theme-b", "theme-a"],
        motif_ids=["motif-a"],
        harmony_label="Am",
        intensity=0.4,
        tension=0.2,
        orchestration_fingerprint="fedcba9876543210",
        runtime_state_id="state-x",
        virtual_bar=3,
    )
    assert first.summary_digest == second.summary_digest
    assert music_state_summary_digest(first) == first.summary_digest
    recompressed = compress_music_state(first.model_copy(update={"summary_digest": None}))
    assert recompressed.summary_digest == first.summary_digest


def test_next_virtual_bar_deterministic() -> None:
    assert next_virtual_bar(current_virtual_bar=1, bar_count=8, advanced=False) == 9
    assert next_virtual_bar(current_virtual_bar=9, bar_count=8, advanced=True) == 10
    assert next_virtual_bar(current_virtual_bar=12, bar_count=8, advanced=False) == 12


def test_theme_drift_and_harmonic_dead_end_reorder() -> None:
    drifted = update_music_state(
        None,
        prefix_digest=None,
        theme_ids=["foreign-theme"],
        motif_ids=["motif-a"],
        harmony_label=None,
        intensity=0.3,
        orchestration_fingerprint=None,
        runtime_state_id="state-a",
        virtual_bar=4,
        score_theme_ids=["score-theme"],
    )
    assert "theme_drift" in drifted.guard_flags
    assert evaluate_continuous_guards(
        drifted,
        score_theme_ids=["score-theme"],
    ).fallback_order[0] == FALLBACK_MOTIF

    stuck = empty_music_state(virtual_bar=2)
    for _ in range(4):
        stuck = update_music_state(
            stuck,
            prefix_digest=None,
            theme_ids=["theme-a"],
            harmony_label="G7",
            intensity=0.2,
            orchestration_fingerprint=None,
            runtime_state_id="state-a",
            virtual_bar=2,
            score_theme_ids=["theme-a"],
        )
    assert "harmonic_dead_end" in stuck.guard_flags
    assert evaluate_continuous_guards(stuck).fallback_order[0] != FALLBACK_REUSE_LOOP
