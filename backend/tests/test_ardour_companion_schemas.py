"""Schema refuse/round-trip and OSC host allowlist policy."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.ardour_companion_schemas import (
    ArdourCompanionConnectV1,
    ArdourCompanionError,
    ArdourCompanionSessionV1,
    ArdourCompanionStatusV1,
    ArdourLocateRequestV1,
    ArdourRecordArmRequestV1,
    ArdourStripBoolValueV1,
    ArdourStripFloatValueV1,
    map_ardour_error_to_http,
    validate_ardour_osc_host,
)


def test_connect_round_trip() -> None:
    body = ArdourCompanionConnectV1(
        host="127.0.0.1",
        osc_port=3819,
        feedback_port=8000,
        control_permission=True,
    )
    assert body.schema_version == "ardour.companion.connect.v1"
    assert body.control_permission is True
    dumped = body.model_dump()
    again = ArdourCompanionConnectV1.model_validate(dumped)
    assert again.host == "127.0.0.1"


def test_connect_refuses_feedback_port_3819() -> None:
    with pytest.raises(ValidationError):
        ArdourCompanionConnectV1(
            host="127.0.0.1",
            osc_port=3820,
            feedback_port=3819,
            control_permission=True,
        )


def test_connect_refuses_same_ports() -> None:
    with pytest.raises(ValidationError):
        ArdourCompanionConnectV1(
            host="10.0.0.2",
            osc_port=9000,
            feedback_port=9000,
        )


def test_connect_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        ArdourCompanionConnectV1.model_validate(
            {
                "host": "127.0.0.1",
                "osc_port": 3819,
                "feedback_port": 8000,
                "events": [],
            }
        )


def test_session_and_status_round_trip() -> None:
    session = ArdourCompanionSessionV1(
        session_id="ardc_deadbeef",
        host="127.0.0.1",
        osc_port=3819,
        feedback_port=8000,
        control_permission=True,
        connection_state="awaiting_feedback",
        fake=True,
    )
    assert session.schema_version == "ardour.companion.session.v1"

    status = ArdourCompanionStatusV1(
        enabled=True,
        connection_state="connected",
        session_id="ardc_deadbeef",
        host="127.0.0.1",
        osc_port=3819,
        feedback_port=8000,
        control_permission=True,
        transport_playing=True,
        record_armed=False,
        strips=[{"ssid": 1, "name": "Audio 1", "fader": 0.5, "pan": 0.5, "mute": 0, "solo": 0}],
        feedback_age_ms=12,
        stale=False,
    )
    assert status.schema_version == "ardour.companion.status.v1"
    assert status.strips[0].ssid == 1


def test_command_bodies() -> None:
    locate = ArdourLocateRequestV1(samples=48000, roll=0)
    assert locate.roll == 0
    arm = ArdourRecordArmRequestV1(desired=True)
    assert arm.desired is True
    fader = ArdourStripFloatValueV1(value=0.75)
    assert fader.value == 0.75
    mute = ArdourStripBoolValueV1(value=1)
    assert mute.value == 1
    with pytest.raises(ValidationError):
        ArdourStripFloatValueV1(value=1.5)


def test_host_allows_loopback_and_private() -> None:
    host, klass = validate_ardour_osc_host("127.0.0.1")
    assert host == "127.0.0.1"
    assert klass == "loopback"
    host, klass = validate_ardour_osc_host("10.0.0.5")
    assert klass == "private"
    host, klass = validate_ardour_osc_host("192.168.1.10")
    assert klass == "private"


def test_host_refuses_link_local_multicast_unspecified() -> None:
    for bad in ("169.254.1.1", "224.0.0.1", "0.0.0.0"):
        with pytest.raises(ArdourCompanionError) as exc:
            validate_ardour_osc_host(bad)
        assert exc.value.code == "ardour_host_refused"


def test_host_refuses_public_by_default() -> None:
    with pytest.raises(ArdourCompanionError) as exc:
        validate_ardour_osc_host("8.8.8.8")
    assert exc.value.code == "ardour_host_refused"


def test_host_allows_public_when_flag_on() -> None:
    host, klass = validate_ardour_osc_host("8.8.8.8", allow_public_hosts=True)
    assert host == "8.8.8.8"
    assert klass == "public"


def test_hostname_allowed_when_resolved_private(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_getaddrinfo(host: str, *args: object, **kwargs: object):  # noqa: ANN001
        assert host == "ardour.lan"
        return [(None, None, None, None, ("10.0.0.9", 0))]

    monkeypatch.setattr(
        "app.ardour_companion_schemas.socket.getaddrinfo",
        _fake_getaddrinfo,
    )
    host, klass = validate_ardour_osc_host("ardour.lan")
    assert host == "ardour.lan"
    assert klass == "private"


def test_hostname_refused_when_resolved_public(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_getaddrinfo(host: str, *args: object, **kwargs: object):  # noqa: ANN001
        return [(None, None, None, None, ("8.8.8.8", 0))]

    monkeypatch.setattr(
        "app.ardour_companion_schemas.socket.getaddrinfo",
        _fake_getaddrinfo,
    )
    with pytest.raises(ArdourCompanionError) as exc:
        validate_ardour_osc_host("example.com")
    assert exc.value.code == "ardour_host_refused"


def test_error_http_mapping() -> None:
    exc = ArdourCompanionError("ardour_companion_disabled")
    status, detail = map_ardour_error_to_http(exc)
    assert status == 403
    assert detail["code"] == "ardour_companion_disabled"
