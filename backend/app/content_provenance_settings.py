"""Bounded content-provenance settings. Capture is always on."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Mapping

logger = logging.getLogger(__name__)

_DEFAULT_MAX_RECORDS_PER_PROJECT = 4000
_DEFAULT_MANIFEST_MAX_RECORDS = 64
_DEFAULT_CHAIN_MAX_DEPTH = 32


@dataclass(frozen=True)
class ContentProvenanceSettings:
    max_records_per_project: int
    manifest_max_records: int
    chain_max_depth: int


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
            "Invalid content provenance int env; using default",
            extra={"key": key, "default": default},
        )
        return default
    return max(minimum, min(maximum, value))


def load_content_provenance_settings(
    env: Mapping[str, str] | None = None,
) -> ContentProvenanceSettings:
    source = env if env is not None else os.environ
    settings = ContentProvenanceSettings(
        max_records_per_project=_int_env(
            source,
            "CONTENT_PROVENANCE_MAX_RECORDS_PER_PROJECT",
            _DEFAULT_MAX_RECORDS_PER_PROJECT,
            minimum=16,
            maximum=50_000,
        ),
        manifest_max_records=_int_env(
            source,
            "CONTENT_PROVENANCE_MANIFEST_MAX_RECORDS",
            _DEFAULT_MANIFEST_MAX_RECORDS,
            minimum=4,
            maximum=256,
        ),
        chain_max_depth=_int_env(
            source,
            "CONTENT_PROVENANCE_CHAIN_MAX_DEPTH",
            _DEFAULT_CHAIN_MAX_DEPTH,
            minimum=2,
            maximum=128,
        ),
    )
    logger.debug(
        "Content provenance settings loaded",
        extra={
            "max_records_per_project": settings.max_records_per_project,
            "manifest_max_records": settings.manifest_max_records,
            "chain_max_depth": settings.chain_max_depth,
        },
    )
    return settings
