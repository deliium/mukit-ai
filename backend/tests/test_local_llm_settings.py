"""Tests for optional local LLM settings (memory-safe defaults, disabled path)."""

from __future__ import annotations

from app.local_llm_settings import (
    DEFAULT_CONTEXT_SIZE,
    DEFAULT_DEVICE,
    DEFAULT_MAX_CONCURRENCY,
    DEFAULT_MEMORY_LIMIT_MB,
    DEFAULT_TIMEOUT_SECONDS,
    load_local_llm_settings,
    local_llm_enabled,
)
from app.llm_settings import load_llm_settings


def test_local_llm_disabled_by_default():
    settings = load_local_llm_settings({})
    assert settings.enabled is False
    assert local_llm_enabled({}) is False
    assert settings.model_id is None


def test_local_llm_defaults_are_memory_safe():
    settings = load_local_llm_settings(
        {
            "LOCAL_LLM_ENABLED": "1",
            "LOCAL_LLM_MODEL": "instruct-q4",
        }
    )
    assert settings.enabled is True
    assert settings.model_id == "local:instruct-q4"
    assert settings.context_size == DEFAULT_CONTEXT_SIZE
    assert settings.device == DEFAULT_DEVICE
    assert settings.memory_limit_mb == DEFAULT_MEMORY_LIMIT_MB
    assert settings.max_concurrency == DEFAULT_MAX_CONCURRENCY
    assert settings.timeout_seconds == DEFAULT_TIMEOUT_SECONDS
    assert settings.quantization == "Q4_K_M"
    assert settings.base_url.endswith("/v1")


def test_local_llm_invalid_device_falls_back(caplog):
    settings = load_local_llm_settings(
        {
            "LOCAL_LLM_ENABLED": "1",
            "LOCAL_LLM_MODEL": "m",
            "LOCAL_LLM_DEVICE": "npu-experimental",
        }
    )
    assert settings.device == DEFAULT_DEVICE
    assert "Invalid LOCAL_LLM_DEVICE" in caplog.text


def test_local_llm_clamps_concurrency_and_context():
    settings = load_local_llm_settings(
        {
            "LOCAL_LLM_ENABLED": "yes",
            "LOCAL_LLM_MODEL": "m",
            "LOCAL_LLM_MAX_CONCURRENCY": "0",
            "LOCAL_LLM_CONTEXT_SIZE": "64",
        }
    )
    assert settings.max_concurrency == 1
    assert settings.context_size == 256


def test_load_llm_settings_includes_local_when_enabled():
    llm = load_llm_settings(
        {
            "LLM_FAKE_MODE": "1",
            "LOCAL_LLM_ENABLED": "1",
            "LOCAL_LLM_MODEL": "phi-q4",
            "LOCAL_LLM_BASE_URL": "http://local-llm:8080/v1",
        }
    )
    providers = {p.provider: p for p in llm.providers}
    assert "fake" in providers
    assert "local" in providers
    assert providers["local"].base_url == "http://local-llm:8080/v1"
    assert providers["local"].api_key == "local"


def test_load_llm_settings_skips_local_when_disabled():
    llm = load_llm_settings({"LLM_FAKE_MODE": "1", "LOCAL_LLM_ENABLED": "0", "LOCAL_LLM_MODEL": "x"})
    assert all(p.provider != "local" for p in llm.providers)
