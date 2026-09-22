"""Tests for critique domain error → HTTP mapping."""

from __future__ import annotations

from app.critique_schemas import (
    CRITIQUE_COMPLEXITY_EXCEEDED,
    CRITIQUE_INVALID_COMPOSITION,
    CRITIQUE_INVALID_SCOPE,
    CRITIQUE_MODEL_UNAVAILABLE,
    CRITIQUE_PAYLOAD_REJECTED,
    CritiqueError,
    map_critique_error_to_http,
)

_CASES = [
    (CRITIQUE_INVALID_COMPOSITION, 422),
    (CRITIQUE_INVALID_SCOPE, 422),
    (CRITIQUE_COMPLEXITY_EXCEEDED, 422),
    (CRITIQUE_MODEL_UNAVAILABLE, 503),
    (CRITIQUE_PAYLOAD_REJECTED, 422),
]


def test_critique_error_http_map():
    for code, status in _CASES:
        exc = CritiqueError("sanitized", code=code, fingerprint_prefix="abcdef123456")
        http_status, detail = map_critique_error_to_http(exc)
        assert http_status == status
        assert detail["code"] == code
        assert "message" in detail
        assert "prompt" not in str(detail).lower()
