"""Bounded health probe for optional local OpenAI-compatible sidecars.

Never downloads weights; never logs absolute paths, prompts, or API keys.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.local_llm_settings import LocalLlmSettings

from .types import ModelHealth, ModelStatus

logger = logging.getLogger(__name__)

DEFAULT_PROBE_TIMEOUT_SECONDS = 2.5
# User-facing detail codes (catalog status uses ready when usable).
DETAIL_LOADED = "loaded"
DETAIL_LOADING = "loading"
DETAIL_UNAVAILABLE = "unavailable"
DETAIL_OUT_OF_MEMORY = "out_of_memory"
DETAIL_UNSUPPORTED_DEVICE = "unsupported_device"


@dataclass(frozen=True)
class LocalHealthProbeResult:
    """Probe outcome mapped to registry status + health.detail."""

    status: ModelStatus
    detail: str
    reachable: bool
    latency_ms: int | None
    error_type: str | None = None


def probe_local_llm_health(
    settings: LocalLlmSettings,
    *,
    timeout_seconds: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
) -> LocalHealthProbeResult:
    """GET ``{base_url}/models`` (OpenAI list) with a short timeout."""
    if not settings.enabled:
        logger.debug("Local health probe skipped; local LLM disabled")
        return LocalHealthProbeResult(
            status="unavailable",
            detail=DETAIL_UNAVAILABLE,
            reachable=False,
            latency_ms=None,
            error_type=None,
        )

    models_url = _models_url(settings.base_url)
    started = time.monotonic()
    logger.debug(
        "Local LLM health probe start",
        extra={"model_id": settings.model_id, "has_base_url": bool(settings.base_url)},
    )
    try:
        request = Request(models_url, method="GET", headers={"Accept": "application/json"})
        with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 — operator URL from env
            body = response.read(4096)
            http_status = getattr(response, "status", 200)
        latency_ms = int((time.monotonic() - started) * 1000)
        result = _map_success(body, http_status=http_status, latency_ms=latency_ms)
        logger.info(
            "Local LLM health probe result",
            extra={
                "model_id": settings.model_id,
                "status": result.status,
                "detail": result.detail,
                "reachable": result.reachable,
                "latency_ms": latency_ms,
            },
        )
        return result
    except HTTPError as exc:
        latency_ms = int((time.monotonic() - started) * 1000)
        result = _map_http_error(exc, latency_ms=latency_ms)
        _log_probe_failure(settings, result)
        return result
    except (URLError, TimeoutError, OSError) as exc:
        latency_ms = int((time.monotonic() - started) * 1000)
        result = LocalHealthProbeResult(
            status="unavailable",
            detail=DETAIL_UNAVAILABLE,
            reachable=False,
            latency_ms=latency_ms,
            error_type=type(exc).__name__,
        )
        _log_probe_failure(settings, result)
        return result
    except Exception as exc:  # noqa: BLE001
        latency_ms = int((time.monotonic() - started) * 1000)
        result = LocalHealthProbeResult(
            status="unavailable",
            detail=DETAIL_UNAVAILABLE,
            reachable=False,
            latency_ms=latency_ms,
            error_type=type(exc).__name__,
        )
        _log_probe_failure(settings, result)
        return result


def health_from_probe(probe: LocalHealthProbeResult) -> ModelHealth:
    return ModelHealth(
        status=probe.status,
        detail=probe.detail,
        credentials_present=True,
    )


def public_limits(settings: LocalLlmSettings) -> dict[str, Any]:
    """Non-secret limits for discovery (no host paths)."""
    return {
        "context_size": settings.context_size,
        "max_concurrency": settings.max_concurrency,
        "memory_limit_mb": settings.memory_limit_mb,
        "quantization": settings.quantization,
        "device": settings.device,
    }


def _models_url(base_url: str) -> str:
    root = (base_url or "").rstrip("/")
    if root.endswith("/v1"):
        return f"{root}/models"
    return f"{root}/v1/models"


def _map_success(body: bytes, *, http_status: int, latency_ms: int) -> LocalHealthProbeResult:
    text = body.decode("utf-8", errors="replace").lower()
    if "out of memory" in text or "oom" in text:
        return LocalHealthProbeResult(
            status="out_of_memory",
            detail=DETAIL_OUT_OF_MEMORY,
            reachable=True,
            latency_ms=latency_ms,
            error_type="oom_body",
        )
    if "loading" in text and "model" in text:
        return LocalHealthProbeResult(
            status="loading",
            detail=DETAIL_LOADING,
            reachable=True,
            latency_ms=latency_ms,
        )
    if http_status == 200:
        # Usable: catalog status ready; detail carries loaded for precision.
        return LocalHealthProbeResult(
            status="ready",
            detail=DETAIL_LOADED,
            reachable=True,
            latency_ms=latency_ms,
        )
    return LocalHealthProbeResult(
        status="unavailable",
        detail=DETAIL_UNAVAILABLE,
        reachable=True,
        latency_ms=latency_ms,
        error_type=f"http_{http_status}",
    )


def _map_http_error(exc: HTTPError, *, latency_ms: int) -> LocalHealthProbeResult:
    code = int(getattr(exc, "code", 0) or 0)
    body = ""
    try:
        body = (exc.read(1024) or b"").decode("utf-8", errors="replace").lower()
    except Exception:  # noqa: BLE001
        body = ""
    if code == 507 or "out of memory" in body or "oom" in body:
        return LocalHealthProbeResult(
            status="out_of_memory",
            detail=DETAIL_OUT_OF_MEMORY,
            reachable=True,
            latency_ms=latency_ms,
            error_type="HTTPError",
        )
    if "unsupported" in body and "device" in body:
        return LocalHealthProbeResult(
            status="unsupported_device",
            detail=DETAIL_UNSUPPORTED_DEVICE,
            reachable=True,
            latency_ms=latency_ms,
            error_type="HTTPError",
        )
    if code in {503, 425} or "loading" in body:
        return LocalHealthProbeResult(
            status="loading",
            detail=DETAIL_LOADING,
            reachable=True,
            latency_ms=latency_ms,
            error_type="HTTPError",
        )
    return LocalHealthProbeResult(
        status="unavailable",
        detail=DETAIL_UNAVAILABLE,
        reachable=True,
        latency_ms=latency_ms,
        error_type="HTTPError",
    )


def _log_probe_failure(settings: LocalLlmSettings, result: LocalHealthProbeResult) -> None:
    level = logger.warning if result.status in {"out_of_memory", "unsupported_device"} else logger.info
    level(
        "Local LLM health probe result",
        extra={
            "model_id": settings.model_id,
            "status": result.status,
            "detail": result.detail,
            "reachable": result.reachable,
            "latency_ms": result.latency_ms,
            "error_type": result.error_type,
        },
    )


def local_ai_readiness_block(
    settings: LocalLlmSettings,
    probe: LocalHealthProbeResult | None = None,
) -> dict[str, Any]:
    """Soft ``local_ai`` subsection for `/ready` (non-secret)."""
    active_probe = probe
    if settings.enabled and settings.model and active_probe is None:
        active_probe = probe_local_llm_health(settings)
    if not settings.enabled:
        block = {
            "enabled": False,
            "reachable": False,
            "status": "disabled",
            "model_id": None,
            "device": settings.device,
            "runtime_profile_hint": "local-ai",
        }
        logger.debug("Readiness local_ai skipped", extra={"reason": "disabled"})
        return block

    if active_probe is None:
        block = {
            "enabled": True,
            "reachable": False,
            "status": "unavailable",
            "model_id": settings.model_id,
            "device": settings.device,
            "runtime_profile_hint": "local-ai",
        }
        logger.info("Readiness local_ai summary", extra={k: block[k] for k in block})
        return block

    block = {
        "enabled": True,
        "reachable": active_probe.reachable,
        "status": active_probe.status,
        "model_id": settings.model_id,
        "device": settings.device,
        "runtime_profile_hint": "local-ai",
        "health_detail": active_probe.detail,
    }
    logger.info(
        "Readiness local_ai summary",
        extra={
            "enabled": True,
            "reachable": active_probe.reachable,
            "status": active_probe.status,
            "model_id": settings.model_id,
            "device": settings.device,
        },
    )
    return block
