"""Locked Ardour OSC ship-1 path map and ``/set_surface`` builder."""

from __future__ import annotations

from typing import Final

from app.services.ardour_osc_codec import encode_osc_message

# Locked /set_surface constants (plan Task 2).
SET_SURFACE_BANK_SIZE: Final[int] = 16
SET_SURFACE_STRIP_TYPES: Final[int] = 159
# feedback = 1+2+8+16+8192 (strip buttons, strip values, heartbeat, master, select)
SET_SURFACE_FEEDBACK: Final[int] = 8219
SET_SURFACE_GAINMODE: Final[int] = 0

PATH_SET_SURFACE: Final[str] = "/set_surface"
PATH_TRANSPORT_PLAY: Final[str] = "/transport_play"
PATH_TRANSPORT_STOP: Final[str] = "/transport_stop"
PATH_LOCATE: Final[str] = "/locate"
PATH_REC_ENABLE_TOGGLE: Final[str] = "/rec_enable_toggle"
PATH_STRIP_FADER: Final[str] = "/strip/fader"
PATH_STRIP_PAN: Final[str] = "/strip/pan_stereo_position"
PATH_STRIP_MUTE: Final[str] = "/strip/mute"
PATH_STRIP_SOLO: Final[str] = "/strip/solo"
PATH_STRIP_NAME: Final[str] = "/strip/name"
PATH_HEARTBEAT: Final[str] = "/heartbeat"
PATH_RECORD_ENABLE: Final[str] = "/rec_enable_toggle"
PATH_SELECT_SSID: Final[str] = "/select/ssid"


def set_surface_arg_tuple(*, feedback_port: int | None = None) -> tuple[int, ...]:
    """Return the frozen ship-1 ``/set_surface`` integer argument tuple.

    Locked prefix: bank_size, strip_types, feedback, gainmode.
    When ``feedback_port`` is provided, append send_page_size=0,
    plugin_page_size=0, and ``port`` so Ardour targets our listen port.
    """
    locked = (
        SET_SURFACE_BANK_SIZE,
        SET_SURFACE_STRIP_TYPES,
        SET_SURFACE_FEEDBACK,
        SET_SURFACE_GAINMODE,
    )
    if feedback_port is None:
        return locked
    if not (1 <= feedback_port <= 65535) or feedback_port == 3819:
        raise ValueError("feedback_port must be 1..65535 and not 3819")
    return locked + (0, 0, int(feedback_port))


def build_set_surface(*, feedback_port: int | None = None) -> bytes:
    """Encode locked ``/set_surface`` for companion connect."""
    return encode_osc_message(PATH_SET_SURFACE, *set_surface_arg_tuple(feedback_port=feedback_port))


def build_transport_play() -> bytes:
    return encode_osc_message(PATH_TRANSPORT_PLAY)


def build_transport_stop() -> bytes:
    return encode_osc_message(PATH_TRANSPORT_STOP)


def build_locate(samples: int, roll: int = 0) -> bytes:
    if samples < 0:
        raise ValueError("samples must be >= 0")
    if roll not in (0, 1):
        raise ValueError("roll must be 0 or 1")
    return encode_osc_message(PATH_LOCATE, int(samples), int(roll))


def build_rec_enable_toggle() -> bytes:
    """Master session record-arm toggle only (no strip recenable in ship-1)."""
    return encode_osc_message(PATH_REC_ENABLE_TOGGLE)


def build_strip_fader(ssid: int, value: float) -> bytes:
    _check_ssid(ssid)
    _check_unit_interval(value)
    return encode_osc_message(PATH_STRIP_FADER, int(ssid), float(value))


def build_strip_pan(ssid: int, value: float) -> bytes:
    _check_ssid(ssid)
    _check_unit_interval(value)
    return encode_osc_message(PATH_STRIP_PAN, int(ssid), float(value))


def build_strip_mute(ssid: int, value: int) -> bytes:
    _check_ssid(ssid)
    if value not in (0, 1):
        raise ValueError("mute value must be 0 or 1")
    return encode_osc_message(PATH_STRIP_MUTE, int(ssid), int(value))


def build_strip_solo(ssid: int, value: int) -> bytes:
    _check_ssid(ssid)
    if value not in (0, 1):
        raise ValueError("solo value must be 0 or 1")
    return encode_osc_message(PATH_STRIP_SOLO, int(ssid), int(value))


def _check_ssid(ssid: int) -> None:
    if not isinstance(ssid, int) or isinstance(ssid, bool) or ssid < 1:
        raise ValueError("ssid must be an integer >= 1")


def _check_unit_interval(value: float) -> None:
    if not (0.0 <= float(value) <= 1.0):
        raise ValueError("value must be in [0, 1]")
