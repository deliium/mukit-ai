"""Process-memory Ardour companion session (one per process for ship-1).

Does not import SQLite, Composition, or ``ai_agents/``. Restart clears the
session. Commands return accepted + feedback-observed status only.
"""

from __future__ import annotations

import logging
import secrets
import threading
from dataclasses import dataclass
from typing import Literal

from app.ardour_companion_schemas import (
    ArdourCommandAcceptedV1,
    ArdourCompanionConnectV1,
    ArdourCompanionError,
    ArdourCompanionSessionV1,
    ArdourCompanionStatusV1,
    ArdourRecordStatusV1,
    ArdourStripsResponseV1,
    validate_ardour_osc_host,
)
from app.ardour_companion_settings import (
    ArdourCompanionSettings,
    load_ardour_companion_settings,
)
from app.services.ardour_osc_paths import (
    build_locate,
    build_rec_enable_toggle,
    build_strip_fader,
    build_strip_mute,
    build_strip_pan,
    build_strip_solo,
    build_transport_play,
    build_transport_stop,
)
from app.services.ardour_osc_transport import ArdourOscTransport

logger = logging.getLogger(__name__)

CommandName = Literal[
    "play",
    "stop",
    "locate",
    "record_arm",
    "strip_fader",
    "strip_pan",
    "strip_mute",
    "strip_solo",
    "connect",
    "disconnect",
]

_MUTATING_STATES = frozenset({"connected", "awaiting_feedback"})


@dataclass
class _HeldSession:
    session_id: str
    host: str
    osc_port: int
    feedback_port: int
    control_permission: bool
    host_class: str
    transport: ArdourOscTransport


_LOCK = threading.RLock()
_SESSION: _HeldSession | None = None


def _new_session_id() -> str:
    return f"ardc_{secrets.token_hex(4)}"


def _require_enabled(settings: ArdourCompanionSettings) -> None:
    if not settings.enabled:
        raise ArdourCompanionError("ardour_companion_disabled")


def _status_shell(
    *,
    settings: ArdourCompanionSettings,
    session: _HeldSession | None,
) -> ArdourCompanionStatusV1:
    if not settings.enabled:
        return ArdourCompanionStatusV1(
            enabled=False,
            connection_state="disabled",
        )
    if session is None:
        return ArdourCompanionStatusV1(
            enabled=True,
            connection_state="disconnected",
            fake=settings.fake,
        )
    state = session.transport.state
    state.refresh_connection_from_age()
    return ArdourCompanionStatusV1(
        enabled=True,
        connection_state=state.connection_state,
        session_id=session.session_id,
        host=session.host,
        osc_port=session.osc_port,
        feedback_port=session.feedback_port,
        control_permission=session.control_permission,
        fake=session.transport.fake,
        transport_playing=state.transport_playing,
        locate_samples=state.locate_samples,
        last_requested_locate_samples=state.last_requested_locate_samples,
        record_armed=state.record_armed,
        selected_ssid=state.selected_ssid,
        strips=state.strips_list(),
        feedback_age_ms=state.feedback_age_ms(),
        stale=state.is_stale(),
        last_error_code=state.last_error_code,
    )


def get_ardour_companion_status(
    settings: ArdourCompanionSettings | None = None,
) -> ArdourCompanionStatusV1:
    """Return status. Always safe when disabled (enabled=false)."""
    cfg = settings if settings is not None else load_ardour_companion_settings()
    with _LOCK:
        return _status_shell(settings=cfg, session=_SESSION)


def connect_ardour_companion(
    body: ArdourCompanionConnectV1,
    *,
    settings: ArdourCompanionSettings | None = None,
) -> ArdourCompanionSessionV1:
    """Connect or replace the active session; sends locked ``/set_surface``."""
    cfg = settings if settings is not None else load_ardour_companion_settings()
    _require_enabled(cfg)
    host, host_class = validate_ardour_osc_host(
        body.host,
        allow_public_hosts=cfg.allow_public_hosts,
    )

    with _LOCK:
        global _SESSION
        if _SESSION is not None:
            logger.info(
                "Ardour companion connect-replace",
                extra={
                    "command": "connect",
                    "connection_state": _SESSION.transport.state.connection_state,
                },
            )
            _SESSION.transport.close()
            _SESSION = None

        transport = ArdourOscTransport(
            host=host,
            osc_port=body.osc_port,
            feedback_port=body.feedback_port,
            host_class=host_class,
            fake=cfg.fake,
            stale_timeout_ms=cfg.feedback_stale_ms,
        )
        try:
            transport.open()
        except ArdourCompanionError:
            transport.close()
            raise

        session = _HeldSession(
            session_id=_new_session_id(),
            host=host,
            osc_port=body.osc_port,
            feedback_port=body.feedback_port,
            control_permission=bool(body.control_permission),
            host_class=host_class,
            transport=transport,
        )
        _SESSION = session
        logger.info(
            "Ardour companion connected",
            extra={
                "command": "connect",
                "accepted": True,
                "connection_state": transport.state.connection_state,
                "host_class": host_class,
                "fake": cfg.fake,
            },
        )
        return ArdourCompanionSessionV1(
            session_id=session.session_id,
            host=session.host,
            osc_port=session.osc_port,
            feedback_port=session.feedback_port,
            control_permission=session.control_permission,
            connection_state=transport.state.connection_state,
            fake=cfg.fake,
        )


