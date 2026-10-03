"""Fake OSC transport open/send/close coverage."""

from __future__ import annotations

import pytest

from app.ardour_companion_schemas import ArdourCompanionError
from app.services.ardour_osc_paths import PATH_SET_SURFACE, PATH_STRIP_FADER, PATH_TRANSPORT_PLAY
from app.services.ardour_osc_transport import ArdourOscTransport


def test_fake_connect_sends_set_surface_and_gets_feedback() -> None:
    transport = ArdourOscTransport(
        host="127.0.0.1",
        osc_port=3819,
        feedback_port=8000,
        host_class="loopback",
        fake=True,
        stale_timeout_ms=3000,
    )
    try:
        transport.open()
        assert transport.fake_peer is not None
        assert PATH_SET_SURFACE in transport.fake_peer.paths_sent()
        assert transport.state.connection_state == "connected"
        assert transport.state.transport_playing is False
        assert len(transport.state.strips) == 2
        transport.send_message(PATH_TRANSPORT_PLAY, [])
        assert transport.state.transport_playing is True
        transport.send_message(PATH_STRIP_FADER, [1, 0.33])
        assert transport.state.strips[1].fader == pytest.approx(0.33)
    finally:
        transport.close()
    assert transport.state.connection_state == "disconnected"


def test_refuse_feedback_port_3819() -> None:
    transport = ArdourOscTransport(
        host="127.0.0.1",
        osc_port=3820,
        feedback_port=3819,
        host_class="loopback",
        fake=True,
        stale_timeout_ms=3000,
    )
    with pytest.raises(ArdourCompanionError) as exc:
        transport.open()
    assert exc.value.code == "ardour_bind_failed"
    transport.close()


def test_close_idempotent() -> None:
    transport = ArdourOscTransport(
        host="10.0.0.2",
        osc_port=3819,
        feedback_port=8011,
        host_class="private",
        fake=True,
        stale_timeout_ms=3000,
    )
    transport.open()
    transport.close()
    transport.close()
    assert transport.state.connection_state == "disconnected"
