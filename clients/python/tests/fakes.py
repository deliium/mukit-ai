"""Shared doubles for the adaptive client tests."""

from __future__ import annotations

import json
import re
from pathlib import Path

from mukit_adaptive.client import HttpResult

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
PACKAGE_SRC = Path(__file__).resolve().parents[1] / "src"
_FORBIDDEN_IMPORTS = (
    re.compile(r"(^|[^\w])import app\b"),
    re.compile(r"(^|[^\w])from app\b"),
    re.compile(r"backend\.app"),
    re.compile(r"frontend/src"),
)


def load_json(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def session_document(**changes: object) -> dict:
    document = load_json("session.v1.json")
    document.update(changes)
    return document


def command_document(**changes: object) -> dict:
    document = load_json("command-result.v1.json")
    document.update(changes)
    return document


def error_document(
    code: str,
    message: str,
    *,
    session_id: str | None = None,
    retry_after_ms: int | None = None,
    details: dict | None = None,
) -> dict:
    return {
        "detail": {
            "schema_version": "adaptive.engine.error.v1",
            "code": code,
            "message": message,
            "session_id": session_id,
            "retry_after_ms": retry_after_ms,
            "details": details,
        }
    }


class RecordingTransport:
    """In-memory HTTP. Each response is consumed once."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._queued: list[tuple[int, bytes]] = []

    def push(self, status: int, body: object) -> None:
        if isinstance(body, bytes):
            payload = body
        else:
            payload = json.dumps(body).encode("utf-8")
        self._queued.append((status, payload))

    def request(self, method: str, url: str, headers: dict[str, str], body: bytes | None) -> HttpResult:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers),
                "body": body,
            }
        )
        if not self._queued:
            raise AssertionError(f"unexpected {method} {url}")
        status, payload = self._queued.pop(0)
        return HttpResult(status=status, body=payload)


def assert_package_has_no_studio_import() -> None:
    for path in PACKAGE_SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for pattern in _FORBIDDEN_IMPORTS:
            assert pattern.search(text) is None, f"{path} matches {pattern.pattern}"