def disconnect_ardour_companion(
    *,
    settings: ArdourCompanionSettings | None = None,
) -> ArdourCommandAcceptedV1:
    cfg = settings if settings is not None else load_ardour_companion_settings()
    _require_enabled(cfg)
    with _LOCK:
        global _SESSION
        if _SESSION is None:
            status = _status_shell(settings=cfg, session=None)
            logger.info(
                "Ardour companion disconnect noop",
                extra={
                    "command": "disconnect",
                    "accepted": True,
                    "connection_state": status.connection_state,
                },
            )
            return ArdourCommandAcceptedV1(
                accepted=True,
                command="disconnect",
                status=status,
            )
        _SESSION.transport.close()
        _SESSION = None
        status = _status_shell(settings=cfg, session=None)
        logger.info(
            "Ardour companion disconnected",
            extra={
                "command": "disconnect",
                "accepted": True,
                "connection_state": status.connection_state,
            },
        )
        return ArdourCommandAcceptedV1(
            accepted=True,
            command="disconnect",
            status=status,
        )


def shutdown_ardour_companion() -> None:
    """Lifespan teardown: close sockets whether or not the flag is on."""
    with _LOCK:
        global _SESSION
        if _SESSION is None:
            logger.info(
                "Ardour companion lifespan teardown",
                extra={"connection_state": "disconnected"},
            )
            return
        _SESSION.transport.close()
        _SESSION = None
        logger.info(
            "Ardour companion lifespan teardown",
            extra={"connection_state": "disconnected"},
        )


def _require_session_for_mutate(
    settings: ArdourCompanionSettings,
) -> tuple[_HeldSession, list[str]]:
    if _SESSION is None:
        raise ArdourCompanionError("ardour_not_connected")
    session = _SESSION
    if not session.control_permission:
        logger.warning(
            "Ardour control permission required",
            extra={"code": "ardour_control_permission_required"},
        )
        raise ArdourCompanionError("ardour_control_permission_required")
    state = session.transport.state
    state.refresh_connection_from_age()
    warnings: list[str] = []
    if state.connection_state not in _MUTATING_STATES:
        code = (
            "ardour_feedback_stale"
            if state.connection_state == "stale"
            else "ardour_not_connected"
        )
        logger.warning(
            "Ardour mutating command refused",
            extra={"code": code, "connection_state": state.connection_state},
        )
        raise ArdourCompanionError(
            code,
            details={"connection_state": state.connection_state},
        )
    if state.connection_state == "awaiting_feedback":
        warnings.append("ardour_feedback_stale")
        logger.warning(
            "Ardour command while awaiting feedback",
            extra={"code": "ardour_feedback_stale", "connection_state": "awaiting_feedback"},
        )
    return session, warnings


def _accepted(
    *,
    command: CommandName,
    settings: ArdourCompanionSettings,
    warnings: list[str] | None = None,
) -> ArdourCommandAcceptedV1:
    status = _status_shell(settings=settings, session=_SESSION)
    logger.info(
        "Ardour companion command",
        extra={
            "command": command,
            "accepted": True,
            "connection_state": status.connection_state,
        },
    )
    return ArdourCommandAcceptedV1(
        accepted=True,
        command=command,
        status=status,
        warnings=list(warnings or []),
    )


def transport_play(
    *,
    settings: ArdourCompanionSettings | None = None,
) -> ArdourCommandAcceptedV1:
    cfg = settings if settings is not None else load_ardour_companion_settings()
    _require_enabled(cfg)
    with _LOCK:
        session, warnings = _require_session_for_mutate(cfg)
        session.transport.send_raw(build_transport_play())
        return _accepted(command="play", settings=cfg, warnings=warnings)


def transport_stop(
    *,
    settings: ArdourCompanionSettings | None = None,
) -> ArdourCommandAcceptedV1:
    cfg = settings if settings is not None else load_ardour_companion_settings()
    _require_enabled(cfg)
    with _LOCK:
        session, warnings = _require_session_for_mutate(cfg)
        session.transport.send_raw(build_transport_stop())
        return _accepted(command="stop", settings=cfg, warnings=warnings)


