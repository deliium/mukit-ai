"""Bounded retention settings for the agent artifact workspace."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Mapping

logger = logging.getLogger(__name__)

_DEFAULT_TEMP_TTL_HOURS = 24
_DEFAULT_TEMP_MAX_PER_PROJECT = 200
_DEFAULT_PAYLOAD_INSPECT_MAX_BYTES = 64 * 1024


@dataclass(frozen=True)
class AgentArtifactSettings:
    temp_ttl_hours: int
    temp_max_per_project: int
    payload_inspect_max_bytes: int


def load_agent_artifact_settings(
    env: Mapping[str, str] | None = None,
) -> AgentArtifactSettings:
    source = env if env is not None else os.environ
    settings = AgentArtifactSettings(
        temp_ttl_hours=_int_env(
            source,
            "AGENT_ARTIFACT_TEMP_TTL_HOURS",
            _DEFAULT_TEMP_TTL_HOURS,
            minimum=1,
            maximum=24 * 30,
        ),
        temp_max_per_project=_int_env(
            source,
            "AGENT_ARTIFACT_TEMP_MAX_PER_PROJECT",
            _DEFAULT_TEMP_MAX_PER_PROJECT,
            minimum=1,
            maximum=10_000,
        ),
        payload_inspect_max_bytes=_int_env(
            source,
            "AGENT_ARTIFACT_PAYLOAD_INSPECT_MAX_BYTES",
            _DEFAULT_PAYLOAD_INSPECT_MAX_BYTES,
            minimum=1024,
            maximum=1024 * 1024,
        ),
    )
    logger.info(
        "Agent artifact settings loaded",
        extra={
            "temp_ttl_hours": settings.temp_ttl_hours,
            "temp_max_per_project": settings.temp_max_per_project,
            "payload_inspect_max_bytes": settings.payload_inspect_max_bytes,
        },
    )
    return settings


def _int_env(
    source: Mapping[str, str],
    key: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    raw = source.get(key)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = int(str(raw).strip())
    except ValueError:
        logger.warning(
            "Invalid agent artifact int env; using default",
            extra={"key": key, "default": default},
        )
        return default
    return max(minimum, min(maximum, value))
