"""Bounded ``MUSIC_TRANSFORMER_*`` defaults (independent of ``PROJECT_DB_PATH``).

Checkpoints and corpora are CLI/filesystem only. Runtime verbosity stays
controlled by ``LOG_LEVEL``. Torch is never imported here.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


logger = logging.getLogger(__name__)

# Bump when architecture card schema or train/inference contract changes.
MUSIC_TRANSFORMER_VERSION = "music_transformer.v1"

_DEFAULT_DEVICE = "cpu"
_DEFAULT_SEED = 42
_DEFAULT_CHECKPOINT_DIR = "checkpoints/music_transformer"
_DEFAULT_EXPERIMENT_ROOT = "experiments/music_transformer"
_DEFAULT_API_ENABLED = False
_DEFAULT_DEVICE_FALLBACK = ""  # empty = no silent fallback


@dataclass(frozen=True)
class MusicTransformerSettings:
    """Env-backed defaults; per-run configs may override."""

    package_version: str
    device: str
    device_fallback: str
    seed: int
    checkpoint_dir: str
    experiment_root: str
    api_enabled: bool
    default_checkpoint: str | None


def load_music_transformer_settings(
    env: Mapping[str, str] | None = None,
) -> MusicTransformerSettings:
    source = env if env is not None else os.environ
    device = _str_env(source, "MUSIC_TRANSFORMER_DEVICE", _DEFAULT_DEVICE)
    fallback = _str_env(source, "MUSIC_TRANSFORMER_DEVICE_FALLBACK", _DEFAULT_DEVICE_FALLBACK)
    checkpoint_dir = _str_env(
        source,
        "MUSIC_TRANSFORMER_CHECKPOINT_DIR",
        _DEFAULT_CHECKPOINT_DIR,
    )
    experiment_root = _str_env(
        source,
        "MUSIC_TRANSFORMER_EXPERIMENT_ROOT",
        _DEFAULT_EXPERIMENT_ROOT,
    )
    default_ckpt_raw = source.get("MUSIC_TRANSFORMER_CHECKPOINT")
    default_checkpoint = (
        default_ckpt_raw.strip()
        if default_ckpt_raw is not None and default_ckpt_raw.strip()
        else None
    )
    settings = MusicTransformerSettings(
        package_version=MUSIC_TRANSFORMER_VERSION,
        device=device,
        device_fallback=fallback,
        seed=_int_env(source, "MUSIC_TRANSFORMER_SEED", _DEFAULT_SEED, minimum=0, maximum=2**31 - 1),
        checkpoint_dir=checkpoint_dir,
        experiment_root=experiment_root,
        api_enabled=_bool_env(source, "MUSIC_TRANSFORMER_API_ENABLED", _DEFAULT_API_ENABLED),
        default_checkpoint=default_checkpoint,
    )
    logger.info(
        "Music Transformer settings loaded",
        extra={
            "package_version": settings.package_version,
            "device": settings.device,
            "seed": settings.seed,
            "api_enabled": settings.api_enabled,
            "checkpoint_dir_basename": Path(settings.checkpoint_dir).name,
            "experiment_root_basename": Path(settings.experiment_root).name,
            "has_default_checkpoint": settings.default_checkpoint is not None,
        },
    )
    if fallback:
        logger.debug(
            "Music Transformer device fallback configured",
            extra={"device_fallback": fallback},
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
        "Invalid boolean Music Transformer setting; using default",
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
            "Invalid integer Music Transformer setting; using default",
            extra={"setting_name": name, "fallback": default},
        )
        return default
    if value < minimum or value > maximum:
        clamped = min(max(value, minimum), maximum)
        logger.warning(
            "Music Transformer setting out of range; clamped",
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