def transport_locate(
    samples: int,
    roll: int = 0,
    *,
    settings: ArdourCompanionSettings | None = None,
) -> ArdourCommandAcceptedV1:
    cfg = settings if settings is not None else load_ardour_companion_settings()
    _require_enabled(cfg)
    if samples < 0 or roll not in (0, 1):
        raise ArdourCompanionError("ardour_payload_invalid")
    with _LOCK:
        session, warnings = _require_session_for_mutate(cfg)
        session.transport.state.note_requested_locate(samples)
        session.transport.send_raw(build_locate(samples, roll))
        return _accepted(command="locate", settings=cfg, warnings=warnings)


def get_record_arm(
    *,
    settings: ArdourCompanionSettings | None = None,
) -> ArdourRecordStatusV1:
    cfg = settings if settings is not None else load_ardour_companion_settings()
    status = get_ardour_companion_status(settings=cfg)
    return ArdourRecordStatusV1(
        record_armed=status.record_armed,
        connection_state=status.connection_state,
        stale=status.stale,
        feedback_age_ms=status.feedback_age_ms,
    )


def set_record_arm(
    desired: bool,
    *,
    settings: ArdourCompanionSettings | None = None,
) -> ArdourCommandAcceptedV1:
    """Toggle master record only when observed ≠ desired; refuse if unknown/stale."""
    cfg = settings if settings is not None else load_ardour_companion_settings()
    _require_enabled(cfg)
    with _LOCK:
        session, warnings = _require_session_for_mutate(cfg)
        state = session.transport.state
        state.refresh_connection_from_age()
        if state.record_armed is None or state.is_stale() or state.connection_state == "stale":
            logger.warning(
                "Ardour record-arm refused; feedback stale or unknown",
                extra={"code": "ardour_feedback_stale"},
            )
            raise ArdourCompanionError("ardour_feedback_stale")
        if state.record_armed is bool(desired):
            return _accepted(command="record_arm", settings=cfg, warnings=warnings)
        session.transport.send_raw(build_rec_enable_toggle())
        return _accepted(command="record_arm", settings=cfg, warnings=warnings)


def get_strips(
    *,
    settings: ArdourCompanionSettings | None = None,
) -> ArdourStripsResponseV1:
    status = get_ardour_companion_status(settings=settings)
    return ArdourStripsResponseV1(
        selected_ssid=status.selected_ssid,
        strips=status.strips,
        connection_state=status.connection_state,
        stale=status.stale,
        feedback_age_ms=status.feedback_age_ms,
    )


def set_strip_fader(
    ssid: int,
    value: float,
    *,
    settings: ArdourCompanionSettings | None = None,
) -> ArdourCommandAcceptedV1:
    return _strip_float_command("strip_fader", ssid, value, settings=settings)


def set_strip_pan(
    ssid: int,
    value: float,
    *,
    settings: ArdourCompanionSettings | None = None,
) -> ArdourCommandAcceptedV1:
    return _strip_float_command("strip_pan", ssid, value, settings=settings)


def set_strip_mute(
    ssid: int,
    value: int,
    *,
    settings: ArdourCompanionSettings | None = None,
) -> ArdourCommandAcceptedV1:
    return _strip_bool_command("strip_mute", ssid, value, settings=settings)


def set_strip_solo(
    ssid: int,
    value: int,
    *,
    settings: ArdourCompanionSettings | None = None,
) -> ArdourCommandAcceptedV1:
    return _strip_bool_command("strip_solo", ssid, value, settings=settings)


def _strip_float_command(
    command: Literal["strip_fader", "strip_pan"],
    ssid: int,
    value: float,
    *,
    settings: ArdourCompanionSettings | None,
) -> ArdourCommandAcceptedV1:
    cfg = settings if settings is not None else load_ardour_companion_settings()
    _require_enabled(cfg)
    if ssid < 1 or not (0.0 <= float(value) <= 1.0):
        raise ArdourCompanionError("ardour_payload_invalid")
    with _LOCK:
        session, warnings = _require_session_for_mutate(cfg)
        packet = (
            build_strip_fader(ssid, value)
            if command == "strip_fader"
            else build_strip_pan(ssid, value)
        )
        session.transport.send_raw(packet)
        return _accepted(command=command, settings=cfg, warnings=warnings)


def _strip_bool_command(
    command: Literal["strip_mute", "strip_solo"],
    ssid: int,
    value: int,
    *,
    settings: ArdourCompanionSettings | None,
) -> ArdourCommandAcceptedV1:
    cfg = settings if settings is not None else load_ardour_companion_settings()
    _require_enabled(cfg)
    if ssid < 1 or value not in (0, 1):
        raise ArdourCompanionError("ardour_payload_invalid")
    with _LOCK:
        session, warnings = _require_session_for_mutate(cfg)
        packet = (
            build_strip_mute(ssid, value)
            if command == "strip_mute"
            else build_strip_solo(ssid, value)
        )
        session.transport.send_raw(packet)
        return _accepted(command=command, settings=cfg, warnings=warnings)
