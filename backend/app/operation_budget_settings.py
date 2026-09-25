"""Env ceilings for an autonomous operation run.

A request may only lower these ceilings. Invalid values fall back to the
defaults. Currency is never invented here — cost fires only when an adapter
reports a number.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Mapping

logger = logging.getLogger(__name__)

DEFAULT_MAX_MODEL_CALLS = 32
DEFAULT_MAX_RUNTIME_MS = 180_000
DEFAULT_MAX_REVISIONS = 8
DEFAULT_NEURAL_AUDIO_MAX_ATTEMPTS = 2
ABSOLUTE_MAX_REVISIONS = 8
NEURAL_ATTEMPTS_MIN = 1
NEURAL_ATTEMPTS_MAX = 5

BUDGET_MODEL_CALL = "operation_model_call_budget"
BUDGET_RUNTIME = "operation_runtime_budget"
BUDGET_TOKEN = "operation_token_budget"
BUDGET_COST = "operation_cost_budget"
BUDGET_REVISION = "operation_revision_budget"
BUDGET_RENDER_ATTEMPT = "operation_render_attempt_budget"


@dataclass(frozen=True)
class OperationBudgetSettings:
    """Resolved numeric ceilings. ``None`` means that optional cap is off."""

    max_model_calls: int = DEFAULT_MAX_MODEL_CALLS
    max_runtime_ms: int = DEFAULT_MAX_RUNTIME_MS
    max_prompt_tokens: int | None = None
    remote_cost_micros: int | None = None
    max_revisions: int = DEFAULT_MAX_REVISIONS
    neural_audio_max_attempts: int = DEFAULT_NEURAL_AUDIO_MAX_ATTEMPTS


def _warn_invalid(key: str, error_type: str) -> None:
    logger.warning(
        "Operation budget env invalid",
        extra={"env_key": key, "error_type": error_type},
    )


def _parse_int(source: Mapping[str, str], key: str) -> tuple[int | None, bool]:
    """Return ``(value, present)``. Invalid text warns and returns ``(None, True)``."""
    raw = source.get(key)
    if raw is None or str(raw).strip() == "":
        return None, False
    try:
        return int(str(raw).strip()), True
    except ValueError:
        _warn_invalid(key, "ValueError")
        return None, True


def load_operation_budget_settings(
    env: Mapping[str, str] | None = None,
) -> OperationBudgetSettings:
    source = env if env is not None else os.environ

    model_calls, calls_set = _parse_int(source, "OPERATION_MAX_MODEL_CALLS")
    if not calls_set or model_calls is None:
        model_calls = DEFAULT_MAX_MODEL_CALLS
    elif model_calls < 0:
        _warn_invalid("OPERATION_MAX_MODEL_CALLS", "ValueError")
        model_calls = DEFAULT_MAX_MODEL_CALLS

    runtime_ms, runtime_set = _parse_int(source, "OPERATION_MAX_RUNTIME_MS")
    if not runtime_set or runtime_ms is None or runtime_ms < 1:
        if runtime_set:
            _warn_invalid("OPERATION_MAX_RUNTIME_MS", "ValueError")
        runtime_ms = DEFAULT_MAX_RUNTIME_MS

    prompt_tokens, prompt_set = _parse_int(source, "OPERATION_MAX_PROMPT_TOKENS")
    if not prompt_set:
        prompt_tokens = None
    elif prompt_tokens is None or prompt_tokens < 1:
        _warn_invalid("OPERATION_MAX_PROMPT_TOKENS", "ValueError")
        prompt_tokens = None

    cost_micros, cost_set = _parse_int(source, "OPERATION_REMOTE_COST_MICROS")
    if not cost_set:
        cost_micros = None
    elif cost_micros is None or cost_micros < 1:
        _warn_invalid("OPERATION_REMOTE_COST_MICROS", "ValueError")
        cost_micros = None

    revisions, revisions_set = _parse_int(source, "OPERATION_MAX_REVISIONS")
    if not revisions_set or revisions is None:
        revisions = DEFAULT_MAX_REVISIONS
    elif revisions < 0:
        _warn_invalid("OPERATION_MAX_REVISIONS", "ValueError")
        revisions = DEFAULT_MAX_REVISIONS
    elif revisions > ABSOLUTE_MAX_REVISIONS:
        logger.debug(
            "Operation revision ceiling clamped",
            extra={"env_key": "OPERATION_MAX_REVISIONS", "stored": ABSOLUTE_MAX_REVISIONS},
        )
        revisions = ABSOLUTE_MAX_REVISIONS

    attempts, attempts_set = _parse_int(source, "NEURAL_AUDIO_MAX_ATTEMPTS")
    if not attempts_set or attempts is None:
        attempts = DEFAULT_NEURAL_AUDIO_MAX_ATTEMPTS
    elif attempts < NEURAL_ATTEMPTS_MIN or attempts > NEURAL_ATTEMPTS_MAX:
        _warn_invalid("NEURAL_AUDIO_MAX_ATTEMPTS", "ValueError")
        attempts = DEFAULT_NEURAL_AUDIO_MAX_ATTEMPTS

    settings = OperationBudgetSettings(
        max_model_calls=model_calls,
        max_runtime_ms=runtime_ms,
        max_prompt_tokens=prompt_tokens,
        remote_cost_micros=cost_micros,
        max_revisions=revisions,
        neural_audio_max_attempts=attempts,
    )
    logger.debug(
        "Operation budget settings resolved",
        extra={
            "max_model_calls": settings.max_model_calls,
            "max_runtime_ms": settings.max_runtime_ms,
            "max_prompt_tokens": settings.max_prompt_tokens or 0,
            "remote_cost_micros": settings.remote_cost_micros or 0,
            "max_revisions": settings.max_revisions,
            "neural_audio_max_attempts": settings.neural_audio_max_attempts,
        },
    )
    return settings


def tighter_wall_ms(request_ms: int | None, settings: OperationBudgetSettings | None = None) -> int:
    """Env runtime ceiling, lowered when the request supplies a smaller wall."""
    cfg = settings or load_operation_budget_settings()
    if request_ms is None:
        return cfg.max_runtime_ms
    return min(int(request_ms), cfg.max_runtime_ms)


_LOADED = load_operation_budget_settings()
logger.info(
    "Operation budget settings loaded",
    extra={
        "max_model_calls": _LOADED.max_model_calls,
        "max_runtime_ms": _LOADED.max_runtime_ms,
        "max_prompt_tokens": _LOADED.max_prompt_tokens or 0,
        "remote_cost_micros": _LOADED.remote_cost_micros or 0,
        "max_revisions": _LOADED.max_revisions,
        "neural_audio_max_attempts": _LOADED.neural_audio_max_attempts,
    },
)
