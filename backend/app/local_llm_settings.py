"""Optional local OpenAI-compatible LLM settings (sidecar URL only; no weight paths)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Mapping

logger = logging.getLogger(__name__)

LOCAL_PROVIDER = "local"
LOCAL_LLM_ENABLED_ENV = "LOCAL_LLM_ENABLED"
LOCAL_RUNTIME_ID = "local_openai_compatible"

# Memory-safe defaults for ~32 GB UMA AMD APU laptops (see docs/local-ai.md).
DEFAULT_BASE_URL = "http://local-llm:8080/v1"
DEFAULT_CONTEXT_SIZE = 4096
DEFAULT_QUANTIZATION = "Q4_K_M"
DEFAULT_DEVICE = "rocm"
DEFAULT_MEMORY_LIMIT_MB = 12288
DEFAULT_MAX_CONCURRENCY = 1
DEFAULT_TIMEOUT_SECONDS = 180
DEFAULT_API_KEY = "local"
ALLOWED_DEVICES = frozenset({"rocm", "vulkan", "cpu"})


@dataclass(frozen=True)
class LocalLlmSettings:
    """Server-side local sidecar config. Never expose host weight paths via APIs."""

    enabled: bool
    base_url: str
    model: str
    context_size: int
    quantization: str
    device: str
    memory_limit_mb: int
    max_concurrency: int
    timeout_seconds: int
    api_key: str

    @property
    def model_id(self) -> str | None:
        if not self.enabled or not self.model:
            return None
        return f"{LOCAL_PROVIDER}:{self.model}"

    @property
    def display_name(self) -> str:
        label = self.model or "local"
        return f"Local ({label})"


def local_llm_enabled(env: Mapping[str, str] | None = None) -> bool:
    source = env if env is not None else os.environ
    raw = (source.get(LOCAL_LLM_ENABLED_ENV) or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def load_local_llm_settings(env: Mapping[str, str] | None = None) -> LocalLlmSettings:
    """Load LOCAL_* knobs with memory-safe defaults. Invalid values warn and fall back."""
    source = env if env is not None else os.environ
    enabled = local_llm_enabled(source)

    logger.debug(
        "Local LLM env key presence (booleans only)",
        extra={
            LOCAL_LLM_ENABLED_ENV: enabled,
            "LOCAL_LLM_BASE_URL": bool(source.get("LOCAL_LLM_BASE_URL")),
            "LOCAL_LLM_MODEL": bool(source.get("LOCAL_LLM_MODEL")),
            "LOCAL_LLM_CONTEXT_SIZE": bool(source.get("LOCAL_LLM_CONTEXT_SIZE")),
            "LOCAL_LLM_QUANTIZATION": bool(source.get("LOCAL_LLM_QUANTIZATION")),
            "LOCAL_LLM_DEVICE": bool(source.get("LOCAL_LLM_DEVICE")),
            "LOCAL_LLM_MEMORY_LIMIT_MB": bool(source.get("LOCAL_LLM_MEMORY_LIMIT_MB")),
            "LOCAL_LLM_MAX_CONCURRENCY": bool(source.get("LOCAL_LLM_MAX_CONCURRENCY")),
            "LOCAL_LLM_TIMEOUT_SECONDS": bool(source.get("LOCAL_LLM_TIMEOUT_SECONDS")),
            "LOCAL_LLM_API_KEY": bool(source.get("LOCAL_LLM_API_KEY")),
        },
    )

    base_url = (source.get("LOCAL_LLM_BASE_URL") or DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL
    # Strip trailing slashes but keep /v1 path shape for ChatOpenAI.
    base_url = base_url.rstrip("/")
    model = (source.get("LOCAL_LLM_MODEL") or "").strip()
    quantization = (source.get("LOCAL_LLM_QUANTIZATION") or DEFAULT_QUANTIZATION).strip() or DEFAULT_QUANTIZATION
    device_raw = (source.get("LOCAL_LLM_DEVICE") or DEFAULT_DEVICE).strip().lower() or DEFAULT_DEVICE
    if device_raw not in ALLOWED_DEVICES:
        logger.warning(
            "Invalid LOCAL_LLM_DEVICE; falling back to default",
            extra={"device": device_raw, "fallback": DEFAULT_DEVICE},
        )
        device_raw = DEFAULT_DEVICE

    context_size = _int_env(source, "LOCAL_LLM_CONTEXT_SIZE", DEFAULT_CONTEXT_SIZE, 256, 131072)
    memory_limit_mb = _int_env(source, "LOCAL_LLM_MEMORY_LIMIT_MB", DEFAULT_MEMORY_LIMIT_MB, 1024, 65536)
    max_concurrency = _int_env(source, "LOCAL_LLM_MAX_CONCURRENCY", DEFAULT_MAX_CONCURRENCY, 1, 8)
    timeout_seconds = _int_env(source, "LOCAL_LLM_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS, 30, 600)
    api_key = (source.get("LOCAL_LLM_API_KEY") or DEFAULT_API_KEY).strip() or DEFAULT_API_KEY

    settings = LocalLlmSettings(
        enabled=enabled,
        base_url=base_url,
        model=model,
        context_size=context_size,
        quantization=quantization,
        device=device_raw,
        memory_limit_mb=memory_limit_mb,
        max_concurrency=max_concurrency,
        timeout_seconds=timeout_seconds,
        api_key=api_key,
    )

    if enabled:
        if not model:
            logger.warning(
                "LOCAL_LLM_ENABLED but LOCAL_LLM_MODEL empty; registry will skip local chat model",
                extra={"device": settings.device, "context_size": settings.context_size},
            )
        else:
            logger.info(
                "Local LLM enabled",
                extra={
                    "model_id": settings.model_id,
                    "device": settings.device,
                    "context_size": settings.context_size,
                    "max_concurrency": settings.max_concurrency,
                    "memory_limit_mb": settings.memory_limit_mb,
                    "quantization": settings.quantization,
                    "timeout_seconds": settings.timeout_seconds,
                    "has_base_url": bool(settings.base_url),
                },
            )
    else:
        logger.debug("Local LLM disabled; skipping sidecar registration")

    return settings


def _int_env(env: Mapping[str, str], name: str, default: int, minimum: int, maximum: int) -> int:
    raw_value = env.get(name)
    if raw_value is None or not str(raw_value).strip():
        return default
    try:
        value = int(str(raw_value).strip())
    except ValueError:
        logger.warning(
            "Invalid integer local LLM setting; using default",
            extra={"setting_name": name, "fallback": default},
        )
        return default
    clamped = min(max(value, minimum), maximum)
    if clamped != value:
        logger.warning(
            "Local LLM setting clamped to safe range",
            extra={"setting_name": name, "fallback": clamped},
        )
    return clamped
