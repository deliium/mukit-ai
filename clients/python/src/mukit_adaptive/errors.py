"""Public client failures.

``code`` is a server ``adaptive.engine.error.v1`` code or a local code
(``engine_client_dependency_missing``, ``engine_reconnect_exhausted``).
"""

from __future__ import annotations


class AdaptiveClientError(Exception):
    """Failure returned to the caller. The message never includes a token."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int | None = None,
        session_id: str | None = None,
        retry_after_ms: int | None = None,
        details: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.session_id = session_id
        self.retry_after_ms = retry_after_ms
        self.details = details
