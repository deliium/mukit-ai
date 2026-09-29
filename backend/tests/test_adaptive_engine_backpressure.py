"""Burst then one-slot replacement. No sample text is logged."""

from __future__ import annotations

import logging

import pytest

from app.services.adaptive_engine_backpressure import (
    EnginePressure,
    drain_context,
    offer_context,
    try_command,
)


def test_context_burst_keeps_only_the_newest_sample(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    state: EnginePressure[dict[str, float]] = EnginePressure.create(
        command_hz=8,
        command_burst=2,
        context_hz=20,
        context_burst=3,
        now_ms=1_000,
    )
    accepted = [
        offer_context(state, {"danger": float(index)}, 1_000) for index in range(3)
    ]
    assert all(item.accepted and not item.coalesced for item in accepted)
    first = offer_context(state, {"danger": 0.2}, 1_000)
    assert first.coalesced and not first.accepted
    newer = offer_context(state, {"danger": 0.9}, 1_000)
    assert newer.coalesced and newer.replaced
    assert state.dropped_count == 1
    assert drain_context(state, 1_000) is None
    drained = drain_context(state, 1_050)
    assert drained == {"danger": 0.9}
    assert drain_context(state, 1_100) is None
    assert "0.9" not in caplog.text


def test_command_bucket_refuses_past_the_burst() -> None:
    state: EnginePressure[str] = EnginePressure.create(
        command_hz=8,
        command_burst=2,
        context_hz=20,
        context_burst=1,
        now_ms=0,
    )
    assert try_command(state, 0).allowed
    assert try_command(state, 0).allowed
    refused = try_command(state, 0)
    assert not refused.allowed
    assert refused.retry_after_ms is not None and refused.retry_after_ms > 0
    restored = try_command(state, 125)
    assert restored.allowed
