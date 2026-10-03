"""Process-memory companion session service tests (fake mode)."""

from __future__ import annotations

import pytest

from app.ardour_companion_schemas import ArdourCompanionConnectV1, ArdourCompanionError
from app.ardour_companion_settings import load_ardour_companion_settings
from app.services import ardour_companion_service as svc
from app.services.ardour_osc_paths import PATH_SET_SURFACE


@pytest.fixture(autouse=True)
def _clean_session() -> None:
    svc.shutdown_ardour_companion()
    yield
    svc.shutdown_ardour_companion()


def _settings(**overrides: str):
    env = {
        "ARDOUR_COMPANION_ENABLED": "1",
        "ARDOUR_COMPANION_FAKE": "1",
        **overrides,
    }
    return load_ardour_companion_settings(env)


def test_connect_sends_set_surface_and_play_updates_observed() -> None:
    settings = _settings()
    session = svc.connect_ardour_companion(
        ArdourCompanionConnectV1(
            host="127.0.0.1",
            osc_port=3819,
            feedback_port=18000,
            control_permission=True,
        ),
        settings=settings,
    )
    assert session.connection_state == "connected"
    with svc._LOCK:
        assert svc._SESSION is not None
        peer = svc._SESSION.transport.fake_peer
        assert peer is not None
        assert PATH_SET_SURFACE in peer.paths_sent()

    result = svc.transport_play(settings=settings)
    assert result.accepted is True
    assert result.status.transport_playing is True

    fader = svc.set_strip_fader(1, 0.2, settings=settings)
    assert fader.status.strips[0].fader == pytest.approx(0.2)


def test_record_arm_refuses_when_stale() -> None:
    settings = _settings()
    svc.connect_ardour_companion(
        ArdourCompanionConnectV1(
            host="127.0.0.1",
            osc_port=3819,
            feedback_port=18001,
            control_permission=True,
        ),
        settings=settings,
    )
    with svc._LOCK:
        assert svc._SESSION is not None
        state = svc._SESSION.transport.state
        state.record_armed = None
    with pytest.raises(ArdourCompanionError) as exc:
        svc.set_record_arm(True, settings=settings)
    assert exc.value.code == "ardour_feedback_stale"


def test_record_arm_toggles_only_when_different() -> None:
    settings = _settings()
    svc.connect_ardour_companion(
        ArdourCompanionConnectV1(
            host="127.0.0.1",
            osc_port=3819,
            feedback_port=18002,
            control_permission=True,
        ),
        settings=settings,
    )
    status = svc.get_ardour_companion_status(settings=settings)
    assert status.record_armed is False
    with svc._LOCK:
        assert svc._SESSION is not None
        peer = svc._SESSION.transport.fake_peer
        assert peer is not None
        before = len(peer.outbound)
    noop = svc.set_record_arm(False, settings=settings)
    assert noop.accepted is True
    with svc._LOCK:
        assert svc._SESSION is not None
        peer = svc._SESSION.transport.fake_peer
        assert peer is not None
        assert len(peer.outbound) == before
    toggled = svc.set_record_arm(True, settings=settings)
    assert toggled.status.record_armed is True


def test_permission_required_for_mutate() -> None:
    settings = _settings()
    svc.connect_ardour_companion(
        ArdourCompanionConnectV1(
            host="127.0.0.1",
            osc_port=3819,
            feedback_port=18003,
            control_permission=False,
        ),
        settings=settings,
    )
    with pytest.raises(ArdourCompanionError) as exc:
        svc.transport_play(settings=settings)
    assert exc.value.code == "ardour_control_permission_required"


def test_connect_replace_and_disconnect() -> None:
    settings = _settings()
    first = svc.connect_ardour_companion(
        ArdourCompanionConnectV1(
            host="127.0.0.1",
            osc_port=3819,
            feedback_port=18004,
            control_permission=True,
        ),
        settings=settings,
    )
    second = svc.connect_ardour_companion(
        ArdourCompanionConnectV1(
            host="10.0.0.8",
            osc_port=3819,
            feedback_port=18005,
            control_permission=True,
        ),
        settings=settings,
    )
    assert first.session_id != second.session_id
    assert second.host == "10.0.0.8"
    result = svc.disconnect_ardour_companion(settings=settings)
    assert result.status.connection_state == "disconnected"
    with pytest.raises(ArdourCompanionError) as exc:
        svc.transport_play(settings=settings)
    assert exc.value.code == "ardour_not_connected"


def test_public_host_refused() -> None:
    settings = _settings()
    with pytest.raises(ArdourCompanionError) as exc:
        svc.connect_ardour_companion(
            ArdourCompanionConnectV1(
                host="8.8.8.8",
                osc_port=3819,
                feedback_port=18006,
                control_permission=True,
            ),
            settings=settings,
        )
    assert exc.value.code == "ardour_host_refused"


def test_disabled_refuses_connect() -> None:
    settings = load_ardour_companion_settings({"ARDOUR_COMPANION_ENABLED": "0"})
    with pytest.raises(ArdourCompanionError) as exc:
        svc.connect_ardour_companion(
            ArdourCompanionConnectV1(
                host="127.0.0.1",
                osc_port=3819,
                feedback_port=18007,
                control_permission=True,
            ),
            settings=settings,
        )
    assert exc.value.code == "ardour_companion_disabled"
    status = svc.get_ardour_companion_status(settings=settings)
    assert status.enabled is False
    assert status.connection_state == "disabled"
