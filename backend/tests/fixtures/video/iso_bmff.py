"""In-process ISO-BMFF (MP4/MOV) fixture. Tests must not commit media bytes."""

from __future__ import annotations

import struct
from pathlib import Path


def box(box_type: bytes, payload: bytes) -> bytes:
    if len(box_type) != 4:
        raise ValueError("box type must be 4 bytes")
    size = 8 + len(payload)
    return struct.pack(">I", size) + box_type + payload


def write_iso_bmff(
    path: Path,
    *,
    major_brand: bytes = b"isom",
    compatible_brands: tuple[bytes, ...] = (b"isom", b"mp42"),
    mvhd_timescale: int = 600,
    mvhd_duration: int = 1200,
    header_version: int = 0,
    video: dict | None = None,
    audio: dict | None = None,
    include_moov: bool = True,
    mdat_before_moov: bool = False,
    mdat_payload: bytes = b"",
) -> Path:
    """Write a minimal ``ftyp`` + ``moov`` file. ``mdat`` is optional and empty by default."""
    ftyp = box(
        b"ftyp",
        major_brand + struct.pack(">I", 0) + b"".join(compatible_brands),
    )
    parts = [ftyp]
    moov = b""
    if include_moov:
        trak_boxes = []
        if video is not None:
            trak_boxes.append(_trak(1, b"vide", header_version, mvhd_duration, video))
        if audio is not None:
            trak_boxes.append(_trak(2, b"soun", header_version, mvhd_duration, audio))
        moov = box(
            b"moov",
            _mvhd(header_version, mvhd_timescale, mvhd_duration) + b"".join(trak_boxes),
        )
    mdat = box(b"mdat", mdat_payload) if mdat_before_moov or mdat_payload else b""
    if mdat_before_moov:
        parts.append(mdat)
        parts.append(moov)
    else:
        parts.append(moov)
        if mdat:
            parts.append(mdat)
    path.write_bytes(b"".join(parts))
    return path


def _mvhd(version: int, timescale: int, duration: int) -> bytes:
    return box(b"mvhd", _media_header(version, timescale, duration) + _matrix_tail(next_track_id=3))


def _mdhd(version: int, timescale: int, duration: int) -> bytes:
    return box(b"mdhd", _media_header(version, timescale, duration) + struct.pack(">HH", 0, 0))


def _media_header(version: int, timescale: int, duration: int) -> bytes:
    if version == 0:
        return struct.pack(">IIIIII", 0, 0, 0, timescale, duration, 0)[:20]
    if version == 1:
        return struct.pack(">I", 0x01000000) + struct.pack(">QQIQ", 0, 0, timescale, duration)
    raise ValueError("header version must be 0 or 1")


def _matrix_tail(*, next_track_id: int) -> bytes:
    # rate, volume, reserved, matrix, pre_defined, next_track_id
    rate_volume = struct.pack(">IH", 0x00010000, 0x0100)
    reserved = b"\x00" * 10
    matrix = struct.pack(">9i", 0x00010000, 0, 0, 0, 0x00010000, 0, 0, 0, 0x40000000)
    predefined = b"\x00" * 24
    return rate_volume + reserved + matrix + predefined + struct.pack(">I", next_track_id)


def _trak(
    track_id: int,
    handler: bytes,
    version: int,
    fallback_duration: int,
    spec: dict,
) -> bytes:
    timescale = int(spec.get("timescale", 24))
    media_duration = int(spec.get("media_duration", fallback_duration))
    sample_count = int(spec["sample_count"])
    width = int(spec.get("width", 320))
    height = int(spec.get("height", 180))
    tkhd = box(b"tkhd", _tkhd_payload(version, track_id, media_duration, width, height))
    mdhd = _mdhd(version, timescale, media_duration)
    hdlr = box(
        b"hdlr",
        struct.pack(">I", version << 24) + struct.pack(">I", 0) + handler + (b"\x00" * 12) + b"\x00",
    )
    stts = box(
        b"stts",
        struct.pack(">II", 0, 1) + struct.pack(">II", sample_count, 1),
    )
    stsd = box(b"stsd", struct.pack(">II", 0, 0))
    stbl = box(b"stbl", stts + stsd)
    minf = box(b"minf", stbl)
    mdia = box(b"mdia", mdhd + hdlr + minf)
    return box(b"trak", tkhd + mdia)


def _tkhd_payload(version: int, track_id: int, duration: int, width: int, height: int) -> bytes:
    dimensions = struct.pack(">II", width << 16, height << 16)
    if version == 0:
        head = struct.pack(">IIIIII", 0, 0, 0, track_id, 0, duration)
        reserved = b"\x00" * 8
    elif version == 1:
        head = struct.pack(">I", 0x01000000) + struct.pack(">QQIIQ", 0, 0, track_id, 0, duration)
        reserved = b"\x00" * 8
    else:
        raise ValueError("header version must be 0 or 1")
    flags = struct.pack(">4H", 0, 0, 0, 0)
    matrix = struct.pack(">9i", 0x00010000, 0, 0, 0, 0x00010000, 0, 0, 0, 0x40000000)
    return head + reserved + flags + matrix + dimensions
