"""Plugin log wrapper.

Forwards INFO, DEBUG, WARNING, and ERROR to
``logging.getLogger("app.plugin_sdk.plugin." + plugin_id)``.

Drops keyword arguments named ``config``, ``prompt``, ``composition``, ``events``,
and ``audio``. Mapping keys that match the secret-name pattern
``(?i)(secret|password|token|api_key|apikey|authorization)`` have their values
replaced with ``"[redacted]"``. A redaction logs one DEBUG line with the key
names and not the values. Config values are never written as log text.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Mapping

_SECRET_KEY = re.compile(r"(?i)(secret|password|token|api_key|apikey|authorization)")
_DROPPED_KWARGS = frozenset({"config", "prompt", "composition", "events", "audio"})


@dataclass(frozen=True)
class PluginContext:
    """Values the host passes into ``register`` and later protocol calls."""

    api_version: str
    plugin_id: str
    config: dict[str, Any]
    logger: PluginLogger


class PluginLogger:
    """Logger adapter that redacts secret-like mapping keys and payload kwargs."""

    def __init__(self, plugin_id: str) -> None:
        self.plugin_id = plugin_id
        self._logger = logging.getLogger(f"app.plugin_sdk.plugin.{plugin_id}")

    def debug(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._emit(logging.DEBUG, msg, *args, **kwargs)

    def info(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._emit(logging.INFO, msg, *args, **kwargs)

    def warning(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._emit(logging.WARNING, msg, *args, **kwargs)

    def error(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._emit(logging.ERROR, msg, *args, **kwargs)

    def _emit(self, level: int, msg: str, *args: Any, **kwargs: Any) -> None:
        for name in _DROPPED_KWARGS:
            kwargs.pop(name, None)
        redacted_keys: list[str] = []
        if "extra" in kwargs and isinstance(kwargs["extra"], Mapping):
            kwargs["extra"] = _redact_mapping(kwargs["extra"], redacted_keys)
        safe_args = tuple(_redact_value(arg, redacted_keys) for arg in args)
        if redacted_keys:
            self._logger.debug(
                "plugin log redacted secret keys",
                extra={"plugin_id": self.plugin_id, "redacted_keys": redacted_keys},
            )
        self._logger.log(level, msg, *safe_args, **kwargs)


def _redact_value(value: Any, redacted_keys: list[str]) -> Any:
    if isinstance(value, Mapping):
        return _redact_mapping(value, redacted_keys)
    return value


def _redact_mapping(value: Mapping[str, Any], redacted_keys: list[str]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, item in value.items():
        key_text = str(key)
        if _SECRET_KEY.search(key_text):
            cleaned[key_text] = "[redacted]"
            redacted_keys.append(key_text)
            continue
        if isinstance(item, Mapping):
            cleaned[key_text] = _redact_mapping(item, redacted_keys)
        else:
            cleaned[key_text] = item
    return cleaned
