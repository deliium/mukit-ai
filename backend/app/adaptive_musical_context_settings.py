"""Env-overridable caps for adaptive musical context sessions.

Counts and the hysteresis gap only. This module never reads sample bodies.
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_LOGGED_CAPS = False


@dataclass(frozen=True)
class AdaptiveMusicalContextSettings:
    """Safe defaults for bindings, rules, and the Schmitt gap."""

    max_bindings: int = 32
    max_rules: int = 32
    max_values: int = 64
    max_tags: int = 32
    max_characters: int = 32
    max_custom: int = 32
    default_dwell: int = 3
    max_dwell: int = 64
    min_hysteresis_gap: float = 0.05


def _positive_int(
    source: dict[str, str],
    key: str,
    default: int,
    *,
    floor: int,
    ceiling: int,
) -> int:
    raw = source.get(key)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        parsed = int(str(raw).strip())
    except ValueError:
        logger.warning(
            "Adaptive musical context cap ignored non-integer env value",
            extra={"setting": key, "invalid": True},
        )
        return default
    if isinstance(raw, bool) or parsed < floor or parsed > ceiling:
        logger.warning(
            "Adaptive musical context cap ignored out-of-range env value",
            extra={"setting": key, "invalid": True},
        )
        return default
    return parsed


def _gap(source: dict[str, str], key: str, default: float) -> float:
    raw = source.get(key)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        parsed = float(str(raw).strip())
    except ValueError:
        logger.warning(
            "Adaptive musical context gap ignored non-numeric env value",
            extra={"setting": key, "invalid": True},
        )
        return default
    if not math.isfinite(parsed) or parsed < 0.0 or parsed > 0.5:
        logger.warning(
            "Adaptive musical context gap ignored out-of-range env value",
            extra={"setting": key, "invalid": True},
        )
        return default
    return parsed


def load_adaptive_musical_context_settings(
    env: dict[str, str] | None = None,
) -> AdaptiveMusicalContextSettings:
    """Load caps. DEBUG logs the numbers once per process when reading the environment."""
    global _LOGGED_CAPS
    source = env if env is not None else os.environ
    default_dwell = _positive_int(
        source,
        "ADAPTIVE_CONTEXT_DEFAULT_DWELL",
        3,
        floor=1,
        ceiling=64,
    )
    max_dwell = _positive_int(
        source,
        "ADAPTIVE_CONTEXT_MAX_DWELL",
        64,
        floor=1,
        ceiling=256,
    )
    if max_dwell < default_dwell:
        logger.warning(
            "Adaptive musical context max dwell is below the default dwell",
            extra={"setting": "ADAPTIVE_CONTEXT_MAX_DWELL", "invalid": True},
        )
        max_dwell = 64 if 64 >= default_dwell else default_dwell
    settings = AdaptiveMusicalContextSettings(
        max_bindings=_positive_int(
            source, "ADAPTIVE_CONTEXT_MAX_BINDINGS", 32, floor=1, ceiling=128
        ),
        max_rules=_positive_int(
            source, "ADAPTIVE_CONTEXT_MAX_RULES", 32, floor=1, ceiling=128
        ),
        max_values=_positive_int(
            source, "ADAPTIVE_CONTEXT_MAX_VALUES", 64, floor=1, ceiling=256
        ),
        max_tags=_positive_int(
            source, "ADAPTIVE_CONTEXT_MAX_TAGS", 32, floor=1, ceiling=64
        ),
        max_characters=_positive_int(
            source, "ADAPTIVE_CONTEXT_MAX_CHARACTERS", 32, floor=1, ceiling=64
        ),
        max_custom=_positive_int(
            source, "ADAPTIVE_CONTEXT_MAX_CUSTOM", 32, floor=1, ceiling=64
        ),
        default_dwell=default_dwell,
        max_dwell=max_dwell,
        min_hysteresis_gap=_gap(source, "ADAPTIVE_CONTEXT_MIN_HYSTERESIS_GAP", 0.05),
    )
    if env is None and not _LOGGED_CAPS:
        _LOGGED_CAPS = True
        logger.debug(
            "Adaptive musical context caps loaded",
            extra={
                "max_bindings": settings.max_bindings,
                "max_rules": settings.max_rules,
                "max_values": settings.max_values,
                "max_tags": settings.max_tags,
                "max_characters": settings.max_characters,
                "max_custom": settings.max_custom,
                "default_dwell": settings.default_dwell,
                "max_dwell": settings.max_dwell,
                "min_hysteresis_gap": settings.min_hysteresis_gap,
            },
        )
    return settings
