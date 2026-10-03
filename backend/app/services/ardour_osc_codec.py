"""Pure OSC 1.0 encode/decode over stdlib ``struct`` (no python-osc)."""

from __future__ import annotations

import logging
import struct
from typing import Any

logger = logging.getLogger(__name__)

OscArg = int | float | str | bytes


class OscCodecError(ValueError):
    """Malformed OSC datagram or unsupported argument type."""


def _pad4(data: bytes) -> bytes:
    remainder = len(data) % 4
    if remainder == 0:
        return data
    return data + (b"\x00" * (4 - remainder))


def _encode_string(text: str) -> bytes:
    encoded = text.encode("utf-8") + b"\x00"
    return _pad4(encoded)


def _encode_blob(payload: bytes) -> bytes:
    header = struct.pack(">i", len(payload))
    return _pad4(header + payload)


def encode_osc_message(path: str, *args: OscArg) -> bytes:
    """Encode a single OSC message (no bundle).

    Supports int (``i``), float (``f``), str (``s``), and bytes blob (``b``).
    """
    if not path.startswith("/"):
        raise OscCodecError("OSC path must start with '/'")
    type_tags = [","]
    body = bytearray()
    for arg in args:
        if isinstance(arg, bool):
            # bool is a subclass of int; refuse to avoid silent i coercion.
            raise OscCodecError("bool OSC args are not supported in ship-1")
        if isinstance(arg, int):
            type_tags.append("i")
            body.extend(struct.pack(">i", arg))
        elif isinstance(arg, float):
            type_tags.append("f")
            body.extend(struct.pack(">f", arg))
        elif isinstance(arg, str):
            type_tags.append("s")
            body.extend(_encode_string(arg))
        elif isinstance(arg, (bytes, bytearray)):
            type_tags.append("b")
            body.extend(_encode_blob(bytes(arg)))
        else:
            raise OscCodecError(f"unsupported OSC arg type: {type(arg).__name__}")

    packet = _encode_string(path) + _encode_string("".join(type_tags)) + bytes(body)
    logger.debug(
        "OSC encode",
        extra={"path": path, "arg_count": len(args)},
    )
    return packet


def _read_padded_string(data: bytes, offset: int) -> tuple[str, int]:
    end = data.find(b"\x00", offset)
    if end < 0:
        raise OscCodecError("unterminated OSC string")
    text = data[offset:end].decode("utf-8")
    size = end - offset + 1
    padded = size + ((4 - (size % 4)) % 4)
    return text, offset + padded


def _read_blob(data: bytes, offset: int) -> tuple[bytes, int]:
    if offset + 4 > len(data):
        raise OscCodecError("truncated OSC blob size")
    (length,) = struct.unpack_from(">i", data, offset)
    if length < 0:
        raise OscCodecError("negative OSC blob size")
    start = offset + 4
    end = start + length
    if end > len(data):
        raise OscCodecError("truncated OSC blob")
    payload = data[start:end]
    total = 4 + length
    padded = total + ((4 - (total % 4)) % 4)
    return payload, offset + padded


def decode_osc_message(packet: bytes) -> tuple[str, list[Any]]:
    """Decode a single OSC message. Bundles are refused in ship-1."""
    if not packet:
        raise OscCodecError("empty OSC packet")
    if packet.startswith(b"#bundle"):
        raise OscCodecError("OSC bundles are not supported in ship-1")

    path, offset = _read_padded_string(packet, 0)
    if not path.startswith("/"):
        raise OscCodecError("OSC path must start with '/'")

    if offset >= len(packet):
        logger.debug("OSC decode", extra={"path": path, "arg_count": 0})
        return path, []

    tags, offset = _read_padded_string(packet, offset)
    if not tags.startswith(","):
        raise OscCodecError("OSC type tag string must start with ','")

    args: list[Any] = []
    for tag in tags[1:]:
        if tag == "i":
            if offset + 4 > len(packet):
                raise OscCodecError("truncated OSC int")
            (value,) = struct.unpack_from(">i", packet, offset)
            args.append(value)
            offset += 4
        elif tag == "f":
            if offset + 4 > len(packet):
                raise OscCodecError("truncated OSC float")
            (value,) = struct.unpack_from(">f", packet, offset)
            args.append(value)
            offset += 4
        elif tag == "s":
            value, offset = _read_padded_string(packet, offset)
            args.append(value)
        elif tag == "b":
            value, offset = _read_blob(packet, offset)
            args.append(value)
        elif tag == "T":
            args.append(True)
        elif tag == "F":
            args.append(False)
        else:
            raise OscCodecError(f"unsupported OSC type tag: {tag}")

    logger.debug("OSC decode", extra={"path": path, "arg_count": len(args)})
    return path, args
