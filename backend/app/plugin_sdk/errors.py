"""Stable plugin error codes. Messages name the code and never include payloads."""

from __future__ import annotations


class PluginError(Exception):
    """Host or SDK failure identified by a stable ``code``."""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)
