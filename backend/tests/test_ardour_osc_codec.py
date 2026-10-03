"""OSC codec round-trip and locked Ardour path map."""

from __future__ import annotations

import struct

import pytest

from app.services.ardour_osc_codec import (
    OscCodecError,
    decode_osc_message,
    encode_osc_message,
)
from app.services.ardour_osc_paths import (
    SET_SURFACE_BANK_SIZE,
    SET_SURFACE_FEEDBACK,
    SET_SURFACE_GAINMODE,
    SET_SURFACE_STRIP_TYPES,
    build_locate,
    build_rec_enable_toggle,
    build_set_surface,
    build_strip_fader,
    build_strip_mute,
    build_strip_pan,
    build_strip_solo,
    build_transport_play,
    build_transport_stop,
    set_surface_arg_tuple,
)


def test_round_trip_int_float_string_blob() -> None:
    packet = encode_osc_message("/test", 7, 0.5, "name", b"\x01\x02")
    path, args = decode_osc_message(packet)
    assert path == "/test"
    assert args[0] == 7
    assert abs(args[1] - 0.5) < 1e-6
    assert args[2] == "name"
    assert args[3] == b"\x01\x02"


def test_encode_path_padding() -> None:
    packet = encode_osc_message("/a")
    # path "/a\0" padded to 4 bytes, then ",\0\0\0"
    assert packet[:4] == b"/a\x00\x00"
    path, args = decode_osc_message(packet)
    assert path == "/a"
    assert args == []


def test_refuse_bundle_and_bad_path() -> None:
    with pytest.raises(OscCodecError):
        encode_osc_message("no-slash")
    with pytest.raises(OscCodecError):
        decode_osc_message(b"#bundle\x00" + b"\x00" * 8)


def test_feedback_bitmask_constant() -> None:
    assert SET_SURFACE_FEEDBACK == 1 + 2 + 8 + 16 + 8192
    assert SET_SURFACE_FEEDBACK == 8219


def test_set_surface_arg_tuple_frozen() -> None:
    locked = set_surface_arg_tuple()
    assert locked == (
        SET_SURFACE_BANK_SIZE,
        SET_SURFACE_STRIP_TYPES,
        SET_SURFACE_FEEDBACK,
        SET_SURFACE_GAINMODE,
    )
    assert locked == (16, 159, 8219, 0)
    with_port = set_surface_arg_tuple(feedback_port=8000)
    assert with_port[:4] == locked
    assert with_port == (16, 159, 8219, 0, 0, 0, 8000)


def test_build_set_surface_encodes_locked_args() -> None:
    packet = build_set_surface(feedback_port=8000)
    path, args = decode_osc_message(packet)
    assert path == "/set_surface"
    assert args == [16, 159, 8219, 0, 0, 0, 8000]


def test_transport_and_locate_builders() -> None:
    path, args = decode_osc_message(build_transport_play())
    assert path == "/transport_play"
    assert args == []
    path, args = decode_osc_message(build_transport_stop())
    assert path == "/transport_stop"
    path, args = decode_osc_message(build_locate(48000, 1))
    assert path == "/locate"
    assert args == [48000, 1]
    path, args = decode_osc_message(build_rec_enable_toggle())
    assert path == "/rec_enable_toggle"
    assert args == []


def test_strip_builders() -> None:
    path, args = decode_osc_message(build_strip_fader(2, 0.25))
    assert path == "/strip/fader"
    assert args[0] == 2
    assert abs(args[1] - 0.25) < 1e-6
    path, args = decode_osc_message(build_strip_pan(3, 0.5))
    assert path == "/strip/pan_stereo_position"
    path, args = decode_osc_message(build_strip_mute(1, 1))
    assert path == "/strip/mute"
    assert args == [1, 1]
    path, args = decode_osc_message(build_strip_solo(4, 0))
    assert path == "/strip/solo"
    assert args == [4, 0]


def test_float_endian_explicit() -> None:
    packet = encode_osc_message("/f", 1.0)
    # after path+tags, float32 big-endian 1.0
    path, args = decode_osc_message(packet)
    assert args[0] == struct.unpack(">f", struct.pack(">f", 1.0))[0]
