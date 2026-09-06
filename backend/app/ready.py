"""Application readiness helpers: DB, LLM provider presence, WAV deps.

Never include secrets, prompts, or binary payloads in readiness payloads.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from .db import ensure_database
from .llm_settings import load_llm_settings
from .services.composition_wav import load_wav_renderer_config

logger = logging.getLogger(__name__)

DEFAULT_CORS_ORIGINS = (
    "http://localhost:3000",
    "http://127.0.0.1:3000",
)


def configure_logging(log_level: str | None = None) -> str:
    """Configure root logging from LOG_LEVEL (DEBUG/INFO/WARNING/ERROR)."""
    raw = (log_level if log_level is not None else os.environ.get("LOG_LEVEL", "INFO")).strip().upper()
    level = getattr(logging, raw, None)
    if not isinstance(level, int):
        logger.warning("Invalid LOG_LEVEL; falling back to INFO", extra={"raw_log_level": raw})
        raw = "INFO"
        level = logging.INFO

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        force=True,
    )
    logging.getLogger().setLevel(level)
    logger.info("Logging configured", extra={"log_level": raw})
    return raw


def parse_cors_allow_origins(raw: str | None = None) -> list[str]:
    """Parse comma-separated CORS_ALLOW_ORIGINS into a clean origin list."""
    source = raw if raw is not None else os.environ.get("CORS_ALLOW_ORIGINS")
    if source is None or not str(source).strip():
        origins = list(DEFAULT_CORS_ORIGINS)
        logger.debug("CORS origins using defaults", extra={"origins": origins})
        return origins

    origins = [part.strip() for part in str(source).split(",") if part.strip()]
    if not origins:
        origins = list(DEFAULT_CORS_ORIGINS)
        logger.debug("CORS origins empty after parse; using defaults", extra={"origins": origins})
        return origins

    logger.debug("CORS origins loaded from env", extra={"origins": origins, "count": len(origins)})
    return origins


def build_readiness_report() -> dict[str, Any]:
    """Collect non-secret readiness flags for GET /ready."""
    logger.info("Readiness check started")

    db_ok = False
    db_path: str | None = None
    try:
        path = ensure_database()
        db_path = str(path)
        db_ok = True
        logger.info("Readiness DB check ok", extra={"project_db_path": db_path})
    except Exception as exc:
        logger.warning("Readiness DB check failed", extra={"error_type": type(exc).__name__})

    llm_settings = load_llm_settings()
    provider_names = [provider.provider for provider in llm_settings.providers]
    logger.info(
        "Readiness LLM check",
        extra={
            "provider_count": len(provider_names),
            "providers": provider_names,
            "default_provider": llm_settings.default_provider,
        },
    )

    wav_config = load_wav_renderer_config()
    wav_ready = bool(wav_config.fluidsynth_exists and wav_config.soundfont_exists)
    logger.info(
        "Readiness WAV check",
        extra={
            "fluidsynth_exists": wav_config.fluidsynth_exists,
            "soundfont_exists": wav_config.soundfont_exists,
            "wav_ready": wav_ready,
            "fluidsynth_bin": wav_config.fluidsynth_basename,
            "soundfont": wav_config.soundfont_basename,
        },
    )

    ready = db_ok  # App can serve projects/exports without LLM; LLM routes stay 503.
    report = {
        "status": "ready" if ready else "not_ready",
        "ready": ready,
        "database": {
            "ok": db_ok,
            "path": db_path,
        },
        "llm": {
            "configured": bool(provider_names),
            "provider_count": len(provider_names),
            "providers": provider_names,
            "default_provider": llm_settings.default_provider,
        },
        "wav": {
            "ready": wav_ready,
            "fluidsynth_exists": wav_config.fluidsynth_exists,
            "soundfont_exists": wav_config.soundfont_exists,
        },
    }
    logger.info(
        "Readiness check completed",
        extra={
            "ready": ready,
            "db_ok": db_ok,
            "llm_configured": bool(provider_names),
            "wav_ready": wav_ready,
        },
    )
    return report
