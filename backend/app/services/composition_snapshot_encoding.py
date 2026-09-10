"""Versioned content-addressed encoding for composition.snapshot.v1."""

from __future__ import annotations

import hashlib
import json
import logging
import zlib
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.composition_schemas import CompositionV2

logger = logging.getLogger(__name__)

SNAPSHOT_ENCODING_PROFILE = "composition.snapshot.v1"
SNAPSHOT_COMPRESSION_PROFILE = "zlib"
NULL_SNAPSHOT_FINGERPRINT = f"{SNAPSHOT_ENCODING_PROFILE}:null"
SNAPSHOT_FINGERPRINT_LOG_PREFIX_LEN = 12


class SnapshotEncodingError(ValueError):
    """Raised when snapshot encode/decode fails validation or integrity checks."""


@dataclass(frozen=True)
class EncodedCompositionSnapshot:
    fingerprint: str
    encoding_profile: str
    compression_profile: str
    payload_zlib: bytes
    uncompressed_byte_size: int
    compressed_byte_size: int
    composition: CompositionV2 | None


def snapshot_fingerprint_log_prefix(fingerprint: str) -> str:
    """Short prefix safe for structured logs."""
    return fingerprint[:SNAPSHOT_FINGERPRINT_LOG_PREFIX_LEN]


def canonical_snapshot_json_dumps(value: Any) -> str:
    """Compact UTF-8 JSON with recursively sorted object keys; array order preserved."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash_profile_and_bytes(canonical_bytes: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(SNAPSHOT_ENCODING_PROFILE.encode("utf-8"))
    digest.update(b"\0")
    digest.update(canonical_bytes)
    return digest.hexdigest()


def encode_composition_snapshot(
    composition: CompositionV2 | None,
) -> EncodedCompositionSnapshot:
    """Encode a Composition V2 (or null empty composition) as a zlib snapshot."""
    if composition is None:
        canonical_bytes = b"null"
        fingerprint = NULL_SNAPSHOT_FINGERPRINT
        payload = zlib.compress(canonical_bytes, level=9)
        encoded = EncodedCompositionSnapshot(
            fingerprint=fingerprint,
            encoding_profile=SNAPSHOT_ENCODING_PROFILE,
            compression_profile=SNAPSHOT_COMPRESSION_PROFILE,
            payload_zlib=payload,
            uncompressed_byte_size=len(canonical_bytes),
            compressed_byte_size=len(payload),
            composition=None,
        )
        logger.debug(
            "Encoded null composition snapshot",
            extra={
                "fingerprint_prefix": snapshot_fingerprint_log_prefix(fingerprint),
                "encoding_profile": SNAPSHOT_ENCODING_PROFILE,
                "compression_profile": SNAPSHOT_COMPRESSION_PROFILE,
                "uncompressed_byte_size": encoded.uncompressed_byte_size,
                "compressed_byte_size": encoded.compressed_byte_size,
            },
        )
        return encoded

    document = composition.model_dump(mode="json")
    canonical = canonical_snapshot_json_dumps(document)
    canonical_bytes = canonical.encode("utf-8")
    fingerprint = _hash_profile_and_bytes(canonical_bytes)
    payload = zlib.compress(canonical_bytes, level=9)
    encoded = EncodedCompositionSnapshot(
        fingerprint=fingerprint,
        encoding_profile=SNAPSHOT_ENCODING_PROFILE,
        compression_profile=SNAPSHOT_COMPRESSION_PROFILE,
        payload_zlib=payload,
        uncompressed_byte_size=len(canonical_bytes),
        compressed_byte_size=len(payload),
        composition=composition,
    )
    logger.debug(
        "Encoded composition snapshot",
        extra={
            "fingerprint_prefix": snapshot_fingerprint_log_prefix(fingerprint),
            "encoding_profile": SNAPSHOT_ENCODING_PROFILE,
            "compression_profile": SNAPSHOT_COMPRESSION_PROFILE,
            "uncompressed_byte_size": encoded.uncompressed_byte_size,
            "compressed_byte_size": encoded.compressed_byte_size,
            "track_count": len(composition.tracks),
            "event_count": sum(len(track.events) for track in composition.tracks),
        },
    )
    return encoded


def decode_composition_snapshot(
    *,
    fingerprint: str,
    encoding_profile: str,
    compression_profile: str,
    payload_zlib: bytes,
    uncompressed_byte_size: int | None = None,
) -> CompositionV2 | None:
    """Decompress and validate a stored snapshot; null fingerprint yields None."""
    logger.debug(
        "Decoding composition snapshot",
        extra={
            "fingerprint_prefix": snapshot_fingerprint_log_prefix(fingerprint),
            "encoding_profile": encoding_profile,
            "compression_profile": compression_profile,
            "compressed_byte_size": len(payload_zlib),
        },
    )
    if encoding_profile != SNAPSHOT_ENCODING_PROFILE:
        raise SnapshotEncodingError(
            f"Unsupported snapshot encoding profile: {encoding_profile}"
        )
    if compression_profile != SNAPSHOT_COMPRESSION_PROFILE:
        raise SnapshotEncodingError(
            f"Unsupported snapshot compression profile: {compression_profile}"
        )

    try:
        canonical_bytes = zlib.decompress(payload_zlib)
    except zlib.error as exc:
        raise SnapshotEncodingError("Snapshot zlib payload is corrupt") from exc

    if (
        uncompressed_byte_size is not None
        and len(canonical_bytes) != uncompressed_byte_size
    ):
        raise SnapshotEncodingError("Snapshot uncompressed size mismatch")

    if fingerprint == NULL_SNAPSHOT_FINGERPRINT:
        if canonical_bytes != b"null":
            raise SnapshotEncodingError("Null snapshot payload must be literal null")
        logger.debug(
            "Decoded null composition snapshot",
            extra={
                "fingerprint_prefix": snapshot_fingerprint_log_prefix(fingerprint),
            },
        )
        return None

    expected = _hash_profile_and_bytes(canonical_bytes)
    if fingerprint != expected:
        raise SnapshotEncodingError("Snapshot fingerprint does not match payload")

    try:
        text = canonical_bytes.decode("utf-8")
        payload = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SnapshotEncodingError("Snapshot payload is not valid UTF-8 JSON") from exc

    if payload is None:
        raise SnapshotEncodingError("Non-null snapshot fingerprint decoded to JSON null")

    try:
        composition = CompositionV2.model_validate(payload)
    except ValidationError as exc:
        raise SnapshotEncodingError("Snapshot payload is not a valid Composition V2") from exc

    # Re-canonicalize to defend against non-sorted stored bytes that somehow hashed.
    round_trip = encode_composition_snapshot(composition)
    if round_trip.fingerprint != fingerprint:
        raise SnapshotEncodingError("Snapshot failed Composition V2 round-trip identity")

    logger.debug(
        "Decoded composition snapshot",
        extra={
            "fingerprint_prefix": snapshot_fingerprint_log_prefix(fingerprint),
            "track_count": len(composition.tracks),
            "event_count": sum(len(track.events) for track in composition.tracks),
        },
    )
    return composition


def composition_snapshot_fingerprint(composition: CompositionV2 | None) -> str:
    """Return the content-addressed fingerprint without requiring storage."""
    return encode_composition_snapshot(composition).fingerprint
