"""Bounded mix analysis settings (DSP + durable reports; no heavy imports).

``MIX_ANALYSIS_*`` is independent from neural render PCM roots for *outputs*
(reports live under ``MIX_ANALYSIS_ROOT``). Stem/mix WAVs are read-only inputs
from ``NEURAL_AUDIO_RENDER_ROOT``. Never writes ``DATASET_ROOT``.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from app.storage_root_policy import reject_storage_root

logger = logging.getLogger(__name__)

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_PROJECT_DB_PATH = _BACKEND_ROOT / "data" / "projects.db"

_DEFAULT_MAX_AUDIO_SECONDS = 120.0
_DEFAULT_MAX_TOTAL_INPUT_BYTES = 200 * 1024 * 1024
_DEFAULT_MAX_REPORTS_PER_PROJECT = 40
_DEFAULT_JOB_TIMEOUT_SECONDS = 90
_DEFAULT_SERIES_MAX_POINTS = 256
_DEFAULT_OBSERVATION_MAX = 64
_DEFAULT_DECODE_MAX_SAMPLES = 12_000_000  # ~4.5 min stereo @ 22.05k

# Observation thresholds (objective proxies — not calibrated monitoring claims)
_DEFAULT_PEAK_HOT_DBFS = -1.0
_DEFAULT_LOW_HEADROOM_DB = 1.5
_DEFAULT_CLIP_RATIO = 0.0005
_DEFAULT_STEREO_IMBALANCE_DB = 6.0
_DEFAULT_LF_BUILDUP_SCORE = 0.65
_DEFAULT_MASKING_PROXY = 0.72
_DEFAULT_SECTION_LOUDNESS_FLAT_DB = 1.0


@dataclass(frozen=True)
class MixAnalysisSettings:
    """Env-backed mix analysis limits (no secrets, no weight paths)."""

    report_root: Path
    fake_mode: bool
    max_audio_seconds: float
    max_total_input_bytes: int
    max_reports_per_project: int
    job_timeout_seconds: int
    series_max_points: int
    observation_max: int
    decode_max_samples: int
    peak_hot_dbfs: float
    low_headroom_db: float
    clip_ratio: float
    stereo_imbalance_db: float
    lf_buildup_score: float
    masking_proxy: float
    section_loudness_flat_db: float


def _resolve_project_db_path(env: Mapping[str, str]) -> Path:
    raw = (env.get("PROJECT_DB_PATH") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return _DEFAULT_PROJECT_DB_PATH


def default_mix_analysis_root(env: Mapping[str, str] | None = None) -> Path:
    """Resolve default report root next to the project DB (never DATASET_ROOT)."""
    source = env if env is not None else os.environ
    db_path = _resolve_project_db_path(source)
    return db_path.parent / "mix_analysis_reports"


def load_mix_analysis_settings(
    env: Mapping[str, str] | None = None,
) -> MixAnalysisSettings:
    source = env if env is not None else os.environ

    root_raw = (source.get("MIX_ANALYSIS_ROOT") or "").strip()
    if root_raw:
        report_root = Path(root_raw).expanduser()
    else:
        report_root = default_mix_analysis_root(source)
    _accept_storage_root("mix_analysis", report_root, source)

    settings = MixAnalysisSettings(
        report_root=report_root,
        fake_mode=_bool_env(source, "MIX_ANALYSIS_FAKE_MODE", default=False),
        max_audio_seconds=_float_env(
            source,
            "MIX_ANALYSIS_MAX_AUDIO_SECONDS",
            _DEFAULT_MAX_AUDIO_SECONDS,
            minimum=1.0,
            maximum=600.0,
        ),
        max_total_input_bytes=_int_env(
            source,
            "MIX_ANALYSIS_MAX_TOTAL_INPUT_BYTES",
            _DEFAULT_MAX_TOTAL_INPUT_BYTES,
            minimum=1024,
            maximum=2 * 1024 * 1024 * 1024,
        ),
        max_reports_per_project=_int_env(
            source,
            "MIX_ANALYSIS_MAX_REPORTS_PER_PROJECT",
            _DEFAULT_MAX_REPORTS_PER_PROJECT,
            minimum=1,
            maximum=500,
        ),
        job_timeout_seconds=_int_env(
            source,
            "MIX_ANALYSIS_JOB_TIMEOUT_SECONDS",
            _DEFAULT_JOB_TIMEOUT_SECONDS,
            minimum=5,
            maximum=600,
        ),
        series_max_points=_int_env(
            source,
            "MIX_ANALYSIS_SERIES_MAX_POINTS",
            _DEFAULT_SERIES_MAX_POINTS,
            minimum=16,
            maximum=2048,
        ),
        observation_max=_int_env(
            source,
            "MIX_ANALYSIS_OBSERVATION_MAX",
            _DEFAULT_OBSERVATION_MAX,
            minimum=1,
            maximum=256,
        ),
        decode_max_samples=_int_env(
            source,
            "MIX_ANALYSIS_DECODE_MAX_SAMPLES",
            _DEFAULT_DECODE_MAX_SAMPLES,
            minimum=8_000,
            maximum=50_000_000,
        ),
        peak_hot_dbfs=_float_env(
            source,
            "MIX_ANALYSIS_PEAK_HOT_DBFS",
            _DEFAULT_PEAK_HOT_DBFS,
            minimum=-24.0,
            maximum=0.0,
        ),
        low_headroom_db=_float_env(
            source,
            "MIX_ANALYSIS_LOW_HEADROOM_DB",
            _DEFAULT_LOW_HEADROOM_DB,
            minimum=0.1,
            maximum=24.0,
        ),
        clip_ratio=_float_env(
            source,
            "MIX_ANALYSIS_CLIP_RATIO",
            _DEFAULT_CLIP_RATIO,
            minimum=0.0,
            maximum=1.0,
        ),
        stereo_imbalance_db=_float_env(
            source,
            "MIX_ANALYSIS_STEREO_IMBALANCE_DB",
            _DEFAULT_STEREO_IMBALANCE_DB,
            minimum=0.5,
            maximum=48.0,
        ),
        lf_buildup_score=_float_env(
            source,
            "MIX_ANALYSIS_LF_BUILDUP_SCORE",
            _DEFAULT_LF_BUILDUP_SCORE,
            minimum=0.1,
            maximum=1.0,
        ),
        masking_proxy=_float_env(
            source,
            "MIX_ANALYSIS_MASKING_PROXY",
            _DEFAULT_MASKING_PROXY,
            minimum=0.1,
            maximum=1.0,
        ),
        section_loudness_flat_db=_float_env(
            source,
            "MIX_ANALYSIS_SECTION_LOUDNESS_FLAT_DB",
            _DEFAULT_SECTION_LOUDNESS_FLAT_DB,
            minimum=0.1,
            maximum=24.0,
        ),
    )
    logger.debug(
        "Mix analysis settings loaded",
        extra={
            "report_root_basename": settings.report_root.name,
            "fake_mode": settings.fake_mode,
            "max_audio_seconds": settings.max_audio_seconds,
            "max_reports_per_project": settings.max_reports_per_project,
        },
    )
    return settings


def _accept_storage_root(settings_name: str, root: Path, source: Mapping[str, str]) -> None:
    dataset_raw = (source.get("DATASET_ROOT") or "").strip()
    dataset_root = Path(dataset_raw).expanduser() if dataset_raw else None
    reject_storage_root(
        root,
        dataset_root=dataset_root,
        project_db=_resolve_project_db_path(source),
    )
    logger.info(
        "storage_root_accepted",
        extra={"settings": settings_name, "basename": root.name},
    )


def _bool_env(env: Mapping[str, str], key: str, *, default: bool) -> bool:
    raw = (env.get(key) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _int_env(
    env: Mapping[str, str],
    key: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    raw = (env.get(key) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "Invalid mix analysis int env; using default",
            extra={"key": key},
        )
        return default
    return max(minimum, min(maximum, value))


def _float_env(
    env: Mapping[str, str],
    key: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    raw = (env.get(key) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "Invalid mix analysis float env; using default",
            extra={"key": key},
        )
        return default
    return max(minimum, min(maximum, value))
