"""Deployment flag for explicit preference learning.

Unset, empty, and ``0`` leave learning off. ``1``, ``true``, ``yes``, and
``on`` (any case) turn it on. Any other string is treated as off and logged
by code only. The raw value is never logged.
"""

from __future__ import annotations

import logging
import os
from typing import Mapping

logger = logging.getLogger(__name__)

PREFERENCE_LEARNING_ENABLED_ENV = "PREFERENCE_LEARNING_ENABLED"
_TRUTHY = frozenset({"1", "true", "yes", "on"})
_RECOGNIZED_OFF = frozenset({"", "0"})

_default_logged = False


def preference_learning_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Return whether the deployment flag allows collection and ranking.

    Logs INFO once for the process-default environment. An unrecognized
    value logs WARNING with code ``preference_flag_unrecognized`` and
    resolves to off.
    """
    source = env if env is not None else os.environ
    raw = source.get(PREFERENCE_LEARNING_ENABLED_ENV)
    text = "" if raw is None else str(raw).strip().lower()
    if text in _TRUTHY:
        enabled = True
        unrecognized = False
    elif text in _RECOGNIZED_OFF:
        enabled = False
        unrecognized = False
    else:
        enabled = False
        unrecognized = True

    if unrecognized:
        logger.warning(
            "Preference learning flag unrecognized; treating as off",
            extra={"code": "preference_flag_unrecognized"},
        )

    global _default_logged
    if env is None and not _default_logged:
        logger.info("Preference learning settings loaded", extra={"enabled": enabled})
        _default_logged = True
    elif env is not None:
        logger.info("Preference learning settings loaded", extra={"enabled": enabled})

    return enabled
