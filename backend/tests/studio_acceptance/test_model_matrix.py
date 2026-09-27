"""Fake-bundle model selection never opens a remote client."""

from __future__ import annotations

import logging
from io import BytesIO
from urllib.error import HTTPError

import pytest

from app.ai_runtime.errors import ModelNotFoundError, ModelUnavailableError
from app.ai_runtime.local_health import probe_local_llm_health
from app.ai_runtime.operations import AiOperation
from app.ai_runtime.registry import reload_registry
from app.ai_runtime.routing import ModelSelectionInput, resolve_provider_for_operation
from app.llm_settings import load_llm_settings
from app.local_llm_settings import LocalLlmSettings
from app.operation_trace import runtime_is_local
from tests.studio_acceptance.conftest import FAKE_BUNDLE

_SENTINEL = "sk-studio-model-sentinel"


def _settings(env: dict[str, str]):
    merged = {**FAKE_BUNDLE, "NEURAL_AUDIO_ENGINE": "fake:neural-audio", **env}
    reload_registry(merged)
    return load_llm_settings(merged), merged


def test_fake_bundle_selects_fake_language_model_without_a_remote_client(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", _SENTINEL)
    monkeypatch.setattr(
        "app.services.llm_chat_client.build_chat_openai",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("remote client")),
    )
    caplog.set_level(logging.DEBUG)
    settings, env = _settings({})
    provider, resolved = resolve_provider_for_operation(
        AiOperation.GENERATE,
        ModelSelectionInput(),
        settings,
        env=env,
    )
    assert provider.provider == "fake"
    assert resolved.descriptor.runtime == "fake"
    assert resolved.resolved_model_id.startswith("fake:")
    assert _SENTINEL not in caplog.text


def test_unknown_remote_id_fails_without_a_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    settings, env = _settings({"OPENAI_API_KEY": "unused"})
    monkeypatch.setattr(
        "socket.create_connection",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("socket")),
    )
    with pytest.raises((ModelNotFoundError, ModelUnavailableError)):
        resolve_provider_for_operation(
            AiOperation.GENERATE,
            ModelSelectionInput(model_id="openai:not-a-real-model"),
            settings,
            env=env,
        )


def test_local_probe_uses_status_code_only(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = LocalLlmSettings(
        enabled=True,
        base_url="http://127.0.0.1:9/v1",
        model="tiny",
        context_size=4096,
        quantization="Q4_K_M",
        device="cpu",
        memory_limit_mb=1024,
        max_concurrency=1,
        timeout_seconds=1,
        api_key="local",
    )

    class _Ready:
        status = 200

        def read(self, _n: int) -> bytes:
            return b'{"data":[]}'

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

    monkeypatch.setattr("app.ai_runtime.local_health.urlopen", lambda *_a, **_k: _Ready())
    ready = probe_local_llm_health(settings)
    assert ready.status == "ready"
    assert ready.detail == "loaded"

    def _unavailable(*_args, **_kwargs):
        raise HTTPError("http://127.0.0.1:9/v1/models", 503, "unavailable", hdrs=None, fp=BytesIO(b""))

    monkeypatch.setattr("app.ai_runtime.local_health.urlopen", _unavailable)
    down = probe_local_llm_health(settings)
    assert down.status == "loading"
    assert down.detail == "loading"


def test_runtime_is_local_classifies_local_fake_stub_and_plugin() -> None:
    assert runtime_is_local("local")
    assert runtime_is_local("local_openai_compatible")
    assert runtime_is_local("fake")
    assert runtime_is_local("stub")
    assert runtime_is_local("plugin")
    assert runtime_is_local("openai") is False
    assert runtime_is_local(None) is False
