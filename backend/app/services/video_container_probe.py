"""Read-only ISO-BMFF probe for one MP4 or MOV picture.

Opens the file in ``rb`` and seeks over ``mdat``. It does not transcode,
demux, or rewrite the upload.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path

from app.video_scoring_schemas import CLOSED_FRAME_RATES, VideoScoringError

logger = logging.getLogger(__name__)

_ALLOWED_BRANDS = frozenset({b"isom", b"mp41", b"mp42", b"iso2", b"qt  "})
_MOOV_READ_LIMIT = 16 * 1024 * 1024


@dataclass(frozen=True)
class VideoAssetProbe:
    container: str
    content_type: str
    byte_size: int
    duration_seconds: float
    frame_rate_numerator: int
    frame_rate_denominator: int
    frame_rate_snapped: bool
    has_audio: bool
    width: int
    height: int


def probe_iso_bmff(path: Path) -> VideoAssetProbe:
    """Return container metadata. The file is opened read-only."""
    byte_size = path.stat().st_size
    logger.debug("probe_iso_bmff", extra={"byte_size": byte_size})
    try:
        with path.open("rb") as handle:
            probe = _parse_file(handle, byte_size)
    except VideoScoringError as exc:
        logger.info(
            "video container probe",
            extra={"byte_size": byte_size, "error_code": exc.code},
        )
        raise
    logger.info(
        "video container probe",
        extra={
            "container": probe.container,
            "byte_size": probe.byte_size,
            "duration_seconds": probe.duration_seconds,
            "frame_rate_numerator": probe.frame_rate_numerator,
            "frame_rate_denominator": probe.frame_rate_denominator,
            "width": probe.width,
            "height": probe.height,
            "has_audio": probe.has_audio,
        },
    )
    return probe


def _parse_file(handle, byte_size: int) -> VideoAssetProbe:
    ftyp_body: bytes | None = None
    moov_body: bytes | None = None
    pos = 0
    while pos + 8 <= byte_size:
        handle.seek(pos)
        header = handle.read(8)
        if len(header) < 8:
            raise VideoScoringError("video_moov_not_found")
        box_size = int.from_bytes(header[0:4], "big")
        box_type = header[4:8]
        header_len = 8
        if box_size == 1:
            extended = handle.read(8)
            if len(extended) < 8:
                raise VideoScoringError("video_moov_not_found")
            box_size = int.from_bytes(extended, "big")
            header_len = 16
        elif box_size == 0:
            box_size = byte_size - pos
        if box_size < header_len or pos + box_size > byte_size:
            raise VideoScoringError("video_moov_not_found")
        payload_len = box_size - header_len
        if box_type == b"ftyp":
            ftyp_body = handle.read(payload_len)
        elif box_type == b"moov":
            if payload_len > _MOOV_READ_LIMIT:
                raise VideoScoringError("video_moov_not_found")
            moov_body = handle.read(payload_len)
        else:
            handle.seek(pos + box_size)
        pos += box_size
    if ftyp_body is None:
        raise VideoScoringError("video_container_unsupported")
    container, content_type = _container_from_ftyp(ftyp_body)
    if moov_body is None:
        raise VideoScoringError("video_moov_not_found")
    return _parse_moov(moov_body, container, content_type, byte_size)


def _container_from_ftyp(body: bytes) -> tuple[str, str]:
    if len(body) < 8:
        raise VideoScoringError("video_container_unsupported")
    major = body[0:4]
    brands = [major]
    offset = 8
    while offset + 4 <= len(body):
        brands.append(body[offset : offset + 4])
        offset += 4
    if not any(brand in _ALLOWED_BRANDS for brand in brands):
        raise VideoScoringError("video_container_unsupported")
    if major == b"qt  ":
        return "mov", "video/quicktime"
    return "mp4", "video/mp4"


def _parse_moov(body: bytes, container: str, content_type: str, byte_size: int) -> VideoAssetProbe:
    children = _child_map(body)
    mvhd = children.get(b"mvhd")
    if mvhd is None:
        raise VideoScoringError("video_duration_invalid")
    timescale, duration_units = _timescale_duration(mvhd, error_code="video_duration_invalid")
    if timescale <= 0 or duration_units <= 0:
        raise VideoScoringError("video_duration_invalid")
    duration_seconds = duration_units / timescale
    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise VideoScoringError("video_duration_invalid")

    has_audio = False
    video_trak: dict | None = None
    for trak in _children_of_type(body, b"trak"):
        handler = _handler_type(trak)
        if handler == b"soun":
            has_audio = True
        elif handler == b"vide" and video_trak is None:
            video_trak = _video_trak(trak)
    if video_trak is None:
        raise VideoScoringError("video_missing_video_track")
    numerator, denominator, snapped = _snap_rate(
        video_trak["sample_count"],
        video_trak["timescale"],
        video_trak["media_duration"],
    )
    return VideoAssetProbe(
        container=container,
        content_type=content_type,
        byte_size=byte_size,
        duration_seconds=duration_seconds,
        frame_rate_numerator=numerator,
        frame_rate_denominator=denominator,
        frame_rate_snapped=snapped,
        has_audio=has_audio,
        width=video_trak["width"],
        height=video_trak["height"],
    )


def _video_trak(trak: bytes) -> dict:
    tkhd = _find_nested(trak, (b"tkhd",))
    if tkhd is None:
        raise VideoScoringError("video_resolution_invalid")
    width, height = _dimensions(tkhd)
    if width <= 0 or height <= 0:
        raise VideoScoringError("video_resolution_invalid")
    mdhd = _find_nested(trak, (b"mdia", b"mdhd"))
    if mdhd is None:
        raise VideoScoringError("video_duration_invalid")
    timescale, media_duration = _timescale_duration(mdhd, error_code="video_duration_invalid")
    stts = _find_nested(trak, (b"mdia", b"minf", b"stbl", b"stts"))
    if stts is None or timescale <= 0 or media_duration <= 0:
        raise VideoScoringError("video_duration_invalid")
    sample_count = _sample_count(stts)
    if sample_count <= 0:
        raise VideoScoringError("video_duration_invalid")
    return {
        "width": width,
        "height": height,
        "timescale": timescale,
        "media_duration": media_duration,
        "sample_count": sample_count,
    }


def _handler_type(trak: bytes) -> bytes:
    hdlr = _find_nested(trak, (b"mdia", b"hdlr"))
    if hdlr is None or len(hdlr) < 12:
        return b""
    return hdlr[8:12]


def _timescale_duration(body: bytes, *, error_code: str) -> tuple[int, int]:
    if len(body) < 4:
        raise VideoScoringError(error_code)
    version = body[0]
    if version == 0:
        if len(body) < 20:
            raise VideoScoringError(error_code)
        timescale = int.from_bytes(body[12:16], "big")
        duration = int.from_bytes(body[16:20], "big")
        return timescale, duration
    if version == 1:
        if len(body) < 32:
            raise VideoScoringError(error_code)
        timescale = int.from_bytes(body[20:24], "big")
        duration = int.from_bytes(body[24:32], "big")
        return timescale, duration
    raise VideoScoringError(error_code)


def _dimensions(body: bytes) -> tuple[int, int]:
    if not body:
        raise VideoScoringError("video_resolution_invalid")
    version = body[0]
    width_at = 76 if version == 0 else 88 if version == 1 else None
    if width_at is None or len(body) < width_at + 8:
        raise VideoScoringError("video_resolution_invalid")
    width = int.from_bytes(body[width_at : width_at + 4], "big") >> 16
    height = int.from_bytes(body[width_at + 4 : width_at + 8], "big") >> 16
    return width, height


def _sample_count(body: bytes) -> int:
    if len(body) < 8:
        raise VideoScoringError("video_duration_invalid")
    entry_count = int.from_bytes(body[4:8], "big")
    total = 0
    offset = 8
    for _ in range(entry_count):
        if offset + 8 > len(body):
            raise VideoScoringError("video_duration_invalid")
        total += int.from_bytes(body[offset : offset + 4], "big")
        offset += 8
    return total


def _snap_rate(sample_count: int, timescale: int, media_duration: int) -> tuple[int, int, bool]:
    numerator = sample_count * timescale
    denominator = media_duration
    if numerator <= 0 or denominator <= 0:
        raise VideoScoringError("video_duration_invalid")
    divisor = math.gcd(numerator, denominator)
    reduced = (numerator // divisor, denominator // divisor)
    measured = reduced[0] / reduced[1]
    best: tuple[float, tuple[int, int]] | None = None
    for closed in CLOSED_FRAME_RATES:
        target = closed[0] / closed[1]
        relative = abs(measured - target) / target
        if relative < 0.01 and (best is None or relative < best[0]):
            best = (relative, closed)
    if best is not None:
        return best[1][0], best[1][1], True
    return reduced[0], reduced[1], False


def _child_map(body: bytes) -> dict[bytes, bytes]:
    found: dict[bytes, bytes] = {}
    for box_type, payload in _iter_boxes(body):
        found.setdefault(box_type, payload)
    return found


def _children_of_type(body: bytes, box_type: bytes) -> list[bytes]:
    return [payload for kind, payload in _iter_boxes(body) if kind == box_type]


def _find_nested(body: bytes, path: tuple[bytes, ...]) -> bytes | None:
    current = body
    for box_type in path:
        match = _child_map(current).get(box_type)
        if match is None:
            return None
        current = match
    return current


def _iter_boxes(body: bytes):
    offset = 0
    limit = len(body)
    while offset + 8 <= limit:
        box_size = int.from_bytes(body[offset : offset + 4], "big")
        box_type = body[offset + 4 : offset + 8]
        header_len = 8
        if box_size == 1:
            if offset + 16 > limit:
                raise VideoScoringError("video_moov_not_found")
            box_size = int.from_bytes(body[offset + 8 : offset + 16], "big")
            header_len = 16
        elif box_size == 0:
            box_size = limit - offset
        if box_size < header_len or offset + box_size > limit:
            raise VideoScoringError("video_moov_not_found")
        yield box_type, body[offset + header_len : offset + box_size]
        offset += box_size
