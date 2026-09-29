"""Env-overridable caps for the external adaptive engine.

Counts, rates, and the ticker mode only. This module never reads the bearer
token, command bodies, or context samples.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_LOGGED_CAPS = False


@dataclass(frozen=True)
class AdaptiveEngineSettings:
    """Safe defaults for the session clock, buckets, and socket caps."""

    tick_ms: int = 50
    ticker: str = "auto"
    context_hz: int = 20
    context_burst: int = 40
    command_hz: int = 8
    command_burst: int = 8
    status_hz: int = 10
    max_sessions: int = 4
    event_queue: int = 32


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
            "Adaptive engine setting ignored non-integer env value",
            extra={"setting": key, "invalid": True},
        )
        return default
    if isinstance(raw, bool) or parsed < floor or parsed > ceiling:
        logger.warning(
            "Adaptive engine setting ignored out-of-range env value",
            extra={"setting": key, "invalid": True},
        )
        return default
    return parsed


def _ticker(source: dict[str, str]) -> str:
    raw = source.get("ADAPTIVE_ENGINE_TICKER")
    if raw is None or str(raw).strip() == "":
        return "auto"
    value = str(raw).strip()
    if value not in {"auto", "manual"}:
        logger.warning(
            "Adaptive engine setting ignored unrecognized ticker mode",
            extra={"setting": "ADAPTIVE_ENGINE_TICKER", "invalid": True},
        )
        return "auto"
    return value


def load_adaptive_engine_settings(
    env: dict[str, str] | None = None,
) -> AdaptiveEngineSettings:
    """Load caps. DEBUG logs the numbers once per process when reading the environment."""
    global _LOGGED_CAPS
    source = env if env is not None else os.environ
    settings = AdaptiveEngineSettings(
        tick_ms=_positive_int(source, "ADAPTIVE_ENGINE_TICK_MS", 50, floor=10, ceiling=1000),
        ticker=_ticker(source),
        context_hz=_positive_int(source, "ADAPTIVE_ENGINE_CONTEXT_HZ", 20, floor=1, ceiling=1000),
        context_burst=_positive_int(
            source, "ADAPTIVE_ENGINE_CONTEXT_BURST", 40, floor=1, ceiling=10000
        ),
        command_hz=_positive_int(source, "ADAPTIVE_ENGINE_COMMAND_HZ", 8, floor=1, ceiling=1000),
        command_burst=_positive_int(
            source, "ADAPTIVE_ENGINE_COMMAND_BURST", 8, floor=1, ceiling=10000
        ),
        status_hz=_positive_int(source, "ADAPTIVE_ENGINE_STATUS_HZ", 10, floor=1, ceiling=1000),
        max_sessions=_positive_int(source, "ADAPTIVE_ENGINE_MAX_SESSIONS", 4, floor=1, ceiling=64),
        event_queue=_positive_int(source, "ADAPTIVE_ENGINE_EVENT_QUEUE", 32, floor=1, ceiling=256),
    )
    if env is None and not _LOGGED_CAPS:
        _LOGGED_CAPS = True
        logger.debug(
            "Adaptive engine caps loaded",
            extra={
                "tick_ms": settings.tick_ms,
                "ticker": settings.ticker,
                "context_hz": settings.context_hz,
                "context_burst": settings.context_burst,
                "command_hz": settings.command_hz,
                "command_burst": settings.command_burst,
                "status_hz": settings.status_hz,
                "max_sessions": settings.max_sessions,
                "event_queue": settings.event_queue,
            },
        )
    return settings
