"""Bounded V4 audio-recovery limits and engine policy.

``AUDIO_RECOVERY_*`` is independent from V3 monophonic ``AUDIO_*`` transcription
and from ``NEURAL_AUDIO_*`` egress rendering. Asset files live under
``AUDIO_RECOVERY_ASSET_ROOT`` beside project data — never ``DATASET_ROOT``.
Runtime verbosity remains controlled by ``LOG_LEVEL``.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping

from app.storage_root_policy import reject_storage_root

logger = logging.getLogger(__name__)

# Mirror backend/app/db/connection.py default without importing Alembic.
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_PROJECT_DB_PATH = _BACKEND_ROOT / "data" / "projects.db"

AudioRecoveryEngineId = Literal[
    "auto",
    "fake:audio-recovery",
    "local:estimators",
    "sidecar:recovery",
]
AUDIO_RECOVERY_ENGINE_IDS: frozenset[str] = frozenset(
    {
        "auto",
        "fake:audio-recovery",
        "local:estimators",
        "sidecar:recovery",
    }
)

AudioRecoverySeparationEngineId = Literal[
    "off",
    "auto",
    "fake:stems",
    "sidecar:demucs",
]
AUDIO_RECOVERY_SEPARATION_ENGINE_IDS: frozenset[str] = frozenset(
    {
        "off",
        "auto",
        "fake:stems",
        "sidecar:demucs",
    }
)

# Closed product stem enum (v1) — see docs/audio-recovery.md.
AUDIO_RECOVERY_STEM_ROLES: frozenset[str] = frozenset(
    {"vocals", "melody", "bass", "drums", "harmonic", "other"}
)

_DEFAULT_MAX_UPLOAD_BYTES = 25 * 1024 * 1024
_DEFAULT_MAX_DURATION_SECONDS = 120.0
_DEFAULT_MAX_SAMPLE_RATE = 48_000
_DEFAULT_CONFIDENCE_INCLUDE_THRESHOLD = 0.5
_DEFAULT_ENGINE: AudioRecoveryEngineId = "auto"
_DEFAULT_SEPARATION_ENGINE: AudioRecoverySeparationEngineId = "auto"
_DEFAULT_SIDECAR_BASE_URL = "http://audio-recovery:8091"
_DEFAULT_MAX_ASSETS_PER_PROJECT = 10
_DEFAULT_MAX_JOBS_PER_PROJECT = 20
_DEFAULT_MAX_TOTAL_BYTES = 500 * 1024 * 1024
_DEFAULT_JOB_TIMEOUT_SECONDS = 600
_DEFAULT_SYNC_MAX_SECONDS = 30.0
_DEFAULT_TARGET_PPQ = 480
_DEFAULT_DEFAULT_TEMPO_BPM = 120


@dataclass(frozen=True)
class AudioRecoverySettings:
    """Env-backed recovery limits (no secrets, no weight paths)."""

    asset_root: Path
    max_upload_bytes: int
    max_duration_seconds: float
    max_sample_rate: int
    confidence_include_threshold: float
    engine: AudioRecoveryEngineId
    separation_engine: AudioRecoverySeparationEngineId
    fake_mode: bool
    sidecar_base_url: str
    max_assets_per_project: int
    max_jobs_per_project: int
    max_total_bytes: int
    job_timeout_seconds: int
    sync_max_seconds: float
    default_target_ppq: int
    default_tempo_bpm: int


def _resolve_project_db_path(env: Mapping[str, str]) -> Path:
    raw = (env.get("PROJECT_DB_PATH") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return _DEFAULT_PROJECT_DB_PATH


def default_audio_recovery_asset_root(env: Mapping[str, str] | None = None) -> Path:
    """Resolve default asset root next to the project DB (never DATASET_ROOT)."""
    source = env if env is not None else os.environ
    db_path = _resolve_project_db_path(source)
    return db_path.parent / "audio_recovery_assets"


def load_audio_recovery_settings(
    env: Mapping[str, str] | None = None,
) -> AudioRecoverySettings:
    source = env if env is not None else os.environ

    root_raw = (source.get("AUDIO_RECOVERY_ASSET_ROOT") or "").strip()
    if root_raw:
        asset_root = Path(root_raw).expanduser()
    else:
        asset_root = default_audio_recovery_asset_root(source)
    _accept_storage_root("audio_recovery", asset_root, source)

    settings = AudioRecoverySettings(
        asset_root=asset_root,
        max_upload_bytes=_int_env(
            source,
            "AUDIO_RECOVERY_MAX_UPLOAD_BYTES",
            _DEFAULT_MAX_UPLOAD_BYTES,
            minimum=1024,
            maximum=100 * 1024 * 1024,
        ),
        max_duration_seconds=_float_env(
            source,
            "AUDIO_RECOVERY_MAX_DURATION_SECONDS",
            _DEFAULT_MAX_DURATION_SECONDS,
            minimum=0.5,
            maximum=600.0,
        ),
        max_sample_rate=_int_env(
            source,
            "AUDIO_RECOVERY_MAX_SAMPLE_RATE",
            _DEFAULT_MAX_SAMPLE_RATE,
            minimum=8_000,
            maximum=96_000,
        ),
        confidence_include_threshold=_float_env(
            source,
            "AUDIO_RECOVERY_CONFIDENCE_INCLUDE_THRESHOLD",
            _DEFAULT_CONFIDENCE_INCLUDE_THRESHOLD,
            minimum=0.0,
            maximum=1.0,
        ),
        engine=_engine_env(source, "AUDIO_RECOVERY_ENGINE", _DEFAULT_ENGINE),
        separation_engine=_separation_engine_env(
            source,
            "AUDIO_RECOVERY_SEPARATION_ENGINE",
            _DEFAULT_SEPARATION_ENGINE,
        ),
        fake_mode=_bool_env(source, "AUDIO_RECOVERY_FAKE_MODE", default=False),
        sidecar_base_url=_str_env(
            source,
            "AUDIO_RECOVERY_SIDECAR_BASE_URL",
            _DEFAULT_SIDECAR_BASE_URL,
        ),
        max_assets_per_project=_int_env(
            source,
            "AUDIO_RECOVERY_MAX_ASSETS_PER_PROJECT",
            _DEFAULT_MAX_ASSETS_PER_PROJECT,
            minimum=1,
            maximum=200,
        ),
        max_jobs_per_project=_int_env(
            source,
            "AUDIO_RECOVERY_MAX_JOBS_PER_PROJECT",
            _DEFAULT_MAX_JOBS_PER_PROJECT,
            minimum=1,
            maximum=500,
        ),
        max_total_bytes=_int_env(
            source,
            "AUDIO_RECOVERY_MAX_TOTAL_BYTES",
            _DEFAULT_MAX_TOTAL_BYTES,
            minimum=1024 * 1024,
            maximum=10 * 1024 * 1024 * 1024,
        ),
        job_timeout_seconds=_int_env(
            source,
            "AUDIO_RECOVERY_JOB_TIMEOUT_SECONDS",
            _DEFAULT_JOB_TIMEOUT_SECONDS,
            minimum=30,
            maximum=3600,
        ),
        sync_max_seconds=_float_env(
            source,
            "AUDIO_RECOVERY_SYNC_MAX_SECONDS",
            _DEFAULT_SYNC_MAX_SECONDS,
            minimum=1.0,
            maximum=300.0,
        ),
        default_target_ppq=_int_env(
            source,
            "AUDIO_RECOVERY_DEFAULT_TARGET_PPQ",
            _DEFAULT_TARGET_PPQ,
            minimum=24,
            maximum=9600,
        ),
        default_tempo_bpm=_int_env(
            source,
            "AUDIO_RECOVERY_DEFAULT_TEMPO_BPM",
            _DEFAULT_DEFAULT_TEMPO_BPM,
            minimum=20,
            maximum=400,
        ),
    )
    logger.info(
        "Audio recovery settings ready",
        extra={
            "asset_root_basename": settings.asset_root.name,
            "max_upload_bytes": settings.max_upload_bytes,
            "max_duration_seconds": settings.max_duration_seconds,
            "max_sample_rate": settings.max_sample_rate,
            "confidence_include_threshold": settings.confidence_include_threshold,
            "engine": settings.engine,
            "separation_engine": settings.separation_engine,
            "fake_mode": settings.fake_mode,
            "max_assets_per_project": settings.max_assets_per_project,
            "max_jobs_per_project": settings.max_jobs_per_project,
            "max_total_bytes": settings.max_total_bytes,
            "job_timeout_seconds": settings.job_timeout_seconds,
            "sync_max_seconds": settings.sync_max_seconds,
            "has_sidecar_base_url": bool(settings.sidecar_base_url),
        },
    )
    logger.debug(
        "Audio recovery settings detail",
        extra={
            "default_target_ppq": settings.default_target_ppq,
            "default_tempo_bpm": settings.default_tempo_bpm,
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


def _engine_env(
    env: Mapping[str, str],
    name: str,
    default: AudioRecoveryEngineId,
) -> AudioRecoveryEngineId:
    raw_value = env.get(name)
    if raw_value is None or raw_value.strip() == "":
        return default
    value = raw_value.strip()
    if value not in AUDIO_RECOVERY_ENGINE_IDS:
        logger.warning(
            "Invalid audio recovery engine; using default",
            extra={"setting_name": name, "fallback": default},
        )
        return default
    return value  # type: ignore[return-value]


def _separation_engine_env(
    env: Mapping[str, str],
    name: str,
    default: AudioRecoverySeparationEngineId,
) -> AudioRecoverySeparationEngineId:
    raw_value = env.get(name)
    if raw_value is None or raw_value.strip() == "":
        return default
    value = raw_value.strip()
    if value not in AUDIO_RECOVERY_SEPARATION_ENGINE_IDS:
        logger.warning(
            "Invalid audio recovery separation engine; using default",
            extra={"setting_name": name, "fallback": default},
        )
        return default
    return value  # type: ignore[return-value]


def _str_env(env: Mapping[str, str], name: str, default: str) -> str:
    raw_value = env.get(name)
    if raw_value is None or raw_value.strip() == "":
        return default
    return raw_value.strip()


def _bool_env(env: Mapping[str, str], name: str, *, default: bool) -> bool:
    raw_value = env.get(name)
    if raw_value is None or raw_value.strip() == "":
        return default
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    logger.warning(
        "Invalid boolean audio recovery setting; using default",
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
            "Invalid integer audio recovery setting; using default",
            extra={"setting_name": name, "fallback": default},
        )
        return default
    if value < minimum or value > maximum:
        clamped = min(max(value, minimum), maximum)
        logger.warning(
            "Audio recovery setting out of range; clamped",
            extra={
                "setting_name": name,
                "fallback": clamped,
                "minimum": minimum,
                "maximum": maximum,
            },
        )
        return clamped
    return value


def _float_env(
    env: Mapping[str, str],
    name: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    raw_value = env.get(name)
    if raw_value is None or raw_value.strip() == "":
        return default
    try:
        value = float(raw_value)
    except ValueError:
        logger.warning(
            "Invalid float audio recovery setting; using default",
            extra={"setting_name": name, "fallback": default},
        )
        return default
    if value < minimum or value > maximum:
        clamped = min(max(value, minimum), maximum)
        logger.warning(
            "Audio recovery setting out of range; clamped",
            extra={
                "setting_name": name,
                "fallback": clamped,
                "minimum": minimum,
                "maximum": maximum,
            },
        )
        return clamped
    return value
