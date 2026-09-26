"""Env ceilings for one autonomous composition run.

A request may only lower ``AUTONOMOUS_MAX_AGENT_OPERATIONS``. Invalid values
fall back to the defaults. ``0`` disables the agent-operation ceiling.
"""

import logging
import os
from dataclasses import dataclass
from typing import Mapping

logger = logging.getLogger(__name__)

DEFAULT_MAX_AGENT_OPERATIONS = 24
DEFAULT_MAX_RUNS_PER_PROJECT = 20

ENV_MAX_AGENT_OPERATIONS = "AUTONOMOUS_MAX_AGENT_OPERATIONS"
ENV_MAX_RUNS_PER_PROJECT = "AUTONOMOUS_MAX_RUNS_PER_PROJECT"


@dataclass(frozen=True)
class AutonomousComposerSettings:
    """Resolved numeric ceilings for autonomous runs."""

    max_agent_operations: int = DEFAULT_MAX_AGENT_OPERATIONS
    max_runs_per_project: int = DEFAULT_MAX_RUNS_PER_PROJECT


def _warn_invalid(key: str, error_type: str) -> None:
    logger.warning(
        "Autonomous composer env invalid",
        extra={"env_key": key, "error_type": error_type},
    )


def _parse_int(source: Mapping[str, str], key: str) -> tuple[int | None, bool]:
    raw = source.get(key)
    if raw is None or str(raw).strip() == "":
        return None, False
    try:
        return int(str(raw).strip()), True
    except ValueError:
        _warn_invalid(key, "ValueError")
        return None, True


def load_autonomous_composer_settings(
    env: Mapping[str, str] | None = None,
) -> AutonomousComposerSettings:
    source = env if env is not None else os.environ

    operations, operations_set = _parse_int(source, ENV_MAX_AGENT_OPERATIONS)
    if not operations_set or operations is None:
        operations = DEFAULT_MAX_AGENT_OPERATIONS
    elif operations < 0:
        _warn_invalid(ENV_MAX_AGENT_OPERATIONS, "ValueError")
        operations = DEFAULT_MAX_AGENT_OPERATIONS

    runs, runs_set = _parse_int(source, ENV_MAX_RUNS_PER_PROJECT)
    if not runs_set or runs is None or runs < 1:
        if runs_set:
            _warn_invalid(ENV_MAX_RUNS_PER_PROJECT, "ValueError")
        runs = DEFAULT_MAX_RUNS_PER_PROJECT

    settings = AutonomousComposerSettings(
        max_agent_operations=operations,
        max_runs_per_project=runs,
    )
    logger.debug(
        "Autonomous composer settings resolved",
        extra={
            "max_agent_operations": settings.max_agent_operations,
            "max_runs_per_project": settings.max_runs_per_project,
        },
    )
    return settings


_LOADED = load_autonomous_composer_settings()
logger.info(
    "Autonomous composer settings loaded",
    extra={
        "max_agent_operations": _LOADED.max_agent_operations,
        "max_runs_per_project": _LOADED.max_runs_per_project,
    },
)
