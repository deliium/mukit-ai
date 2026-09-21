"""Bounded neural audio render settings (egress only; no heavy imports).

``NEURAL_AUDIO_*`` is independent from FluidSynth WAV export, Tone.js,
and ``AUDIO_*`` transcription ingress. Render files live under
``NEURAL_AUDIO_RENDER_ROOT`` beside project data — never ``DATASET_ROOT``.
Runtime verbosity remains controlled by ``LOG_LEVEL``.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping


logger = logging.getLogger(__name__)

# Mirror backend/app/db/connection.py default without importing Alembic.
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_PROJECT_DB_PATH = _BACKEND_ROOT / "data" / "projects.db"

NeuralAudioEngineId = Literal[
    "auto",
    "fake:neural-audio",
    "sidecar:musicgen",
    "local:midi-ddsp",
]
NEURAL_AUDIO_ENGINE_IDS: frozenset[str] = frozenset(
    {
        "auto",
        "fake:neural-audio",
        "sidecar:musicgen",
        "local:midi-ddsp",
    }
)

_DEFAULT_ENGINE: NeuralAudioEngineId = "auto"
_DEFAULT_SIDECAR_BASE_URL = "http://neural-audio:8090"
_DEFAULT_MAX_CONCURRENCY = 1
_DEFAULT_MAX_RENDERS_PER_PROJECT = 20
_DEFAULT_MAX_PROMPT_CHARS = 2000
_DEFAULT_JOB_TIMEOUT_SECONDS = 600
_DEFAULT_MAX_AUDIO_SECONDS = 120.0
_DEFAULT_MAX_TOTAL_BYTES = 500 * 1024 * 1024
_DEFAULT_MAX_GENRE_CHARS = 64
_DEFAULT_MAX_MOOD_CHARS = 64


@dataclass(frozen=True)
class NeuralAudioSettings:
    """Env-backed neural render limits (no secrets, no weight paths)."""

    render_root: Path
    fake_mode: bool
    engine: NeuralAudioEngineId
    sidecar_base_url: str
    max_concurrency: int
    max_renders_per_project: int
    max_prompt_chars: int
    job_timeout_seconds: int
    max_audio_seconds: float
    max_total_bytes: int
    max_genre_chars: int
    max_mood_chars: int


def _resolve_project_db_path(env: Mapping[str, str]) -> Path:
    raw = (env.get("PROJECT_DB_PATH") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return _DEFAULT_PROJECT_DB_PATH


def default_neural_audio_render_root(env: Mapping[str, str] | None = None) -> Path:
    """Resolve default render root next to the project DB (never DATASET_ROOT)."""
    source = env if env is not None else os.environ
    db_path = _resolve_project_db_path(source)
    return db_path.parent / "neural_audio_renders"


def load_neural_audio_settings(
    env: Mapping[str, str] | None = None,
) -> NeuralAudioSettings:
    source = env if env is not None else os.environ

    root_raw = (source.get("NEURAL_AUDIO_RENDER_ROOT") or "").strip()
    if root_raw:
        render_root = Path(root_raw).expanduser()
    else:
        render_root = default_neural_audio_render_root(source)

    settings = NeuralAudioSettings(
        render_root=render_root,
        fake_mode=_bool_env(source, "NEURAL_AUDIO_FAKE_MODE", default=False),
        engine=_engine_env(source, "NEURAL_AUDIO_ENGINE", _DEFAULT_ENGINE),
        sidecar_base_url=_str_env(
            source,
            "NEURAL_AUDIO_SIDECAR_BASE_URL",
            _DEFAULT_SIDECAR_BASE_URL,
        ),
        max_concurrency=_int_env(
            source,
            "NEURAL_AUDIO_MAX_CONCURRENCY",
            _DEFAULT_MAX_CONCURRENCY,
            minimum=1,
            maximum=4,
        ),
        max_renders_per_project=_int_env(
            source,
            "NEURAL_AUDIO_MAX_RENDERS_PER_PROJECT",
            _DEFAULT_MAX_RENDERS_PER_PROJECT,
            minimum=1,
            maximum=200,
        ),
        max_prompt_chars=_int_env(
            source,
            "NEURAL_AUDIO_MAX_PROMPT_CHARS",
            _DEFAULT_MAX_PROMPT_CHARS,
            minimum=64,
            maximum=8000,
        ),
        job_timeout_seconds=_int_env(
            source,
            "NEURAL_AUDIO_JOB_TIMEOUT_SECONDS",
            _DEFAULT_JOB_TIMEOUT_SECONDS,
            minimum=30,
            maximum=3600,
        ),
        max_audio_seconds=_float_env(
            source,
            "NEURAL_AUDIO_MAX_AUDIO_SECONDS",
            _DEFAULT_MAX_AUDIO_SECONDS,
            minimum=1.0,
            maximum=600.0,
        ),
        max_total_bytes=_int_env(
            source,
            "NEURAL_AUDIO_MAX_TOTAL_BYTES",
            _DEFAULT_MAX_TOTAL_BYTES,
            minimum=1024 * 1024,
            maximum=10 * 1024 * 1024 * 1024,
        ),
        max_genre_chars=_int_env(
            source,
            "NEURAL_AUDIO_MAX_GENRE_CHARS",
            _DEFAULT_MAX_GENRE_CHARS,
            minimum=8,
            maximum=256,
        ),
        max_mood_chars=_int_env(
            source,
            "NEURAL_AUDIO_MAX_MOOD_CHARS",
            _DEFAULT_MAX_MOOD_CHARS,
            minimum=8,
            maximum=256,
        ),
    )
    logger.info(
        "Neural audio settings ready",
        extra={
            "render_root_basename": settings.render_root.name,
            "fake_mode": settings.fake_mode,
            "engine": settings.engine,
            "max_concurrency": settings.max_concurrency,
            "max_renders_per_project": settings.max_renders_per_project,
            "max_prompt_chars": settings.max_prompt_chars,
            "job_timeout_seconds": settings.job_timeout_seconds,
            "max_audio_seconds": settings.max_audio_seconds,
            "max_total_bytes": settings.max_total_bytes,
            "has_sidecar_base_url": bool(settings.sidecar_base_url),
        },
    )
    logger.debug(
        "Neural audio settings detail",
        extra={
            "max_genre_chars": settings.max_genre_chars,
            "max_mood_chars": settings.max_mood_chars,
        },
    )
    return settings


def _engine_env(
    env: Mapping[str, str],
    name: str,
    default: NeuralAudioEngineId,
) -> NeuralAudioEngineId:
    raw_value = env.get(name)
    if raw_value is None or raw_value.strip() == "":
        return default
    value = raw_value.strip()
    if value not in NEURAL_AUDIO_ENGINE_IDS:
        logger.warning(
            "Invalid neural audio engine; using default",
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
        "Invalid boolean neural audio setting; using default",
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
            "Invalid integer neural audio setting; using default",
            extra={"setting_name": name, "fallback": default},
        )
        return default
    if value < minimum or value > maximum:
        clamped = min(max(value, minimum), maximum)
        logger.warning(
            "Neural audio setting out of range; clamped",
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
            "Invalid float neural audio setting; using default",
            extra={"setting_name": name, "fallback": default},
        )
        return default
    if value < minimum or value > maximum:
        clamped = min(max(value, minimum), maximum)
        logger.warning(
            "Neural audio setting out of range; clamped",
            extra={
                "setting_name": name,
                "fallback": clamped,
                "minimum": minimum,
                "maximum": maximum,
            },
        )
        return clamped
    return value
