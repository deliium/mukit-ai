"""Env-overridable caps for ``adaptive.score.v1`` documents.

Counts only. This module never reads composition payloads.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_LOGGED_CAPS = False


@dataclass(frozen=True)
class AdaptiveScoreSettings:
    """Safe defaults matching the adaptive score architecture lock."""

    max_states: int = 32
    max_variants_per_state: int = 8
    max_transitions: int = 64
    max_layers: int = 32
    max_stingers: int = 32
    max_conditions_per_transition: int = 8
    max_name_length: int = 80
    max_body_bytes: int = 262144


def _positive_int(source: dict[str, str], key: str, default: int, *, ceiling: int) -> int:
    raw = source.get(key)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        parsed = int(str(raw).strip())
    except ValueError:
        logger.warning(
            "Adaptive score cap ignored non-integer env value",
            extra={"setting": key, "invalid": True},
        )
        return default
    if parsed < 1 or parsed > ceiling:
        logger.warning(
            "Adaptive score cap ignored out-of-range env value",
            extra={"setting": key, "invalid": True, "ceiling": ceiling},
        )
        return default
    return parsed


def load_adaptive_score_settings(
    env: dict[str, str] | None = None,
) -> AdaptiveScoreSettings:
    """Load caps. DEBUG logs the integers once per process."""
    global _LOGGED_CAPS
    source = env if env is not None else os.environ
    settings = AdaptiveScoreSettings(
        max_states=_positive_int(source, "ADAPTIVE_SCORE_MAX_STATES", 32, ceiling=256),
        max_variants_per_state=_positive_int(
            source, "ADAPTIVE_SCORE_MAX_VARIANTS_PER_STATE", 8, ceiling=64
        ),
        max_transitions=_positive_int(
            source, "ADAPTIVE_SCORE_MAX_TRANSITIONS", 64, ceiling=512
        ),
        max_layers=_positive_int(source, "ADAPTIVE_SCORE_MAX_LAYERS", 32, ceiling=256),
        max_stingers=_positive_int(source, "ADAPTIVE_SCORE_MAX_STINGERS", 32, ceiling=256),
        max_conditions_per_transition=_positive_int(
            source, "ADAPTIVE_SCORE_MAX_CONDITIONS", 8, ceiling=32
        ),
        max_name_length=_positive_int(
            source, "ADAPTIVE_SCORE_MAX_NAME_LENGTH", 80, ceiling=200
        ),
        max_body_bytes=_positive_int(
            source, "ADAPTIVE_SCORE_MAX_BODY_BYTES", 262144, ceiling=1_048_576
        ),
    )
    if env is None and not _LOGGED_CAPS:
        _LOGGED_CAPS = True
        logger.debug(
            "Adaptive score caps loaded",
            extra={
                "max_states": settings.max_states,
                "max_variants_per_state": settings.max_variants_per_state,
                "max_transitions": settings.max_transitions,
                "max_layers": settings.max_layers,
                "max_stingers": settings.max_stingers,
                "max_conditions_per_transition": settings.max_conditions_per_transition,
                "max_name_length": settings.max_name_length,
                "max_body_bytes": settings.max_body_bytes,
            },
        )
    return settings
