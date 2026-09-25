"""PLUGIN_* caps. Invalid integers log the env name and fall back to the default."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Mapping

logger = logging.getLogger(__name__)

_DEFAULT_MAX_COUNT = 32
_DEFAULT_MAX_MANIFEST_BYTES = 65536
_DEFAULT_MAX_EXPORT_BYTES = 2_000_000
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}
_SENSITIVE_SEGMENT = ("key", "token", "secret")


@dataclass(frozen=True)
class PluginSettings:
    """Process plugin caps. Empty ``paths`` disables loading."""

    paths: tuple[str, ...]
    max_count: int
    max_manifest_bytes: int
    max_export_bytes: int
    reload_enabled: bool

    @property
    def enabled(self) -> bool:
        return bool(self.paths)

    @property
    def path_count(self) -> int:
        return len(self.paths)


def load_plugin_settings(env: Mapping[str, str] | None = None) -> PluginSettings:
    """Read plugin caps from ``env`` or ``os.environ``."""
    source = env if env is not None else os.environ
    paths = _parse_paths(source.get("PLUGIN_PATHS"))
    settings = PluginSettings(
        paths=paths,
        max_count=_int_env(source, "PLUGIN_MAX_COUNT", _DEFAULT_MAX_COUNT),
        max_manifest_bytes=_int_env(source, "PLUGIN_MAX_MANIFEST_BYTES", _DEFAULT_MAX_MANIFEST_BYTES),
        max_export_bytes=_int_env(source, "PLUGIN_MAX_EXPORT_BYTES", _DEFAULT_MAX_EXPORT_BYTES),
        reload_enabled=_bool_env(source, "PLUGIN_RELOAD_ENABLED", default=True),
    )
    logger.info(
        "plugin settings loaded",
        extra={
            "enabled": settings.enabled,
            "path_count": settings.path_count,
            "max_count": settings.max_count,
            "max_manifest_bytes": settings.max_manifest_bytes,
            "max_export_bytes": settings.max_export_bytes,
            "reload_enabled": settings.reload_enabled,
        },
    )
    return settings


def _parse_paths(raw: str | None) -> tuple[str, ...]:
    if raw is None or not str(raw).strip():
        return ()
    parts = tuple(part.strip() for part in str(raw).split(os.pathsep) if part.strip())
    sensitive = sum(1 for part in parts if _segment_is_sensitive(part))
    if sensitive:
        logger.debug(
            "plugin path entries omitted from logs",
            extra={"sensitive_path_count": sensitive, "path_count": len(parts)},
        )
    return parts


def _segment_is_sensitive(path: str) -> bool:
    lowered = path.lower()
    return any(token in lowered for token in _SENSITIVE_SEGMENT)


def _bool_env(env: Mapping[str, str], key: str, *, default: bool) -> bool:
    raw = (env.get(key) or "").strip().lower()
    if not raw:
        return default
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    logger.warning("Invalid plugin bool env; using default", extra={"key": key})
    return default


def _int_env(env: Mapping[str, str], key: str, default: int) -> int:
    raw = (env.get(key) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid plugin int env; using default", extra={"key": key})
        return default
    if value < 1:
        logger.warning("Invalid plugin int env; using default", extra={"key": key})
        return default
    return value
