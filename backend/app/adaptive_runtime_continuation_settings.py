"""Env-overridable caps for adaptive runtime symbolic continuation.

Counts, deadlines, and the intensity epsilon only. This module never reads
note events, harmony labels, or buffer JSON.
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_LOGGED_CAPS = False


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


def load_adaptive_runtime_continuation_settings(
    env: dict[str, str] | None = None,
) -> AdaptiveRuntimeContinuationSettings:
    """Load caps. DEBUG logs the numbers once per process when reading the environment."""
    global _LOGGED_CAPS
    source = env if env is not None else os.environ
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
    return settings
