"""Tests for LocalLanguageModel adapter (mocked shared chat factory)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ai_runtime.capabilities import ModelCapability
from app.ai_runtime.errors import ModelUnavailableError
from app.ai_runtime.operations import creative_chat_operations
from app.ai_runtime.runtimes.local_language import (
    LocalLanguageModel,
    build_local_language_model,
)
from app.ai_runtime.types import ModelDescriptor, ModelHealth
from app.llm_settings import LLMProviderSettings, LLMSettings


def _local_descriptor() -> ModelDescriptor:
    return ModelDescriptor(
        id="local:instruct-q4",
        display_name="Local (instruct-q4)",
        provider="local",
        runtime="local_openai_compatible",
        primary_capability=ModelCapability.LANGUAGE_PLANNER,
        locality="local",
        model_version="instruct-q4",
        supported_operations=creative_chat_operations(),
        status="ready",
        health=ModelHealth(status="ready", detail="loaded", credentials_present=True),
        provider_model="instruct-q4",
        limits={"context_size": 4096, "max_concurrency": 1},
    )


def _settings() -> tuple[LLMProviderSettings, LLMSettings]:
    provider = LLMProviderSettings(
        provider="local",
        model="instruct-q4",
        api_key="local",
        base_url="http://local-llm:8080/v1",
    )
    settings = LLMSettings(
        providers=(provider,),
        default_provider="local",
        request_timeout_seconds=180,
        temperature=0.7,
    )
    return provider, settings


def test_local_language_model_uses_shared_factory_not_engine_imports():
    descriptor = _local_descriptor()
    provider, settings = _settings()
    fake_client = MagicMock(name="ChatOpenAI")

    with patch(
        "app.ai_runtime.runtimes.local_language.build_chat_openai",
        return_value=fake_client,
    ) as build_factory:
        with patch(
            "app.ai_runtime.runtimes.local_language.ainvoke_chat_text",
            new_callable=AsyncMock,
            return_value='{"ok": true}',
        ) as ainvoke:
            with patch("langchain_openai.ChatOpenAI") as inline_ctor:
                model = build_local_language_model(
                    descriptor,
                    provider_settings=provider,
                    llm_settings=settings,
                    purpose="unit_test",
                )
                assert isinstance(model, LocalLanguageModel)
                output = asyncio.run(model.complete_text("hello", purpose="unit"))

    assert output == '{"ok": true}'
    build_factory.assert_called_once()
    kwargs = build_factory.call_args.kwargs
    assert kwargs["api_key"] == "local"
    assert kwargs["model"] == "instruct-q4"
    assert kwargs["base_url"] == "http://local-llm:8080/v1"
    assert kwargs["timeout_seconds"] == 180
    ainvoke.assert_awaited_once()
    inline_ctor.assert_not_called()


def test_local_language_model_rejects_wrong_runtime():
    descriptor = _local_descriptor()
    bad = ModelDescriptor(
        id=descriptor.id,
        display_name=descriptor.display_name,
        provider=descriptor.provider,
        runtime="openai_compatible_chat",
        primary_capability=descriptor.primary_capability,
        locality="local",
        model_version=descriptor.model_version,
        supported_operations=descriptor.supported_operations,
        status="ready",
        health=descriptor.health,
        provider_model=descriptor.provider_model,
    )
    provider, settings = _settings()
    with pytest.raises(ModelUnavailableError):
        build_local_language_model(bad, provider_settings=provider, llm_settings=settings)


def test_local_language_model_maps_transport_failure():
    descriptor = _local_descriptor()
    provider, settings = _settings()
    with patch(
        "app.ai_runtime.runtimes.local_language.build_chat_openai",
        return_value=MagicMock(),
    ):
        with patch(
            "app.ai_runtime.runtimes.local_language.ainvoke_chat_text",
            new_callable=AsyncMock,
            side_effect=RuntimeError("connection refused"),
        ):
            model = build_local_language_model(
                descriptor,
                provider_settings=provider,
                llm_settings=settings,
            )
            with pytest.raises(ModelUnavailableError) as exc_info:
                asyncio.run(model.complete_text("x"))
    assert exc_info.value.code == "model_unavailable"
