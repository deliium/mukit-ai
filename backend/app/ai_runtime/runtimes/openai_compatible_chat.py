"""OpenAI-compatible chat LanguageModel — must use shared llm_chat_client factory."""

from __future__ import annotations

import logging
import time
from typing import Any

from app.llm_settings import LLMProviderSettings, LLMSettings
from app.services.llm_chat_client import ainvoke_chat_text, build_chat_openai

from ..errors import ModelUnavailableError
from ..types import ModelDescriptor

logger = logging.getLogger(__name__)


class OpenAICompatibleChatLanguageModel:
    """LanguageModel backed by LangChain ChatOpenAI via ``build_chat_openai`` only."""

    def __init__(
        self,
        descriptor: ModelDescriptor,
        *,
        provider_settings: LLMProviderSettings,
        llm_settings: LLMSettings,
        temperature: float | None = None,
        timeout_seconds: int | None = None,
        purpose: str | None = None,
    ) -> None:
        if descriptor.runtime != "openai_compatible_chat":
            raise ModelUnavailableError(
                f"Runtime mismatch for {descriptor.id}: expected openai_compatible_chat",
                code="model_unavailable",
            )
        if not provider_settings.api_key:
            raise ModelUnavailableError(
                f"Model unavailable (no credentials): {descriptor.id}",
                code="model_unavailable",
            )

        self._descriptor = descriptor
        self._provider_settings = provider_settings
        self._temperature = (
            temperature if temperature is not None else llm_settings.temperature
        )
        self._timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else llm_settings.request_timeout_seconds
        )
        self._purpose = purpose or "ai_runtime_chat"
        self._client: Any | None = None

        logger.info(
            "Constructed openai_compatible_chat LanguageModel",
            extra={
                "model_id": descriptor.id,
                "runtime": descriptor.runtime,
                "timeout_seconds": self._timeout_seconds,
                "purpose": self._purpose,
            },
        )

    @property
    def model_id(self) -> str:
        return self._descriptor.id

    @property
    def descriptor(self) -> ModelDescriptor:
        return self._descriptor

    def _ensure_client(self) -> Any:
        if self._client is None:
            model_name = self._descriptor.provider_model or self._provider_settings.model
            self._client = build_chat_openai(
                api_key=self._provider_settings.api_key,
                model=model_name,
                temperature=self._temperature,
                timeout_seconds=self._timeout_seconds,
                base_url=self._provider_settings.base_url,
                purpose=self._purpose,
            )
        return self._client

    async def complete_text(self, prompt: str, *, purpose: str | None = None) -> str:
        call_purpose = purpose or self._purpose
        started = time.monotonic()
        logger.debug(
            "LanguageModel complete_text start",
            extra={
                "model_id": self.model_id,
                "purpose": call_purpose,
                "prompt_length": len(prompt or ""),
            },
        )
        try:
            client = self._ensure_client()
            result = await ainvoke_chat_text(
                client,
                prompt,
                purpose=call_purpose,
                model_id=self.model_id,
                runtime=self.descriptor.runtime,
            )
        except Exception as exc:
            logger.warning(
                "LanguageModel transport failure",
                extra={
                    "model_id": self.model_id,
                    "purpose": call_purpose,
                    "error_type": type(exc).__name__,
                },
            )
            raise
        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.debug(
            "LanguageModel complete_text done",
            extra={
                "model_id": self.model_id,
                "purpose": call_purpose,
                "response_length": len(result or ""),
                "elapsed_ms": elapsed_ms,
            },
        )
        return result


def build_openai_compatible_language_model(
    descriptor: ModelDescriptor,
    *,
    provider_settings: LLMProviderSettings,
    llm_settings: LLMSettings,
    temperature: float | None = None,
    timeout_seconds: int | None = None,
    purpose: str | None = None,
) -> OpenAICompatibleChatLanguageModel:
    return OpenAICompatibleChatLanguageModel(
        descriptor,
        provider_settings=provider_settings,
        llm_settings=llm_settings,
        temperature=temperature,
        timeout_seconds=timeout_seconds,
        purpose=purpose,
    )
