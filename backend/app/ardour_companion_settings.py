"""Deployment settings for the Ardour OSC companion.

Feature flag defaults off. Unrecognized truthy strings resolve to off.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Mapping

logger = logging.getLogger(__name__)

ENABLED_ENV = "ARDOUR_COMPANION_ENABLED"
FAKE_ENV = "ARDOUR_COMPANION_FAKE"
ALLOW_PUBLIC_HOSTS_ENV = "ARDOUR_COMPANION_ALLOW_PUBLIC_HOSTS"
DEFAULT_HOST_ENV = "ARDOUR_COMPANION_DEFAULT_HOST"
DEFAULT_OSC_PORT_ENV = "ARDOUR_COMPANION_DEFAULT_OSC_PORT"
DEFAULT_FEEDBACK_PORT_ENV = "ARDOUR_COMPANION_DEFAULT_FEEDBACK_PORT"
FEEDBACK_STALE_MS_ENV = "ARDOUR_COMPANION_FEEDBACK_STALE_MS"
COMMAND_TIMEOUT_MS_ENV = "ARDOUR_COMPANION_COMMAND_TIMEOUT_MS"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_OSC_PORT = 3819
DEFAULT_FEEDBACK_PORT = 8000
DEFAULT_FEEDBACK_STALE_MS = 3000
DEFAULT_COMMAND_TIMEOUT_MS = 2000

_TRUTHY = frozenset({"1", "true", "yes", "on"})
_RECOGNIZED_OFF = frozenset({"", "0"})

_default_logged = False


@dataclass(frozen=True)
class ArdourCompanionSettings:
    """Parsed ``ARDOUR_COMPANION_*`` environment values."""

    enabled: bool
    fake: bool
    allow_public_hosts: bool
    default_host: str
    default_osc_port: int
    default_feedback_port: int
    feedback_stale_ms: int
    command_timeout_ms: int


def _parse_truthy(raw: str | None) -> tuple[bool, bool]:
    text = "" if raw is None else str(raw).strip().lower()
    if text in _TRUTHY:
        return True, False
    if text in _RECOGNIZED_OFF:
        return False, False
    return False, True


def _clamp_int(raw: str | None, *, default: int, minimum: int, maximum: int) -> int:
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = int(str(raw).strip())
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def load_ardour_companion_settings(
    env: Mapping[str, str] | None = None,
) -> ArdourCompanionSettings:
    """Load companion settings from ``env`` or the process environment."""
    source = env if env is not None else os.environ

    enabled, unrecognized_enabled = _parse_truthy(source.get(ENABLED_ENV))
    if unrecognized_enabled:
        logger.info(
            "Ardour companion flag unrecognized; treating as off",
            extra={"code": "ardour_companion_flag_unrecognized"},
        )

    fake, unrecognized_fake = _parse_truthy(source.get(FAKE_ENV))
    if unrecognized_fake:
        logger.info(
            "Ardour companion fake flag unrecognized; treating as off",
            extra={"code": "ardour_companion_flag_unrecognized", "flag": "fake"},
        )
        fake = False

    allow_public, unrecognized_public = _parse_truthy(source.get(ALLOW_PUBLIC_HOSTS_ENV))
    if unrecognized_public:
        logger.info(
            "Ardour companion allow-public flag unrecognized; treating as off",
            extra={"code": "ardour_companion_flag_unrecognized", "flag": "allow_public"},
        )
        allow_public = False

    raw_host = source.get(DEFAULT_HOST_ENV)
    default_host = (
        DEFAULT_HOST
        if raw_host is None or str(raw_host).strip() == ""
        else str(raw_host).strip()
    )

    default_osc_port = _clamp_int(
        source.get(DEFAULT_OSC_PORT_ENV),
        default=DEFAULT_OSC_PORT,
        minimum=1,
        maximum=65535,
    )
    default_feedback_port = _clamp_int(
        source.get(DEFAULT_FEEDBACK_PORT_ENV),
        default=DEFAULT_FEEDBACK_PORT,
        minimum=1,
        maximum=65535,
    )
    if default_feedback_port == 3819:
        logger.warning(
            "Ardour companion default feedback port refused; using 8000",
            extra={"code": "ardour_payload_invalid", "port": 3819},
        )
        default_feedback_port = DEFAULT_FEEDBACK_PORT

    feedback_stale_ms = _clamp_int(
        source.get(FEEDBACK_STALE_MS_ENV),
        default=DEFAULT_FEEDBACK_STALE_MS,
        minimum=500,
        maximum=60_000,
    )
    command_timeout_ms = _clamp_int(
        source.get(COMMAND_TIMEOUT_MS_ENV),
        default=DEFAULT_COMMAND_TIMEOUT_MS,
        minimum=100,
        maximum=30_000,
    )

    settings = ArdourCompanionSettings(
        enabled=enabled,
        fake=fake,
        allow_public_hosts=allow_public,
        default_host=default_host,
        default_osc_port=default_osc_port,
        default_feedback_port=default_feedback_port,
        feedback_stale_ms=feedback_stale_ms,
        command_timeout_ms=command_timeout_ms,
    )

    global _default_logged
    payload = {
        "enabled": settings.enabled,
        "fake": settings.fake,
        "default_osc_port": settings.default_osc_port,
        "default_feedback_port": settings.default_feedback_port,
        "allow_public_hosts": settings.allow_public_hosts,
    }
    if env is None and not _default_logged:
        logger.debug("Ardour companion settings loaded", extra=payload)
        _default_logged = True
    elif env is not None:
        logger.debug("Ardour companion settings loaded", extra=payload)

    return settings
