"""Unified text completion seam for resolved models.

Generate/edit call this helper so ``runtime=execution_node`` never builds
ChatOpenAI against the typed worker surface.
"""

from __future__ import annotations

import logging

from app.ai_runtime.errors import ModelUnavailableError
from app.ai_runtime.runtimes.execution_node import (
    EXECUTION_NODE_RUNTIME,
    ExecutionNodeLanguageModel,
)
from app.ai_runtime.types import ResolvedModel
from app.llm_settings import LLMProviderSettings, load_llm_settings
from app.services.llm_chat_client import ainvoke_chat_text, build_chat_openai

logger = logging.getLogger(__name__)


async def ainvoke_text_for_resolved(
    resolved: ResolvedModel,
    prompt: str,
    *,
    purpose: str,
    provider: LLMProviderSettings | None = None,
    temperature: float | None = None,
    timeout_seconds: int | None = None,
) -> str:
    """Return completion text for ``resolved`` without logging the prompt body."""
    runtime = resolved.descriptor.runtime
    logger.debug(
        "ainvoke_text_for_resolved branch",
        extra={
            "runtime": runtime,
            "model_id": resolved.resolved_model_id,
            "purpose": purpose,
            "prompt_length": len(prompt or ""),
        },
    )
    if runtime == EXECUTION_NODE_RUNTIME:
        model = ExecutionNodeLanguageModel(resolved.descriptor)
        return await model.complete_text(prompt, purpose=purpose)

    settings = load_llm_settings()
    active_provider = provider
    if active_provider is None:
        from app.ai_runtime.routing import provider_settings_for_resolved

        active_provider = provider_settings_for_resolved(resolved, settings)

    temp = temperature if temperature is not None else settings.temperature
    timeout = (
        timeout_seconds
        if timeout_seconds is not None
        else settings.request_timeout_seconds
    )
    model_name = resolved.descriptor.provider_model or active_provider.model
    try:
        client = build_chat_openai(
            api_key=active_provider.api_key,
            base_url=active_provider.base_url,
            model=model_name,
            temperature=temp,
            timeout_seconds=timeout,
            purpose=purpose,
        )
    except ImportError as exc:
        raise ModelUnavailableError(
            "LangChain OpenAI dependencies are not installed",
            code="model_unavailable",
        ) from exc
    return await ainvoke_chat_text(
        client,
        prompt,
        purpose=purpose,
        model_id=resolved.resolved_model_id,
        runtime=str(runtime),
    )
