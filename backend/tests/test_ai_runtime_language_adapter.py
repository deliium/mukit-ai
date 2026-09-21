"""Tests for AI runtime language / stub adapters."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ai_runtime.capabilities import ModelCapability
from app.ai_runtime.errors import ModelUnavailableError
from app.ai_runtime.operations import AiOperation, creative_chat_operations
from app.ai_runtime.runtimes.fake_language import build_fake_language_model
from app.ai_runtime.runtimes.openai_compatible_chat import (
    OpenAICompatibleChatLanguageModel,
    build_openai_compatible_language_model,
)
from app.ai_runtime.runtimes.stub import build_stub_model
from app.ai_runtime.types import ModelDescriptor, ModelHealth
from app.llm_settings import LLMProviderSettings, LLMSettings


def _chat_descriptor(*, model_id: str = "openai:gpt-4o-mini") -> ModelDescriptor:
    return ModelDescriptor(
        id=model_id,
        display_name=model_id,
        provider="openai",
        runtime="openai_compatible_chat",
        primary_capability=ModelCapability.LANGUAGE_PLANNER,
        locality="remote",
        model_version="gpt-4o-mini",
        supported_operations=creative_chat_operations(),
        status="ready",
        health=ModelHealth(status="ready", credentials_present=True),
        provider_model="gpt-4o-mini",
    )


def _settings() -> tuple[LLMProviderSettings, LLMSettings]:
    provider = LLMProviderSettings(
        provider="openai",
        model="gpt-4o-mini",
        api_key="sk-test",
    )
    settings = LLMSettings(
        providers=(provider,),
        default_provider="openai",
        request_timeout_seconds=90,
        temperature=0.5,
    )
    return provider, settings


def test_openai_compatible_adapter_uses_shared_factory_not_inline_chatopenai():
    descriptor = _chat_descriptor()
    provider, settings = _settings()
    fake_client = MagicMock(name="ChatOpenAI")

    with patch(
        "app.ai_runtime.runtimes.openai_compatible_chat.build_chat_openai",
        return_value=fake_client,
    ) as build_factory:
        with patch(
            "app.ai_runtime.runtimes.openai_compatible_chat.ainvoke_chat_text",
            new_callable=AsyncMock,
            return_value='{"ok": true}',
        ) as ainvoke:
            with patch("langchain_openai.ChatOpenAI") as inline_ctor:
                model = build_openai_compatible_language_model(
                    descriptor,
                    provider_settings=provider,
                    llm_settings=settings,
                    purpose="unit_test",
                )
                assert isinstance(model, OpenAICompatibleChatLanguageModel)
                output = asyncio.run(model.complete_text("hello", purpose="unit"))

    assert output == '{"ok": true}'
    build_factory.assert_called_once()
    kwargs = build_factory.call_args.kwargs
    assert kwargs["api_key"] == "sk-test"
    assert kwargs["model"] == "gpt-4o-mini"
    assert kwargs["timeout_seconds"] == 90
    assert kwargs["temperature"] == 0.5
    ainvoke.assert_awaited_once()
    inline_ctor.assert_not_called()


def test_fake_language_model_rejects_complete_text():
    descriptor = ModelDescriptor(
        id="fake:fake-deterministic",
        display_name="Fake",
        provider="fake",
        runtime="fake",
        primary_capability=ModelCapability.LANGUAGE_PLANNER,
        locality="local",
        model_version="fake-deterministic",
        supported_operations=creative_chat_operations(),
        status="ready",
        health=ModelHealth(status="ready", credentials_present=True),
        provider_model="fake-deterministic",
    )
    model = build_fake_language_model(descriptor)
    assert model.is_fake is True
    with pytest.raises(ModelUnavailableError) as exc:
        asyncio.run(model.complete_text("x"))
    assert exc.value.code == "model_unavailable"


def test_stub_model_raises_unavailable():
    descriptor = ModelDescriptor(
        id="local:embedding-stub",
        display_name="Embed stub",
        provider="local",
        runtime="stub",
        primary_capability=ModelCapability.EMBEDDING,
        locality="local",
        model_version=None,
        supported_operations=(AiOperation.EMBED,),
        status="unconfigured",
        health=ModelHealth(status="unconfigured", credentials_present=False),
    )
    stub = build_stub_model(descriptor)
    with pytest.raises(ModelUnavailableError) as exc:
        asyncio.run(stub.embed(["a"]))
    assert exc.value.code == "model_unavailable"
