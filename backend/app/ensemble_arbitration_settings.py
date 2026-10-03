"""``ENSEMBLE_*`` limits for multi-model candidate arbitration.

Preview is opt-in and default-off. This module does not open SQLite and does
not import ``ai_agents``. Runtime verbosity stays controlled by ``LOG_LEVEL``.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Mapping

logger = logging.getLogger(__name__)

ENSEMBLE_ARBITRATION_ENABLED_ENV = "ENSEMBLE_ARBITRATION_ENABLED"
ENSEMBLE_MAX_MODELS_ENV = "ENSEMBLE_MAX_MODELS"
ENSEMBLE_MAX_WALL_MS_ENV = "ENSEMBLE_MAX_WALL_MS"
ENSEMBLE_ALLOW_PARALLEL_ENV = "ENSEMBLE_ALLOW_PARALLEL"
ENSEMBLE_FAKE_ENV = "ENSEMBLE_FAKE"

_TRUTHY = frozenset({"1", "true", "yes", "on"})
_DEFAULT_MAX_MODELS = 3
_HARD_MAX_MODELS = 4
_DEFAULT_MAX_WALL_MS = 120_000
_HARD_MAX_WALL_MS = 600_000
_MIN_MODELS = 2


@dataclass(frozen=True)
class EnsembleArbitrationSettings:
    """Env-backed ensemble arbitration limits. No secrets."""

    enabled: bool
    fake: bool
    max_models: int
    max_wall_ms: int
    allow_parallel: bool


def _env(source: Mapping[str, str], key: str) -> str:
    raw = source.get(key)
    return "" if raw is None else str(raw).strip()


def _truthy(value: str) -> bool:
    return value.strip().lower() in _TRUTHY


def _clamp_int(
    raw: str,
    *,
    default: int,
    low: int,
    high: int,
    field_name: str,
) -> int:
    if not raw:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        logger.debug(
            "Ensemble arbitration setting ignored",
            extra={"field_name": field_name, "code": "ensemble_setting_invalid"},
        )
        return default
    if parsed < low:
        return low
    if parsed > high:
        return high
    return parsed


def hard_max_models() -> int:
    return _HARD_MAX_MODELS


def min_models() -> int:
    return _MIN_MODELS


def clamp_model_arity(count: int, *, max_models: int | None = None) -> int:
    """Clamp a model-list length to ``[2, max_models]`` (does not raise)."""
    ceiling = int(max_models) if max_models is not None else _DEFAULT_MAX_MODELS
    ceiling = min(max(ceiling, _MIN_MODELS), _HARD_MAX_MODELS)
    clamped = min(max(int(count), _MIN_MODELS), ceiling)
    if clamped != count:
        logger.debug(
            "Ensemble model arity clamped",
            extra={"field_name": "model_ids", "code": "ensemble_model_arity_clamped"},
        )
    return clamped


def clamp_top_n(top_n: int, *, max_models: int | None = None) -> int:
    """Clamp top_n to ``[1, max_models]`` (does not raise)."""
    ceiling = int(max_models) if max_models is not None else _DEFAULT_MAX_MODELS
    ceiling = min(max(ceiling, _MIN_MODELS), _HARD_MAX_MODELS)
    clamped = min(max(int(top_n), 1), ceiling)
    if clamped != top_n:
        logger.debug(
            "Ensemble top_n clamped",
            extra={"field_name": "top_n", "code": "ensemble_top_n_clamped"},
        )
    return clamped


def load_ensemble_arbitration_settings(
    env: Mapping[str, str] | None = None,
) -> EnsembleArbitrationSettings:
    """Resolve settings. Does not create directories and does not open SQLite."""
    source = env if env is not None else os.environ
    enabled = _truthy(_env(source, ENSEMBLE_ARBITRATION_ENABLED_ENV))
    fake = _truthy(_env(source, ENSEMBLE_FAKE_ENV))
    allow_parallel = _truthy(_env(source, ENSEMBLE_ALLOW_PARALLEL_ENV))
    max_models = _clamp_int(
        _env(source, ENSEMBLE_MAX_MODELS_ENV),
        default=_DEFAULT_MAX_MODELS,
        low=_MIN_MODELS,
        high=_HARD_MAX_MODELS,
        field_name=ENSEMBLE_MAX_MODELS_ENV,
    )
    max_wall_ms = _clamp_int(
        _env(source, ENSEMBLE_MAX_WALL_MS_ENV),
        default=_DEFAULT_MAX_WALL_MS,
        low=1_000,
        high=_HARD_MAX_WALL_MS,
        field_name=ENSEMBLE_MAX_WALL_MS_ENV,
    )
    settings = EnsembleArbitrationSettings(
        enabled=enabled,
        fake=fake,
        max_models=max_models,
        max_wall_ms=max_wall_ms,
        allow_parallel=allow_parallel,
    )
    logger.info(
        "Ensemble arbitration settings loaded",
        extra={
            "enabled": settings.enabled,
            "max_models": settings.max_models,
            "allow_parallel": settings.allow_parallel,
            "max_wall_ms": settings.max_wall_ms,
            "fake": settings.fake,
        },
    )
    return settings
