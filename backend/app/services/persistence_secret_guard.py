"""Reject secret field names and credential-like values before persistence."""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Mapping

logger = logging.getLogger(__name__)

FORBIDDEN_SECRET_FIELD_NAMES = {
    "api_key",
    "apikey",
    "openai_api_key",
    "deepseek_api_key",
    "authorization",
    "access_token",
    "secret",
    "password",
    "bearer",
    "token",
}

# Conservative patterns for common provider tokens / bearer credentials.
_SECRET_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bsk-proj-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-+/=]{16,}\b", re.IGNORECASE),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z\-_]{20,}\b"),
)


class PersistenceSecretError(ValueError):
    """Raised when a persisted payload contains secret fields or values."""

    def __init__(self, message: str, *, code: str = "persistence_secret_rejected") -> None:
        super().__init__(message)
        self.code = code


def contains_forbidden_secret_fields(payload: Any, *, path: str = "") -> list[str]:
    """Return dotted paths of API-key-like fields found in a nested payload."""
    found: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_path = f"{path}.{key}" if path else str(key)
            normalized = str(key).strip().lower().replace("-", "_")
            if normalized in FORBIDDEN_SECRET_FIELD_NAMES or normalized.endswith("_api_key"):
                found.append(key_path)
            found.extend(contains_forbidden_secret_fields(value, path=key_path))
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            found.extend(
                contains_forbidden_secret_fields(item, path=f"{path}[{index}]")
            )
    return found


def configured_provider_secrets(env: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """Exact configured provider secret values (non-empty only)."""
    source = env if env is not None else os.environ
    values: list[str] = []
    for key in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY"):
        raw = source.get(key)
        if isinstance(raw, str):
            cleaned = raw.strip()
            if cleaned and cleaned.lower() not in {"fake", "unused", "changeme"}:
                values.append(cleaned)
    return tuple(values)


def find_secret_value_hits(
    text: str,
    *,
    env: Mapping[str, str] | None = None,
) -> list[str]:
    """Return opaque hit codes for credential-like values (never the secrets)."""
    if not text:
        return []
    hits: list[str] = []
    for secret in configured_provider_secrets(env):
        if secret and secret in text:
            hits.append("configured_provider_secret")
            break
    for pattern in _SECRET_VALUE_PATTERNS:
        if pattern.search(text):
            hits.append(f"pattern:{pattern.pattern[:24]}")
    return hits


def collect_string_leaves(payload: Any, *, path: str = "") -> list[tuple[str, str]]:
    """Collect (path, string_value) pairs from nested JSON-like data."""
    found: list[tuple[str, str]] = []
    if isinstance(payload, str):
        found.append((path or "<root>", payload))
    elif isinstance(payload, dict):
        for key, value in payload.items():
            key_path = f"{path}.{key}" if path else str(key)
            found.extend(collect_string_leaves(value, path=key_path))
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            found.extend(collect_string_leaves(item, path=f"{path}[{index}]"))
    return found


def assert_no_secret_fields(payload: Any, *, context: str) -> None:
    secrets = contains_forbidden_secret_fields(payload)
    if secrets:
        logger.warning(
            "Rejected payload containing secret-like fields",
            extra={
                "context": context,
                "code": "forbidden_secret_field",
                "secret_field_count": len(secrets),
            },
        )
        raise PersistenceSecretError(
            "Payload must not include API keys or secret fields "
            f"(found: {', '.join(secrets[:5])})",
            code="forbidden_secret_field",
        )


def assert_no_secret_values(
    text: str,
    *,
    field_name: str,
    env: Mapping[str, str] | None = None,
) -> None:
    hits = find_secret_value_hits(text, env=env)
    if hits:
        logger.warning(
            "Rejected free text containing credential-like values",
            extra={
                "field_name": field_name,
                "code": "forbidden_secret_value",
                "hit_count": len(hits),
            },
        )
        raise PersistenceSecretError(
            f"Field '{field_name}' must not contain API keys or credentials",
            code="forbidden_secret_value",
        )


def assert_payload_has_no_secret_values(
    payload: Any,
    *,
    context: str,
    env: Mapping[str, str] | None = None,
) -> None:
    """Scan all nested strings for configured secrets and common token patterns."""
    assert_no_secret_fields(payload, context=context)
    for path, value in collect_string_leaves(payload):
        hits = find_secret_value_hits(value, env=env)
        if hits:
            logger.warning(
                "Rejected nested free text containing credential-like values",
                extra={
                    "context": context,
                    "field_name": path,
                    "code": "forbidden_secret_value",
                    "hit_count": len(hits),
                },
            )
            raise PersistenceSecretError(
                f"Field '{path}' must not contain API keys or credentials",
                code="forbidden_secret_value",
            )
