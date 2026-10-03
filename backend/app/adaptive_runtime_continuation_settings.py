"""Env-overridable caps for adaptive runtime symbolic continuation.

Counts, deadlines, intensity epsilon, and continuous-mode flags only. This
module never reads note events, harmony labels, or buffer JSON.
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass
from typing import Mapping

logger = logging.getLogger(__name__)

_LOGGED_CAPS = False
_LOGGED_CONTINUOUS = False

ADAPTIVE_CONTINUOUS_ENABLED_ENV = "ADAPTIVE_CONTINUOUS_ENABLED"
ADAPTIVE_ENGINE_CONTINUOUS_ENABLED_ENV = "ADAPTIVE_ENGINE_CONTINUOUS_ENABLED"
_TRUTHY = frozenset({"1", "true", "yes", "on"})
_RECOGNIZED_OFF = frozenset({"", "0"})


@dataclass(frozen=True)
class AdaptiveRuntimeContinuationSettings:
    """Safe defaults for the reserved window, the target window, and deadlines."""

    play_bars: int = 5
    generate_bars: int = 8
    prefix_bars: int = 8
    deadline_ms: int = 250
    latency_budget_ms: int = 50
    intensity_epsilon: float = 0.25
    max_buffer_events: int = 512
    adaptive_continuous_enabled: bool = False
    adaptive_engine_continuous_enabled: bool = False


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
    if isinstance(raw, bool):
        logger.warning(
            "Adaptive runtime continuation cap ignored non-integer env value",
            extra={"setting": key, "invalid": True},
        )
        return default
    try:
        parsed = int(str(raw).strip())
    except ValueError:
        logger.warning(
            "Adaptive runtime continuation cap ignored non-integer env value",
            extra={"setting": key, "invalid": True},
        )
        return default
    if parsed < floor or parsed > ceiling:
        logger.warning(
            "Adaptive runtime continuation cap ignored out-of-range env value",
            extra={"setting": key, "invalid": True},
        )
        return default
    return parsed


def _epsilon(source: dict[str, str], key: str, default: float) -> float:
    raw = source.get(key)
    if raw is None or str(raw).strip() == "":
        return default
    if isinstance(raw, bool):
        logger.warning(
            "Adaptive runtime continuation epsilon ignored non-numeric env value",
            extra={"setting": key, "invalid": True},
        )
        return default
    try:
        parsed = float(str(raw).strip())
    except ValueError:
        logger.warning(
            "Adaptive runtime continuation epsilon ignored non-numeric env value",
            extra={"setting": key, "invalid": True},
        )
        return default
    if not math.isfinite(parsed) or parsed <= 0.0 or parsed > 1.0:
        logger.warning(
            "Adaptive runtime continuation epsilon ignored out-of-range env value",
            extra={"setting": key, "invalid": True},
        )
        return default
    return parsed


def _parse_truthy(raw: str | None, *, code: str) -> bool:
    text = "" if raw is None else str(raw).strip().lower()
    if text in _TRUTHY:
        return True
    if text in _RECOGNIZED_OFF:
        return False
    logger.warning(
        "Adaptive continuous flag unrecognized; treating as off",
        extra={"code": code},
    )
    return False


def adaptive_continuous_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Studio continuous / virtual timeline flag. Default off."""
    source = env if env is not None else os.environ
    return _parse_truthy(
        source.get(ADAPTIVE_CONTINUOUS_ENABLED_ENV),
        code="adaptive_continuous_flag_unrecognized",
    )


def adaptive_engine_continuous_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Engine continuous maintain proxy flag. Default off."""
    source = env if env is not None else os.environ
    return _parse_truthy(
        source.get(ADAPTIVE_ENGINE_CONTINUOUS_ENABLED_ENV),
        code="adaptive_engine_continuous_flag_unrecognized",
    )


def load_adaptive_runtime_continuation_settings(
    env: dict[str, str] | None = None,
) -> AdaptiveRuntimeContinuationSettings:
    """Load caps and continuous flags.

    DEBUG logs numeric caps once per process. INFO logs continuous booleans
    once per process. Never logs MusicState or buffer material.
    """
    global _LOGGED_CAPS, _LOGGED_CONTINUOUS
    source = env if env is not None else os.environ
    continuous = adaptive_continuous_enabled(source)
    engine_continuous = adaptive_engine_continuous_enabled(source)
    settings = AdaptiveRuntimeContinuationSettings(
        play_bars=_positive_int(
            source,
            "ADAPTIVE_CONTINUATION_PLAY_BARS",
            5,
            floor=1,
            ceiling=64,
        ),
        generate_bars=_positive_int(
            source,
            "ADAPTIVE_CONTINUATION_GENERATE_BARS",
            8,
            floor=1,
            ceiling=64,
        ),
        prefix_bars=_positive_int(
            source,
            "ADAPTIVE_CONTINUATION_PREFIX_BARS",
            8,
            floor=1,
            ceiling=64,
        ),
        deadline_ms=_positive_int(
            source,
            "ADAPTIVE_CONTINUATION_DEADLINE_MS",
            250,
            floor=0,
            ceiling=60_000,
        ),
        latency_budget_ms=_positive_int(
            source,
            "ADAPTIVE_CONTINUATION_LATENCY_BUDGET_MS",
            50,
            floor=1,
            ceiling=5_000,
        ),
        intensity_epsilon=_epsilon(
            source,
            "ADAPTIVE_CONTINUATION_INTENSITY_EPSILON",
            0.25,
        ),
        max_buffer_events=_positive_int(
            source,
            "ADAPTIVE_CONTINUATION_MAX_BUFFER_EVENTS",
            512,
            floor=1,
            ceiling=4096,
        ),
        adaptive_continuous_enabled=continuous,
        adaptive_engine_continuous_enabled=engine_continuous,
    )
    if env is None and not _LOGGED_CAPS:
        _LOGGED_CAPS = True
        logger.debug(
            "Adaptive runtime continuation caps loaded",
            extra={
                "play_bars": settings.play_bars,
                "generate_bars": settings.generate_bars,
                "prefix_bars": settings.prefix_bars,
                "deadline_ms": settings.deadline_ms,
                "latency_budget_ms": settings.latency_budget_ms,
                "intensity_epsilon": settings.intensity_epsilon,
                "max_buffer_events": settings.max_buffer_events,
            },
        )
    if env is None and not _LOGGED_CONTINUOUS:
        _LOGGED_CONTINUOUS = True
        logger.info(
            "Adaptive continuous settings loaded",
            extra={
                "adaptive_continuous_enabled": settings.adaptive_continuous_enabled,
                "adaptive_engine_continuous_enabled": (
                    settings.adaptive_engine_continuous_enabled
                ),
            },
        )
    elif env is not None:
        logger.info(
            "Adaptive continuous settings loaded",
            extra={
                "adaptive_continuous_enabled": settings.adaptive_continuous_enabled,
                "adaptive_engine_continuous_enabled": (
                    settings.adaptive_engine_continuous_enabled
                ),
            },
        )
    return settings
