"""Environment caps for durable Composer Profiles."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Mapping

logger = logging.getLogger(__name__)

_DEFAULT_MAX_PROFILES = 50
_DEFAULT_MAX_SOURCE_PROJECTS = 16
_DEFAULT_MAX_EXPORT_BYTES = 256 * 1024
_DEFAULT_FRAGMENT_MAX_CHARS = 1_200
_DEFAULT_MAX_LIST_ITEMS = 16

_caps_logged = False


@dataclass(frozen=True)
class ComposerProfileSettings:
    max_profiles: int
    max_source_projects: int
    max_export_bytes: int
    fragment_max_chars: int
    max_list_items: int


def load_composer_profile_settings(
    env: Mapping[str, str] | None = None,
) -> ComposerProfileSettings:
    """Load ``COMPOSER_PROFILE_*`` caps with safe clamped defaults."""
    global _caps_logged
    source = env if env is not None else os.environ
    settings = ComposerProfileSettings(
        max_profiles=_int_env(
            source,
            "COMPOSER_PROFILE_MAX_PROFILES",
            _DEFAULT_MAX_PROFILES,
            minimum=1,
            maximum=500,
        ),
        max_source_projects=_int_env(
            source,
            "COMPOSER_PROFILE_MAX_SOURCE_PROJECTS",
            _DEFAULT_MAX_SOURCE_PROJECTS,
            minimum=1,
            maximum=64,
        ),
        max_export_bytes=_int_env(
            source,
            "COMPOSER_PROFILE_MAX_EXPORT_BYTES",
            _DEFAULT_MAX_EXPORT_BYTES,
            minimum=4_096,
            maximum=2 * 1024 * 1024,
        ),
        fragment_max_chars=_int_env(
            source,
            "COMPOSER_PROFILE_FRAGMENT_MAX_CHARS",
            _DEFAULT_FRAGMENT_MAX_CHARS,
            minimum=64,
            maximum=8_192,
        ),
        max_list_items=_int_env(
            source,
            "COMPOSER_PROFILE_MAX_LIST_ITEMS",
            _DEFAULT_MAX_LIST_ITEMS,
            minimum=1,
            maximum=64,
        ),
    )
    if not _caps_logged or env is not None:
        logger.debug(
            "Composer profile settings loaded",
            extra={
                "max_profiles": settings.max_profiles,
                "max_source_projects": settings.max_source_projects,
                "max_export_bytes": settings.max_export_bytes,
                "fragment_max_chars": settings.fragment_max_chars,
                "max_list_items": settings.max_list_items,
            },
        )
        if env is None:
            _caps_logged = True
    return settings


def _int_env(
    source: Mapping[str, str],
    key: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    raw = source.get(key)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = int(str(raw).strip())
    except ValueError:
        logger.warning(
            "Invalid composer profile int env; using default",
            extra={"key": key, "default": default},
        )
        return default
    return max(minimum, min(maximum, value))
