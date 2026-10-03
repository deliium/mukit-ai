"""Feedback reducer unit tests."""

from __future__ import annotations

from app.services.ardour_feedback_state import ArdourFeedbackState


def test_transport_and_strip_reduce() -> None:
    state = ArdourFeedbackState(stale_timeout_ms=1000)
    state.mark_awaiting_feedback()
    state.apply_message("/heartbeat", [1], now=100.0)
    assert state.connection_state == "connected"
    state.apply_message("/transport_play", [1], now=100.1)
    assert state.transport_playing is True
    state.apply_message("/transport_stop", [1], now=100.2)
    assert state.transport_playing is False
    state.apply_message("/strip/name", [1, "Kick"], now=100.3)
    state.apply_message("/strip/fader", [1, 0.4], now=100.4)
    state.apply_message("/strip/pan_stereo_position", [1, 0.6], now=100.5)
    state.apply_message("/strip/mute", [1, 1], now=100.6)
    state.apply_message("/strip/solo", [1, 0], now=100.7)
    state.apply_message("/rec_enable_toggle", [1], now=100.8)
    state.apply_message("/select/ssid", [1], now=100.9)
    assert state.record_armed is True
    assert state.selected_ssid == 1
    strips = state.strips_list()
    assert strips[0].name == "Kick"
    assert strips[0].fader == 0.4
    assert strips[0].mute == 1


def test_stale_after_timeout() -> None:
    state = ArdourFeedbackState(stale_timeout_ms=500)
    state.mark_awaiting_feedback()
    state.apply_message("/heartbeat", [1], now=1.0)
    assert state.connection_state == "connected"
    assert state.is_stale(now=1.6) is True
    state.refresh_connection_from_age(now=1.6)
    assert state.connection_state == "stale"
    assert state.feedback_age_ms(now=1.6) == 600
