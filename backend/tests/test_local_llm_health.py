"""Tests for local LLM health probe and registry bootstrap."""

from __future__ import annotations

from io import BytesIO
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from app.ai_runtime.bootstrap import build_registry_from_env
from app.ai_runtime.local_health import (
    DETAIL_LOADED,
    DETAIL_LOADING,
    DETAIL_OUT_OF_MEMORY,
    DETAIL_UNSUPPORTED_DEVICE,
    local_ai_readiness_block,
    probe_local_llm_health,
)
from app.ai_runtime.operations import AiOperation
from app.ai_runtime.routing import ModelSelectionInput, resolve_model_for_operation
from app.local_llm_settings import load_local_llm_settings
from app.ready import build_readiness_report


def _enabled_env(**extra: str) -> dict[str, str]:
    base = {
        "LOCAL_LLM_ENABLED": "1",
        "LOCAL_LLM_MODEL": "instruct-q4",
        "LOCAL_LLM_BASE_URL": "http://local-llm:8080/v1",
        "LLM_FAKE_MODE": "1",
    }
    base.update(extra)
    return base


def test_probe_maps_ready_loaded():
    settings = load_local_llm_settings(_enabled_env())
    response = MagicMock()
    response.status = 200
    response.read.return_value = b'{"data":[{"id":"instruct-q4"}]}'
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    with patch("app.ai_runtime.local_health.urlopen", return_value=response):
        result = probe_local_llm_health(settings)
    assert result.status == "ready"
    assert result.detail == DETAIL_LOADED
    assert result.reachable is True


def test_probe_maps_loading_http():
    settings = load_local_llm_settings(_enabled_env())
    err = HTTPError("http://x", 503, "loading", hdrs=None, fp=BytesIO(b"model loading"))
    with patch("app.ai_runtime.local_health.urlopen", side_effect=err):
        result = probe_local_llm_health(settings)
    assert result.status == "loading"
    assert result.detail == DETAIL_LOADING


def test_probe_maps_oom():
    settings = load_local_llm_settings(_enabled_env())
    err = HTTPError("http://x", 507, "oom", hdrs=None, fp=BytesIO(b"out of memory"))
    with patch("app.ai_runtime.local_health.urlopen", side_effect=err):
        result = probe_local_llm_health(settings)
    assert result.status == "out_of_memory"
    assert result.detail == DETAIL_OUT_OF_MEMORY


def test_probe_maps_unsupported_device():
    settings = load_local_llm_settings(_enabled_env())
    err = HTTPError(
        "http://x",
        500,
        "err",
        hdrs=None,
        fp=BytesIO(b"unsupported device backend"),
    )
    with patch("app.ai_runtime.local_health.urlopen", side_effect=err):
        result = probe_local_llm_health(settings)
    assert result.status == "unsupported_device"
    assert result.detail == DETAIL_UNSUPPORTED_DEVICE


def test_probe_unreachable():
    settings = load_local_llm_settings(_enabled_env())
    with patch("app.ai_runtime.local_health.urlopen", side_effect=URLError("down")):
        result = probe_local_llm_health(settings)
    assert result.status == "unavailable"
    assert result.reachable is False


def test_registry_lists_local_when_enabled_and_ready():
    env = _enabled_env()
    response = MagicMock()
    response.status = 200
    response.read.return_value = b'{"data":[]}'
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    with patch("app.ai_runtime.local_health.urlopen", return_value=response):
        models, _default = build_registry_from_env(env)
    local = models["local:instruct-q4"]
    assert local.runtime == "local_openai_compatible"
    assert local.locality == "local"
    assert local.status == "ready"
    assert local.health.detail == DETAIL_LOADED
    assert "context_size" in local.limits
    assert "/home/" not in str(local.limits)
    assert "models/llm" not in str(local.limits)


def test_registry_absent_when_local_disabled():
    models, _ = build_registry_from_env({"LLM_FAKE_MODE": "1", "LOCAL_LLM_ENABLED": "0"})
    assert not any(m.runtime == "local_openai_compatible" for m in models.values())


def test_ready_soft_local_ai_section(monkeypatch, tmp_path):
    monkeypatch.setenv("PROJECT_DB_PATH", str(tmp_path / "ready-local.db"))
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    monkeypatch.setenv("LOCAL_LLM_ENABLED", "1")
    monkeypatch.setenv("LOCAL_LLM_MODEL", "instruct-q4")
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://127.0.0.1:9/v1")
    with patch("app.ai_runtime.local_health.urlopen", side_effect=URLError("down")):
        report = build_readiness_report()
    assert report["ready"] is True or report["database"]["ok"] is True
    local_ai = report["local_ai"]
    assert local_ai["enabled"] is True
    assert local_ai["reachable"] is False
    assert local_ai["model_id"] == "local:instruct-q4"
    assert "api_key" not in str(local_ai).lower() or local_ai.get("api_key") is None


def test_routing_resolves_ready_local(monkeypatch):
    env = _enabled_env()
    response = MagicMock()
    response.status = 200
    response.read.return_value = b'{"data":[]}'
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    with patch("app.ai_runtime.local_health.urlopen", return_value=response):
        resolved = resolve_model_for_operation(
            AiOperation.GENERATE,
            ModelSelectionInput(provider="local", model="instruct-q4"),
            env=env,
            reload=True,
        )
    assert resolved.resolved_model_id == "local:instruct-q4"
    assert resolved.descriptor.runtime == "local_openai_compatible"


def test_local_ai_readiness_disabled_block():
    settings = load_local_llm_settings({"LOCAL_LLM_ENABLED": "0"})
    block = local_ai_readiness_block(settings)
    assert block["enabled"] is False
    assert block["status"] == "disabled"
