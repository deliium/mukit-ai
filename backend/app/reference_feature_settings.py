"""Environment caps for reference feature analysis and soft conditioning."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Mapping

logger = logging.getLogger(__name__)

_DEFAULT_MAX_REFERENCES = 3
_DEFAULT_SUMMARY_MAX_CHARS = 280
_DEFAULT_SOFT_FRAGMENT_MAX_CHARS = 1_600
_DEFAULT_ANALYZE_LRU_SIZE = 0  # 0 = disabled
_DEFAULT_POLICY_DIGEST_PREFIX_LEN = 16
_DEFAULT_STRENGTH_LEGEND_MAX_CHARS = 240

_caps_logged = False


@dataclass(frozen=True)
class ReferenceFeatureSettings:
    max_references: int
    summary_max_chars: int
    soft_fragment_max_chars: int
    analyze_lru_size: int
    policy_digest_prefix_len: int
    strength_legend_max_chars: int


def load_reference_feature_settings(
    env: Mapping[str, str] | None = None,
) -> ReferenceFeatureSettings:
    """Load ``REFERENCE_FEATURES_*`` caps with safe clamped defaults."""
    global _caps_logged
    source = env if env is not None else os.environ
    settings = ReferenceFeatureSettings(
        max_references=_int_env(
            source,
            "REFERENCE_FEATURES_MAX_REFERENCES",
            _DEFAULT_MAX_REFERENCES,
            minimum=1,
            maximum=8,
        ),
        summary_max_chars=_int_env(
            source,
            "REFERENCE_FEATURES_SUMMARY_MAX_CHARS",
            _DEFAULT_SUMMARY_MAX_CHARS,
            minimum=64,
            maximum=2_000,
        ),
        soft_fragment_max_chars=_int_env(
            source,
            "REFERENCE_FEATURES_SOFT_FRAGMENT_MAX_CHARS",
            _DEFAULT_SOFT_FRAGMENT_MAX_CHARS,
            minimum=128,
            maximum=8_192,
        ),
        analyze_lru_size=_int_env(
            source,
            "REFERENCE_FEATURES_ANALYZE_LRU_SIZE",
            _DEFAULT_ANALYZE_LRU_SIZE,
            minimum=0,
            maximum=256,
        ),
        policy_digest_prefix_len=_int_env(
            source,
            "REFERENCE_FEATURES_POLICY_DIGEST_PREFIX_LEN",
            _DEFAULT_POLICY_DIGEST_PREFIX_LEN,
            minimum=8,
            maximum=64,
        ),
        strength_legend_max_chars=_int_env(
            source,
            "REFERENCE_FEATURES_STRENGTH_LEGEND_MAX_CHARS",
            _DEFAULT_STRENGTH_LEGEND_MAX_CHARS,
            minimum=64,
            maximum=1_000,
        ),
    )
    if not _caps_logged or env is not None:
        logger.debug(
            "Reference feature settings loaded",
            extra={
                "max_references": settings.max_references,
                "summary_max_chars": settings.summary_max_chars,
                "soft_fragment_max_chars": settings.soft_fragment_max_chars,
                "analyze_lru_size": settings.analyze_lru_size,
                "policy_digest_prefix_len": settings.policy_digest_prefix_len,
                "strength_legend_max_chars": settings.strength_legend_max_chars,
            },
        )
        if env is None:
            _caps_logged = True
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
            "Invalid reference feature int env; using default",
            extra={"key": key, "default": default},
        )
        return default
    return max(minimum, min(maximum, value))
