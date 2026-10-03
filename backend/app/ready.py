"""Application readiness helpers: DB, LLM provider presence, WAV deps, arrangement catalog.

Never include secrets, prompts, catalog override contents, or binary payloads in readiness
payloads.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from .db import ensure_database
from .llm_settings import load_llm_settings
from .services.composition_wav import load_wav_renderer_config
from .services.instrument_catalog import InstrumentCatalogError, get_catalog_meta

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


def _ai_registry_summary() -> dict[str, Any]:
    """Non-secret AI registry snapshot for /ready (ids and counts only)."""
    try:
        from app.ai_runtime.registry import get_default_model_id, list_models, reload_registry
        from app.ai_runtime.routing import default_operation_routes

        reload_registry()
        models = list_models()
        by_capability: dict[str, int] = {}
        by_status: dict[str, int] = {}
        for model in models:
            cap = str(model.primary_capability)
            by_capability[cap] = by_capability.get(cap, 0) + 1
            by_status[model.status] = by_status.get(model.status, 0) + 1
        summary = {
            "model_count": len(models),
            "default_model_id": get_default_model_id(),
            "by_capability": by_capability,
            "by_status": by_status,
            "operation_defaults": default_operation_routes(),
            "model_ids": [m.id for m in models],
        }
        logger.debug("Readiness AI registry summary", extra={"model_count": len(models)})
        return summary
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Readiness AI registry summary failed",
            extra={"error_type": type(exc).__name__},
        )
        return {
            "model_count": 0,
            "default_model_id": None,
            "by_capability": {},
            "by_status": {},
            "operation_defaults": {},
            "model_ids": [],
            "error_type": type(exc).__name__,
        }


def _browser_models_readiness_block() -> dict[str, Any]:
    """Soft registry-only BrowserModel note. Never scans SPA public/ or fails ready."""
    try:
        from app.ai_runtime.registry import list_models, reload_registry

        reload_registry()
        browser_rows = [m for m in list_models() if str(m.runtime) == "browser_model"]
        model_ids = [m.id for m in browser_rows][:16]
        digest_prefixes: list[str] = []
        for model in browser_rows:
            limits = dict(model.limits or {})
            prefix = limits.get("asset_digest_prefix")
            if isinstance(prefix, str) and prefix.strip():
                digest_prefixes.append(prefix.strip()[:16])
            else:
                # Public algorithm identity when asset_kind=none (no weight digest).
                algo = limits.get("algorithm_version")
                if isinstance(algo, str) and algo.strip():
                    digest_prefixes.append(algo.strip()[:24])
        block = {
            "descriptor_count": len(browser_rows),
            "model_ids": model_ids,
            "limits_digest_prefixes": digest_prefixes[:16],
            "server_executable": False,
        }
        logger.debug(
            "Readiness browser_models block assembled",
            extra={
                "descriptor_count": block["descriptor_count"],
                "model_id_count": len(model_ids),
            },
        )
        return block
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Readiness browser_models block failed",
            extra={"error_type": type(exc).__name__},
        )
        return {
            "descriptor_count": 0,
            "model_ids": [],
            "limits_digest_prefixes": [],
            "server_executable": False,
            "error_type": type(exc).__name__,
        }


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

    from app.local_llm_settings import load_local_llm_settings
    from app.ai_runtime.local_health import local_ai_readiness_block

    local_settings = load_local_llm_settings()
    local_ai_block = local_ai_readiness_block(local_settings)

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

    catalog_ok = False
    catalog_block: dict[str, Any] = {
        "ok": False,
        "catalog_version": None,
        "range_policy_version": None,
        "fingerprint": None,
        "profile_count": None,
        "source_path_category": None,
        "error_code": None,
    }
    try:
        meta = get_catalog_meta()
        catalog_ok = True
        catalog_block = {
            "ok": True,
            "catalog_version": meta.catalog_version,
            "range_policy_version": meta.range_policy_version,
            "fingerprint": meta.fingerprint,
            "profile_count": meta.profile_count,
            "source_path_category": meta.source_path_category,
            "error_code": None,
        }
        logger.info(
            "Readiness arrangement catalog check ok",
            extra={
                "catalog_version": meta.catalog_version,
                "range_policy_version": meta.range_policy_version,
                "fingerprint_prefix": meta.fingerprint[:12],
                "profile_count": meta.profile_count,
                "source_path_category": meta.source_path_category,
            },
        )
    except InstrumentCatalogError as exc:
        catalog_block["error_code"] = exc.code
        catalog_block["source_path_category"] = exc.path_category
        logger.warning(
            "Readiness arrangement catalog check failed",
            extra={
                "error_code": exc.code,
                "path_category": exc.path_category,
                "error_type": type(exc).__name__,
            },
        )
    except Exception as exc:  # noqa: BLE001
        catalog_block["error_code"] = "catalog_invalid_schema"
        logger.warning(
            "Readiness arrangement catalog check unexpected failure",
            extra={"error_type": type(exc).__name__},
        )

    # App can serve projects/exports without LLM; LLM routes stay 503.
    # Invalid configured arrangement catalog makes the process not ready.
    ai_summary = _ai_registry_summary()
    from app.services.music_transformer_generate import music_transformer_readiness_block

    mt_block = music_transformer_readiness_block()
    from app.services.execution_node_service import execution_nodes_readiness_block

    execution_nodes_block = execution_nodes_readiness_block()
    from app.scheduling_settings import ai_scheduling_readiness_block

    ai_scheduling_block = ai_scheduling_readiness_block()
    browser_models_block = _browser_models_readiness_block()
    ready = bool(db_ok and catalog_ok)
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
        "local_ai": local_ai_block,
        "execution_nodes": execution_nodes_block,
        "ai_scheduling": ai_scheduling_block,
        "browser_models": browser_models_block,
        "ai": ai_summary,
        "wav": {
            "ready": wav_ready,
            "fluidsynth_exists": wav_config.fluidsynth_exists,
            "soundfont_exists": wav_config.soundfont_exists,
        },
        "arrangement_catalog": catalog_block,
        "music_transformer": mt_block,
    }
    logger.info(
        "Readiness check completed",
        extra={
            "ready": ready,
            "db_ok": db_ok,
            "catalog_ok": catalog_ok,
            "llm_configured": bool(provider_names),
            "wav_ready": wav_ready,
            "local_ai_enabled": bool(local_ai_block.get("enabled")),
            "local_ai_status": local_ai_block.get("status"),
            "music_transformer_api_enabled": bool(mt_block.get("enabled")),
        },
    )
    return report
