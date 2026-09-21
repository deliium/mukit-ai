"""Bounded ``TOKENIZER_*`` defaults (independent of ``PROJECT_DB_PATH`` / FastAPI).

Tokenizer artifacts and corpora are CLI/filesystem only. Runtime verbosity
stays controlled by ``LOG_LEVEL``.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Mapping

from app.import_settings import IMPORT_DEFAULT_TARGET_PPQ


logger = logging.getLogger(__name__)

# Bump when vocab families or quantization rules change identity.
TOKENIZER_VERSION = "tokenizer.v1"

_DEFAULT_GRID_SUBDIVISIONS = 4
_DEFAULT_VELOCITY_BINS = 32
_DEFAULT_MAX_DUR_STEPS = 64
_DEFAULT_MAX_TRACKS = 32
_DEFAULT_MAX_VOCAB_SIZE = 8192
_DEFAULT_REQUIRE_BOS_EOS = True
_DEFAULT_PROFILE = "core"
_DEFAULT_TICKS_PER_QUARTER = IMPORT_DEFAULT_TARGET_PPQ


@dataclass(frozen=True)
class TokenizerSettings:
    """Env-backed defaults; ``tokenizer.config.v1`` may override per encode/decode."""

    tokenizer_version: str
    ticks_per_quarter: int
    grid_subdivisions_per_quarter: int
    velocity_bins: int
    max_dur_steps: int
    max_tracks: int
    max_vocab_size: int
    require_bos_eos: bool
    default_profile: str

    @property
    def grid_ticks(self) -> int:
        if self.ticks_per_quarter % self.grid_subdivisions_per_quarter != 0:
            raise ValueError(
                "ticks_per_quarter must be divisible by grid_subdivisions_per_quarter"
            )
        return self.ticks_per_quarter // self.grid_subdivisions_per_quarter


def load_tokenizer_settings(env: Mapping[str, str] | None = None) -> TokenizerSettings:
    source = env if env is not None else os.environ
    settings = TokenizerSettings(
        tokenizer_version=TOKENIZER_VERSION,
        ticks_per_quarter=_int_env(
            source,
            "TOKENIZER_TICKS_PER_QUARTER",
            _DEFAULT_TICKS_PER_QUARTER,
            minimum=24,
            maximum=9600,
        ),
        grid_subdivisions_per_quarter=_int_env(
            source,
            "TOKENIZER_GRID_SUBDIVISIONS_PER_QUARTER",
            _DEFAULT_GRID_SUBDIVISIONS,
            minimum=1,
            maximum=64,
        ),
        velocity_bins=_int_env(
            source,
            "TOKENIZER_VELOCITY_BINS",
            _DEFAULT_VELOCITY_BINS,
            minimum=1,
            maximum=127,
        ),
        max_dur_steps=_int_env(
            source,
            "TOKENIZER_MAX_DUR_STEPS",
            _DEFAULT_MAX_DUR_STEPS,
            minimum=1,
            maximum=512,
        ),
        max_tracks=_int_env(
            source,
            "TOKENIZER_MAX_TRACKS",
            _DEFAULT_MAX_TRACKS,
            minimum=1,
            maximum=128,
        ),
        max_vocab_size=_int_env(
            source,
            "TOKENIZER_MAX_VOCAB_SIZE",
            _DEFAULT_MAX_VOCAB_SIZE,
            minimum=256,
            maximum=100_000,
        ),
        require_bos_eos=_bool_env(source, "TOKENIZER_REQUIRE_BOS_EOS", _DEFAULT_REQUIRE_BOS_EOS),
        default_profile=_str_env(source, "TOKENIZER_PROFILE", _DEFAULT_PROFILE),
    )

    try:
        grid_ticks = settings.grid_ticks
    except ValueError:
        logger.warning(
            "TOKENIZER ticks/grid mismatch; encode will reject until config fixed",
            extra={
                "ticks_per_quarter": settings.ticks_per_quarter,
                "grid_subdivisions_per_quarter": settings.grid_subdivisions_per_quarter,
            },
        )
        grid_ticks = -1

    logger.info(
        "Tokenizer settings loaded",
        extra={
            "tokenizer_version": settings.tokenizer_version,
            "ticks_per_quarter": settings.ticks_per_quarter,
            "grid_subdivisions_per_quarter": settings.grid_subdivisions_per_quarter,
            "velocity_bins": settings.velocity_bins,
            "max_dur_steps": settings.max_dur_steps,
            "max_tracks": settings.max_tracks,
            "default_profile": settings.default_profile,
            "require_bos_eos": settings.require_bos_eos,
        },
    )
    logger.debug(
        "Tokenizer settings resolved grid",
        extra={
            "grid_ticks": grid_ticks,
            "max_vocab_size": settings.max_vocab_size,
        },
    )
    return settings


def _bool_env(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    lowered = raw.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    logger.warning(
        "Invalid boolean tokenizer setting; using default",
        extra={"setting_name": name, "fallback": default},
    )
    return default


def _int_env(
    env: Mapping[str, str],
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    raw_value = env.get(name)
    if raw_value is None or raw_value.strip() == "":
        return default
    try:
        value = int(raw_value)
    except ValueError:
        logger.warning(
            "Invalid integer tokenizer setting; using default",
            extra={"setting_name": name, "fallback": default},
        )
        return default
    if value < minimum or value > maximum:
        clamped = min(max(value, minimum), maximum)
        logger.warning(
            "Tokenizer setting out of range; clamped",
            extra={
                "setting_name": name,
                "fallback": clamped,
                "minimum": minimum,
                "maximum": maximum,
            },
        )
        return clamped
    return value


def _str_env(env: Mapping[str, str], name: str, default: str) -> str:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip()
