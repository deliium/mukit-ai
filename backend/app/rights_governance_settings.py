"""Bounded rights-governance settings. Caps only — no separate FS root in ship-1."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Mapping

logger = logging.getLogger(__name__)

_DEFAULT_MAX_ENTRIES_PER_PROJECT = 256
_DEFAULT_MAX_ATTRIBUTION_CHARS = 500


@dataclass(frozen=True)
class RightsGovernanceSettings:
    max_entries_per_project: int
    max_attribution_chars: int


def _int_env(
    source: Mapping[str, str],
    key: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    raw = (source.get(key) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "Invalid rights governance int env; using default",
            extra={"key": key, "default": default},
        )
        return default
    return max(minimum, min(maximum, value))


def load_rights_governance_settings(
    env: Mapping[str, str] | None = None,
) -> RightsGovernanceSettings:
    source = env if env is not None else os.environ
    settings = RightsGovernanceSettings(
        max_entries_per_project=_int_env(
            source,
            "RIGHTS_GOVERNANCE_MAX_ENTRIES_PER_PROJECT",
            _DEFAULT_MAX_ENTRIES_PER_PROJECT,
            minimum=8,
            maximum=10_000,
        ),
        max_attribution_chars=_int_env(
            source,
            "RIGHTS_GOVERNANCE_MAX_ATTRIBUTION_CHARS",
            _DEFAULT_MAX_ATTRIBUTION_CHARS,
            minimum=64,
            maximum=4000,
        ),
    )
    logger.debug(
        "Rights governance settings loaded",
        extra={
            "max_entries_per_project": settings.max_entries_per_project,
            "max_attribution_chars": settings.max_attribution_chars,
        },
    )
    return settings
