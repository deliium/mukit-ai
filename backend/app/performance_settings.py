"""Env flags for the performance conductor layer."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Mapping

logger = logging.getLogger(__name__)

_TRUTHY = frozenset({"1", "true", "yes", "on"})


@dataclass(frozen=True)
class PerformanceConductorSettings:
    ai_enabled: bool = False
    max_body_bytes: int = 65536
    max_name_length: int = 120


def load_performance_settings(
    env: Mapping[str, str] | None = None,
) -> PerformanceConductorSettings:
    source = env if env is not None else os.environ
    raw = str(source.get("PERFORMANCE_CONDUCTOR_AI_ENABLED", "")).strip().lower()
    ai_enabled = raw in _TRUTHY
    logger.debug(
        "Loaded performance conductor settings",
        extra={"ai_enabled": ai_enabled},
    )
    return PerformanceConductorSettings(ai_enabled=ai_enabled)
