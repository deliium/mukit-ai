"""Bounded monophonic audio-transcription limits and format policy.

AUDIO_* settings are independent from IMPORT_* (symbolic MIDI/MusicXML) and
LLM generation budgets. Client uploads should prefer WAV PCM; other formats
are accepted only when content signatures match and a decoder is available.
Runtime verbosity remains controlled by ``LOG_LEVEL``.

Content sniff helpers live in ``audio_format_policy`` and are re-exported here
for backward-compatible imports.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Literal, Mapping

from app.audio_format_policy import (  # noqa: F401 — re-export public API
    AUDIO_EXTENSIONS,
    AUDIO_FLAC_EXTENSIONS,
    AUDIO_MP3_EXTENSIONS,
    AUDIO_OGG_EXTENSIONS,
    AUDIO_WAV_EXTENSIONS,
    FLAC_SIGNATURE,
    MP3_FRAME_SYNC_PREFIXES,
    MP3_ID3_SIGNATURE,
    OGG_SIGNATURE,
    WAV_RIFF_SIGNATURE,
    WAV_WAVE_MARKER,
    sniff_audio_format,
)


logger = logging.getLogger(__name__)

AudioEngineId = Literal["auto", "fake:audio-mono", "librosa_pyin", "basic_pitch"]
AUDIO_ENGINE_IDS: frozenset[str] = frozenset(
    {"auto", "fake:audio-mono", "librosa_pyin", "basic_pitch"}
)

# Documented env defaults (also wired through .env.example / docker-compose).
_DEFAULT_MAX_UPLOAD_BYTES = 10 * 1024 * 1024
_DEFAULT_MAX_DURATION_SECONDS = 60.0
_DEFAULT_MAX_SAMPLE_RATE = 48_000
_DEFAULT_CONFIDENCE_INCLUDE_THRESHOLD = 0.5
_DEFAULT_ENGINE: AudioEngineId = "auto"
_DEFAULT_TARGET_PPQ = 480
_DEFAULT_DEFAULT_TEMPO_BPM = 120


@dataclass(frozen=True)
class AudioTranscriptionSettings:
    max_upload_bytes: int
    max_duration_seconds: float
    max_sample_rate: int
    confidence_include_threshold: float
    engine: AudioEngineId
    fake_mode: bool
    default_target_ppq: int
    default_tempo_bpm: int


def load_audio_transcription_settings(
    env: Mapping[str, str] | None = None,
) -> AudioTranscriptionSettings:
    source = env if env is not None else os.environ
    settings = AudioTranscriptionSettings(
        max_upload_bytes=_int_env(
            source,
            "AUDIO_MAX_UPLOAD_BYTES",
            _DEFAULT_MAX_UPLOAD_BYTES,
            minimum=1024,
            maximum=50 * 1024 * 1024,
        ),
        max_duration_seconds=_float_env(
            source,
            "AUDIO_MAX_DURATION_SECONDS",
            _DEFAULT_MAX_DURATION_SECONDS,
            minimum=0.5,
            maximum=300.0,
        ),
        max_sample_rate=_int_env(
            source,
            "AUDIO_MAX_SAMPLE_RATE",
            _DEFAULT_MAX_SAMPLE_RATE,
            minimum=8_000,
            maximum=96_000,
        ),
        confidence_include_threshold=_float_env(
            source,
            "AUDIO_CONFIDENCE_INCLUDE_THRESHOLD",
            _DEFAULT_CONFIDENCE_INCLUDE_THRESHOLD,
            minimum=0.0,
            maximum=1.0,
        ),
        engine=_engine_env(source, "AUDIO_TRANSCRIPTION_ENGINE", _DEFAULT_ENGINE),
        fake_mode=_bool_env(source, "AUDIO_FAKE_MODE", default=False),
        default_target_ppq=_int_env(
            source,
            "AUDIO_DEFAULT_TARGET_PPQ",
            _DEFAULT_TARGET_PPQ,
            minimum=24,
            maximum=9600,
        ),
        default_tempo_bpm=_int_env(
            source,
            "AUDIO_DEFAULT_TEMPO_BPM",
            _DEFAULT_DEFAULT_TEMPO_BPM,
            minimum=20,
            maximum=400,
        ),
    )
    logger.debug(
        "Audio transcription settings loaded",
        extra={
            "max_upload_bytes": settings.max_upload_bytes,
            "max_duration_seconds": settings.max_duration_seconds,
            "max_sample_rate": settings.max_sample_rate,
            "confidence_include_threshold": settings.confidence_include_threshold,
            "engine": settings.engine,
            "fake_mode": settings.fake_mode,
            "default_target_ppq": settings.default_target_ppq,
            "default_tempo_bpm": settings.default_tempo_bpm,
        },
    )
    logger.info(
        "Audio transcription settings ready",
        extra={
            "max_upload_bytes": settings.max_upload_bytes,
            "max_duration_seconds": settings.max_duration_seconds,
            "max_sample_rate": settings.max_sample_rate,
            "confidence_include_threshold": settings.confidence_include_threshold,
            "engine": settings.engine,
            "fake_mode": settings.fake_mode,
        },
    )
    return settings


def _engine_env(
    env: Mapping[str, str],
    name: str,
    default: AudioEngineId,
) -> AudioEngineId:
    raw_value = env.get(name)
    if raw_value is None or raw_value.strip() == "":
        return default
    value = raw_value.strip()
    if value not in AUDIO_ENGINE_IDS:
        logger.warning(
            "Invalid audio transcription engine; using default",
            extra={"setting_name": name, "fallback": default},
        )
        return default
    return value  # type: ignore[return-value]


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
        "Invalid boolean audio setting; using default",
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
            "Invalid integer audio setting; using default",
            extra={"setting_name": name, "fallback": default},
        )
        return default
    if value < minimum or value > maximum:
        clamped = min(max(value, minimum), maximum)
        logger.warning(
            "Audio setting out of range; clamped",
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
            "Invalid float audio setting; using default",
            extra={"setting_name": name, "fallback": default},
        )
        return default
    if value < minimum or value > maximum:
        clamped = min(max(value, minimum), maximum)
        logger.warning(
            "Audio setting out of range; clamped",
            extra={
                "setting_name": name,
                "fallback": clamped,
                "minimum": minimum,
                "maximum": maximum,
            },
        )
        return clamped
    return value
