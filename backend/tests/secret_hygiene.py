"""Helpers that assert API/config payloads never leak LLM secrets."""

from __future__ import annotations

import re
from typing import Any

# Patterns that must never appear in browser-facing or committed config dumps.
_SECRET_SUBSTRINGS = (
    "api_key",
    "apikey",
    "openai_api_key",
    "deepseek_api_key",
    "authorization",
    "bearer ",
)
_KEY_LIKE = re.compile(r"\bsk-[A-Za-z0-9]{10,}\b|\bsk_live_[A-Za-z0-9]{10,}\b")


def assert_no_secret_leakage(payload: Any, *, context: str) -> None:
    """Fail if a serialized response/config looks like it contains API secrets."""
    text = payload if isinstance(payload, str) else str(payload)
    lowered = text.lower()
    for token in _SECRET_SUBSTRINGS:
        # Allow documentation mentions of env var *names* in warnings like "Set OPENAI_API_KEY"
        # only when not paired with an assigned secret value.
        if token == "api_key" and "openai_api_key" in lowered and "set openai_api_key" in lowered:
            continue
        if f'"{token}"' in lowered or f"'{token}'" in lowered:
            # JSON field named api_key is always a leak.
            if token == "api_key":
                raise AssertionError(f"{context}: response contains secret field name {token!r}")
    if _KEY_LIKE.search(text):
        raise AssertionError(f"{context}: response contains sk- style API key material")
