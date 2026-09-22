"""Env-tunable thresholds for the controlled critique revision loop."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Cost-class ordinal for peak tracking (never dollars).
_COST_CLASS_RANK: dict[str, int] = {
    "free_local": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
}


@dataclass(frozen=True)
class RevisionLoopSettings:
    """Configurable constants — not scattered magic numbers."""

    improvement_min_delta: int = 1
    hard_weight: int = 100
    technical_weight: int = 10
    on_failure: str = "keep_last_valid_and_stop"
    stop_when_hard_ok: bool = True
    revise_on_technical: bool = False
    # Default wall budgets per mode (ms); None = no wall budget.
    max_wall_ms_off: int | None = None
    max_wall_ms_fast: int = 60_000
    max_wall_ms_balanced: int = 120_000
    max_wall_ms_thorough: int = 180_000
    max_prompt_tokens_fast: int | None = 8_000
    max_prompt_tokens_balanced: int | None = 24_000
    max_prompt_tokens_thorough: int | None = 48_000


def load_revision_loop_settings(env: dict[str, str] | None = None) -> RevisionLoopSettings:
    source = env if env is not None else os.environ

    def _int(key: str, default: int) -> int:
        raw = source.get(key)
        if raw is None or str(raw).strip() == "":
            return default
        try:
            return int(raw)
        except ValueError:
            return default

    def _optional_int(key: str, default: int | None) -> int | None:
        raw = source.get(key)
        if raw is None or str(raw).strip() == "":
            return default
        try:
            return int(raw)
        except ValueError:
            return default

    def _bool(key: str, default: bool) -> bool:
        raw = source.get(key)
        if raw is None or str(raw).strip() == "":
            return default
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}

    settings = RevisionLoopSettings(
        improvement_min_delta=max(0, _int("REVISION_LOOP_IMPROVEMENT_MIN_DELTA", 1)),
        hard_weight=max(1, _int("REVISION_LOOP_HARD_WEIGHT", 100)),
        technical_weight=max(0, _int("REVISION_LOOP_TECHNICAL_WEIGHT", 10)),
        on_failure=str(
            source.get("REVISION_LOOP_ON_FAILURE") or "keep_last_valid_and_stop"
        ).strip()
        or "keep_last_valid_and_stop",
        stop_when_hard_ok=_bool("REVISION_LOOP_STOP_WHEN_HARD_OK", True),
        revise_on_technical=_bool("REVISION_LOOP_REVISE_ON_TECHNICAL", False),
        max_wall_ms_fast=_optional_int("REVISION_LOOP_MAX_WALL_MS_FAST", 60_000),
        max_wall_ms_balanced=_optional_int("REVISION_LOOP_MAX_WALL_MS_BALANCED", 120_000),
        max_wall_ms_thorough=_optional_int("REVISION_LOOP_MAX_WALL_MS_THOROUGH", 180_000),
        max_prompt_tokens_fast=_optional_int("REVISION_LOOP_MAX_PROMPT_TOKENS_FAST", 8_000),
        max_prompt_tokens_balanced=_optional_int(
            "REVISION_LOOP_MAX_PROMPT_TOKENS_BALANCED", 24_000
        ),
        max_prompt_tokens_thorough=_optional_int(
            "REVISION_LOOP_MAX_PROMPT_TOKENS_THOROUGH", 48_000
        ),
    )
    logger.debug(
        "Revision loop settings loaded",
        extra={
            "improvement_min_delta": settings.improvement_min_delta,
            "stop_when_hard_ok": settings.stop_when_hard_ok,
            "revise_on_technical": settings.revise_on_technical,
        },
    )
    return settings


def default_wall_ms_for_mode(mode: str, settings: RevisionLoopSettings) -> int | None:
    key = str(mode).strip().lower()
    if key == "fast":
        return settings.max_wall_ms_fast
    if key == "balanced":
        return settings.max_wall_ms_balanced
    if key == "thorough":
        return settings.max_wall_ms_thorough
    return settings.max_wall_ms_off


def default_prompt_token_cap_for_mode(mode: str, settings: RevisionLoopSettings) -> int | None:
    key = str(mode).strip().lower()
    if key == "fast":
        return settings.max_prompt_tokens_fast
    if key == "balanced":
        return settings.max_prompt_tokens_balanced
    if key == "thorough":
        return settings.max_prompt_tokens_thorough
    return None


def cost_class_rank(cost_class: str | None) -> int:
    if not cost_class:
        return -1
    return _COST_CLASS_RANK.get(str(cost_class).strip().lower(), -1)
