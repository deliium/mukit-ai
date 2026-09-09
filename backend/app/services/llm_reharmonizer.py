"""AI-assisted reharmonization orchestration with deterministic validation.

Provider proposals never bypass authorization, preservation, or compatibility
checks — every candidate is realized through the deterministic preview path or
an equivalent validated apply.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from app.harmony_schemas import ReharmonizePreviewRequest, ReharmonizePreviewResponse
from app.llm_settings import LLMProviderSettings, LLMSettings, load_llm_settings
from app.services.composition_reharmonization import preview_reharmonization
from app.services.llm_music_generator import (
    InvalidLLMOutputError,
    NoLLMProviderConfiguredError,
    UnsupportedLLMProviderError,
)


logger = logging.getLogger(__name__)

MAX_REHARMONIZE_REPAIR_ATTEMPTS = 1


async def run_reharmonize_preview(
    request: ReharmonizePreviewRequest,
    settings: LLMSettings | None = None,
) -> ReharmonizePreviewResponse:
    """Preview reharmonization using deterministic and optional AI engines."""
    if request.engine == "deterministic":
        return preview_reharmonization(request)

    active = settings or load_llm_settings()
    provider = _select_reharmonize_provider(request, active)
    model = request.selection_options.model or provider.model

    from app.services.fake_llm import FakeLLMError, draft_fake_reharmonization, is_fake_provider

    if is_fake_provider(provider):
        logger.info(
            "Routing reharmonization to fake LLM provider",
            extra={
                "provider": provider.provider,
                "model": model,
                "operation": request.operation,
                "content_policy": request.content_policy,
            },
        )
        try:
            return await draft_fake_reharmonization(request, provider)
        except FakeLLMError as exc:
            raise InvalidLLMOutputError(str(exc)) from exc

    logger.info(
        "AI reharmonization started",
        extra={
            "provider": provider.provider,
            "model": model,
            "operation": request.operation,
            "content_policy": request.content_policy,
            "start_bar": request.selection.start_bar,
            "end_bar": request.selection.end_bar,
            "target_count": len(request.target_track_ids),
            "instruction_meta": _instruction_meta(request.instruction),
        },
    )

    # Real providers: bounded draft → validate via deterministic engine fallback.
    # Until a full structured graph lands, repair falls back to deterministic
    # realization of the same operation/policy (never trusts raw provider JSON).
    attempt = 0
    last_error: Exception | None = None
    while attempt <= MAX_REHARMONIZE_REPAIR_ATTEMPTS:
        try:
            response = preview_reharmonization(
                request.model_copy(update={"engine": "deterministic"})
            )
            stamped = response.model_copy(
                update={
                    "provider": provider.provider,
                    "model": model,
                    "warnings": [
                        *response.warnings,
                        "ai_engine_used_deterministic_realization",
                    ][:32],
                }
            )
            logger.info(
                "AI reharmonization completed",
                extra={
                    "provider": provider.provider,
                    "model": model,
                    "attempt": attempt,
                    "compatibility_status": stamped.compatibility.status,
                    "changed_span_count": len(stamped.harmony_changes),
                },
            )
            return stamped
        except Exception as exc:  # noqa: BLE001 — mapped below
            last_error = exc
            attempt += 1
            logger.warning(
                "AI reharmonization repair attempt",
                extra={
                    "attempt": attempt,
                    "error_type": type(exc).__name__,
                    "code": getattr(exc, "code", None),
                },
            )

    if last_error is not None:
        raise InvalidLLMOutputError(
            f"AI reharmonization failed after repairs: {type(last_error).__name__}"
        ) from last_error
    raise InvalidLLMOutputError("AI reharmonization failed without a candidate")


def _select_reharmonize_provider(
    request: ReharmonizePreviewRequest,
    settings: LLMSettings,
) -> LLMProviderSettings:
    if not settings.providers:
        raise NoLLMProviderConfiguredError("No LLM providers are configured")
    requested = (request.selection_options.provider or settings.default_provider or "").strip()
    requested_model = request.selection_options.model
    for provider in settings.providers:
        if provider.provider == requested:
            if requested_model and requested_model != provider.model:
                return LLMProviderSettings(
                    provider=provider.provider,
                    model=requested_model,
                    api_key=provider.api_key,
                    base_url=provider.base_url,
                    is_default=provider.is_default,
                )
            return provider
    raise UnsupportedLLMProviderError(f"Unsupported or unavailable LLM provider: {requested}")


def _instruction_meta(instruction: str | None) -> dict[str, Any]:
    text = (instruction or "").strip()
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12] if text else None
    intent = None
    lowered = text.lower()
    if "tens" in lowered:
        intent = "increase_tension"
    elif "simpl" in lowered or "less tens" in lowered:
        intent = "decrease_tension"
    elif "cadence" in lowered:
        intent = "strengthen_cadence"
    return {"length": len(text), "hash_prefix": digest, "recognized_intent": intent}
