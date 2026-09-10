"""Tests for composition.snapshot.v1 encoding."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.composition_schemas import CompositionV2
from app.services.composition_snapshot_encoding import (
    NULL_SNAPSHOT_FINGERPRINT,
    SNAPSHOT_COMPRESSION_PROFILE,
    SNAPSHOT_ENCODING_PROFILE,
    SnapshotEncodingError,
    canonical_snapshot_json_dumps,
    composition_snapshot_fingerprint,
    decode_composition_snapshot,
    encode_composition_snapshot,
)


FIXTURE_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "fixtures" / "composition_v2_expressive.json"
)


@pytest.fixture
def expressive_v2() -> CompositionV2:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return CompositionV2.model_validate(payload)


def test_null_snapshot_round_trip():
    encoded = encode_composition_snapshot(None)
    assert encoded.fingerprint == NULL_SNAPSHOT_FINGERPRINT
    assert encoded.encoding_profile == SNAPSHOT_ENCODING_PROFILE
    assert encoded.compression_profile == SNAPSHOT_COMPRESSION_PROFILE
    assert encoded.uncompressed_byte_size == 4
    assert encoded.compressed_byte_size == len(encoded.payload_zlib)
    assert encoded.compressed_byte_size > 0

    decoded = decode_composition_snapshot(
        fingerprint=encoded.fingerprint,
        encoding_profile=encoded.encoding_profile,
        compression_profile=encoded.compression_profile,
        payload_zlib=encoded.payload_zlib,
        uncompressed_byte_size=encoded.uncompressed_byte_size,
    )
    assert decoded is None


def test_canonical_bytes_stable_across_key_ordering(expressive_v2):
    original = expressive_v2.model_dump(mode="json")
    reordered = {key: original[key] for key in reversed(list(original.keys()))}
    left = CompositionV2.model_validate(original)
    right = CompositionV2.model_validate(reordered)

    assert composition_snapshot_fingerprint(left) == composition_snapshot_fingerprint(right)
    assert canonical_snapshot_json_dumps(original) == canonical_snapshot_json_dumps(reordered)


def test_compression_round_trip_and_dedupe_identity(expressive_v2):
    first = encode_composition_snapshot(expressive_v2)
    second = encode_composition_snapshot(copy.deepcopy(expressive_v2))
    assert first.fingerprint == second.fingerprint
    assert first.payload_zlib == second.payload_zlib
    assert first.compressed_byte_size < first.uncompressed_byte_size

    decoded = decode_composition_snapshot(
        fingerprint=first.fingerprint,
        encoding_profile=first.encoding_profile,
        compression_profile=first.compression_profile,
        payload_zlib=first.payload_zlib,
        uncompressed_byte_size=first.uncompressed_byte_size,
    )
    assert decoded is not None
    assert decoded.model_dump(mode="json") == expressive_v2.model_dump(mode="json")


def test_decode_rejects_tampered_fingerprint(expressive_v2):
    encoded = encode_composition_snapshot(expressive_v2)
    with pytest.raises(SnapshotEncodingError, match="fingerprint"):
        decode_composition_snapshot(
            fingerprint="0" * 64,
            encoding_profile=encoded.encoding_profile,
            compression_profile=encoded.compression_profile,
            payload_zlib=encoded.payload_zlib,
            uncompressed_byte_size=encoded.uncompressed_byte_size,
        )


def test_decode_rejects_size_mismatch(expressive_v2):
    encoded = encode_composition_snapshot(expressive_v2)
    with pytest.raises(SnapshotEncodingError, match="uncompressed size"):
        decode_composition_snapshot(
            fingerprint=encoded.fingerprint,
            encoding_profile=encoded.encoding_profile,
            compression_profile=encoded.compression_profile,
            payload_zlib=encoded.payload_zlib,
            uncompressed_byte_size=encoded.uncompressed_byte_size + 1,
        )
