"""Integration: select local:* and resolve generate routing without a real GPU."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.ai_runtime import registry as registry_mod
from app.ai_runtime.operations import AiOperation
from app.ai_runtime.routing import (
    ModelSelectionInput,
    provider_settings_for_resolved,
    resolve_model_for_operation,
    resolve_provider_for_operation,
)
from app.main import app


@pytest.fixture(autouse=True)
def _clear_registry():
    registry_mod.clear_registry_for_tests()
    yield
    registry_mod.clear_registry_for_tests()


def _ready_local_env() -> dict[str, str]:
    return {
        "LLM_FAKE_MODE": "0",
        "LOCAL_LLM_ENABLED": "1",
        "LOCAL_LLM_MODEL": "instruct-q4",
        "LOCAL_LLM_BASE_URL": "http://127.0.0.1:18080/v1",
        "LOCAL_LLM_API_KEY": "local",
        "DEFAULT_LLM_PROVIDER": "local",
    }


def test_resolve_provider_for_local_generate_uses_base_url():
    env = _ready_local_env()
    response = MagicMock()
    response.status = 200
    response.read.return_value = b'{"data":[{"id":"instruct-q4"}]}'
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    with patch("app.ai_runtime.local_health.urlopen", return_value=response):
        provider, resolved = resolve_provider_for_operation(
            AiOperation.GENERATE,
            ModelSelectionInput(model_id="local:instruct-q4"),
            env=env,
        )
    assert resolved.descriptor.runtime == "local_openai_compatible"
    assert provider.provider == "local"
    assert provider.base_url == "http://127.0.0.1:18080/v1"
    assert provider.api_key == "local"


def test_llm_models_lists_ready_local_only(monkeypatch):
    for key, value in _ready_local_env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    response = MagicMock()
    response.status = 200
    response.read.return_value = b'{"data":[]}'
    response.__enter__.return_value = response
    response.__exit__.return_value = False

    client = TestClient(app)
    with patch("app.ai_runtime.local_health.urlopen", return_value=response):
        registry_mod.reload_registry()
        listed = client.get("/llm/models")
    assert listed.status_code == 200
    body = listed.json()
    assert any(m["provider"] == "local" and m["model"] == "instruct-q4" for m in body["models"])
    assert all("api_key" not in str(m).lower() for m in body["models"])


def test_llm_models_omits_unready_local(monkeypatch):
    from urllib.error import URLError

    for key, value in _ready_local_env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    client = TestClient(app)
    with patch("app.ai_runtime.local_health.urlopen", side_effect=URLError("down")):
        registry_mod.reload_registry()
        listed = client.get("/llm/models")
    assert listed.status_code == 200
    body = listed.json()
    assert not any(m["provider"] == "local" for m in body["models"])


def test_default_compose_path_no_local_dependency(monkeypatch):
    """LOCAL_LLM_ENABLED unset/0 → registry has no local_openai_compatible chat model."""
    monkeypatch.delenv("LOCAL_LLM_ENABLED", raising=False)
    monkeypatch.delenv("LOCAL_LLM_MODEL", raising=False)
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    registry_mod.reload_registry()
    models = registry_mod.list_models()
    assert not any(m.runtime == "local_openai_compatible" for m in models)
    # Stubs may still use provider=local with runtime=stub.
    assert any(m.runtime == "stub" for m in models)


def test_generate_route_with_mocked_local_sidecar(monkeypatch, tmp_path):
    """Generate path resolves LocalLanguageModel transport (mocked HTTP) when local selected."""
    monkeypatch.setenv("PROJECT_DB_PATH", str(tmp_path / "local-gen.db"))
    for key, value in _ready_local_env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    probe = MagicMock()
    probe.status = 200
    probe.read.return_value = b'{"data":[]}'
    probe.__enter__.return_value = probe
    probe.__exit__.return_value = False

    # Prefer resolving + LocalLanguageModel.complete_text over full multi-stage generate.
    with patch("app.ai_runtime.local_health.urlopen", return_value=probe):
        resolved = resolve_model_for_operation(
            AiOperation.GENERATE,
            ModelSelectionInput(provider="local", model="instruct-q4"),
            env=dict(**{k: v for k, v in __import__("os").environ.items()}),
            reload=True,
        )
        settings = provider_settings_for_resolved(
            resolved,
            env=dict(**{k: v for k, v in __import__("os").environ.items()}),
        )

    from app.ai_runtime.runtimes.local_language import build_local_language_model
    from app.llm_settings import load_llm_settings
    import asyncio

    llm_settings = load_llm_settings()
    with patch(
        "app.ai_runtime.runtimes.local_language.build_chat_openai",
        return_value=MagicMock(),
    ):
        with patch(
            "app.ai_runtime.runtimes.local_language.ainvoke_chat_text",
            new_callable=AsyncMock,
            return_value='{"schema_version":"composition.v2"}',
        ) as ainvoke:
            model = build_local_language_model(
                resolved.descriptor,
                provider_settings=settings,
                llm_settings=llm_settings,
                purpose="integration_local_generate",
            )
            text = asyncio.run(model.complete_text("compose a phrase"))
    assert "composition" in text
    ainvoke.assert_awaited_once()
    assert resolved.descriptor.runtime == "local_openai_compatible"
