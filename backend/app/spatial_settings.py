"""Env overrides for spatial preview caps (defaults match Task 1 constants)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Mapping

from app.spatial_constants import MAX_MOTION_KEYFRAMES, MAX_SOURCES

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SpatialPreviewSettings:
    max_sources: int = MAX_SOURCES
    max_motion_keyframes: int = MAX_MOTION_KEYFRAMES
    max_body_bytes: int = 131072
    max_name_length: int = 120


def _positive_int(raw: str | None, default: int) -> int:
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except ValueError:
        return default
    return value if value > 0 else default


def load_spatial_settings(
    env: Mapping[str, str] | None = None,
) -> SpatialPreviewSettings:
    source = env if env is not None else os.environ
    max_sources = _positive_int(
        source.get("SPATIAL_PREVIEW_MAX_SOURCES"), MAX_SOURCES
    )
    max_motion = _positive_int(
        source.get("SPATIAL_PREVIEW_MAX_MOTION_KEYFRAMES"), MAX_MOTION_KEYFRAMES
    )
    logger.debug(
        "Loaded spatial preview settings",
        extra={"max_sources": max_sources, "max_motion_keyframes": max_motion},
    )
    return SpatialPreviewSettings(
        max_sources=max_sources,
        max_motion_keyframes=max_motion,
    )
