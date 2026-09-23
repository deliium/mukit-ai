"""Tests for live_harmony_context active span lookup."""

from __future__ import annotations

from app.services.live_harmony_context import (
    active_harmony_at_tick,
    reset_live_harmony_empty_warn_count_for_tests,
)


def test_active_harmony_at_tick_hit_and_miss() -> None:
    reset_live_harmony_empty_warn_count_for_tests()
    composition = {
        "harmony": [
            {"start_tick": 0, "duration_ticks": 1920, "chord": "Cmaj7"},
            {"start_tick": 1920, "duration_ticks": 1920, "chord": "Dm7"},
        ]
    }
    hit = active_harmony_at_tick(composition, 100)
    assert hit.symbol == "Cmaj7"
    assert hit.start_tick == 0

    second = active_harmony_at_tick(composition, 1920)
    assert second.symbol == "Dm7"

    miss = active_harmony_at_tick(composition, 99999)
    assert miss.symbol is None


def test_active_harmony_empty_composition() -> None:
    reset_live_harmony_empty_warn_count_for_tests()
    empty = active_harmony_at_tick({"harmony": []}, 0)
    assert empty.symbol is None
    assert active_harmony_at_tick(None, 0).symbol is None
