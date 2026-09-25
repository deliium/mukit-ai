"""Bounded mix-plan settings. Output root is never the neural stem root."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

logger = logging.getLogger(__name__)

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_PROJECT_DB_PATH = _BACKEND_ROOT / "data" / "projects.db"

_DEFAULT_MAX_AUDIO_SECONDS = 120.0
_DEFAULT_PREVIEW_MAX_SECONDS = 30.0
_DEFAULT_MAX_TOTAL_INPUT_BYTES = 200 * 1024 * 1024
_DEFAULT_MAX_REVISIONS = 40
_DEFAULT_JOB_TIMEOUT_SECONDS = 90


@dataclass(frozen=True)
class MixPlanSettings:
    plan_root: Path
    fake_mode: bool
    llm_fake_mode: bool
    max_audio_seconds: float
    preview_max_seconds: float
    max_total_input_bytes: int
    max_revisions_per_project: int
    job_timeout_seconds: int


def _resolve_project_db_path(env: Mapping[str, str]) -> Path:
    raw = (env.get("PROJECT_DB_PATH") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return _DEFAULT_PROJECT_DB_PATH


def default_mix_plan_root(env: Mapping[str, str] | None = None) -> Path:
    source = env if env is not None else os.environ
    return _resolve_project_db_path(source).parent / "mix_plans"


def load_mix_plan_settings(env: Mapping[str, str] | None = None) -> MixPlanSettings:
    source = env if env is not None else os.environ
    root_raw = (source.get("MIX_PLAN_ROOT") or "").strip()
    plan_root = Path(root_raw).expanduser() if root_raw else default_mix_plan_root(source)
    settings = MixPlanSettings(
        plan_root=plan_root,
        fake_mode=_bool_env(source, "MIX_PLAN_FAKE_MODE", default=False),
        llm_fake_mode=_bool_env(source, "LLM_FAKE_MODE", default=False),
        max_audio_seconds=_float_env(
            source, "MIX_PLAN_MAX_AUDIO_SECONDS", _DEFAULT_MAX_AUDIO_SECONDS, minimum=1.0, maximum=600.0
        ),
        preview_max_seconds=_float_env(
            source, "MIX_PLAN_PREVIEW_MAX_SECONDS", _DEFAULT_PREVIEW_MAX_SECONDS, minimum=1.0, maximum=120.0
        ),
        max_total_input_bytes=_int_env(
            source,
            "MIX_PLAN_MAX_TOTAL_INPUT_BYTES",
            _DEFAULT_MAX_TOTAL_INPUT_BYTES,
            minimum=1024,
            maximum=2 * 1024 * 1024 * 1024,
        ),
        max_revisions_per_project=_int_env(
            source, "MIX_PLAN_MAX_REVISIONS_PER_PROJECT", _DEFAULT_MAX_REVISIONS, minimum=1, maximum=500
        ),
        job_timeout_seconds=_int_env(
            source, "MIX_PLAN_JOB_TIMEOUT_SECONDS", _DEFAULT_JOB_TIMEOUT_SECONDS, minimum=5, maximum=600
        ),
    )
    logger.debug(
        "Mix plan settings loaded",
        extra={
            "plan_root_basename": settings.plan_root.name,
            "fake_mode": settings.fake_mode,
            "max_audio_seconds": settings.max_audio_seconds,
            "preview_max_seconds": settings.preview_max_seconds,
        },
    )
    return settings


def _bool_env(env: Mapping[str, str], key: str, *, default: bool) -> bool:
    raw = (env.get(key) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _int_env(
    env: Mapping[str, str], key: str, default: int, *, minimum: int, maximum: int
) -> int:
    raw = (env.get(key) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid mix plan int env; using default", extra={"key": key})
        return default
    return max(minimum, min(maximum, value))


def _float_env(
    env: Mapping[str, str], key: str, default: float, *, minimum: float, maximum: float
) -> float:
    raw = (env.get(key) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("Invalid mix plan float env; using default", extra={"key": key})
        return default
    return max(minimum, min(maximum, value))
