"""Build the AI model registry from LLM env (+ optional registry file)."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Mapping

from app.llm_settings import FAKE_PROVIDER, load_llm_settings
from app.local_llm_settings import LOCAL_PROVIDER, LOCAL_RUNTIME_ID, load_local_llm_settings

from .capabilities import ModelCapability
from .local_health import health_from_probe, probe_local_llm_health, public_limits
from .operations import AiOperation, creative_chat_operations
from .types import ModelDescriptor, ModelHealth

logger = logging.getLogger(__name__)

REGISTRY_PATH_ENV = "AI_MODEL_REGISTRY_PATH"

_STUB_MODELS: tuple[tuple[str, str, ModelCapability, AiOperation], ...] = (
    ("local:embedding-stub", "Embedding (unconfigured stub)", ModelCapability.EMBEDDING, AiOperation.EMBED),
    (
        "local:transcription-stub",
        "Transcription (unconfigured stub)",
        ModelCapability.AUDIO_TRANSCRIPTION,
        AiOperation.TRANSCRIBE,
    ),
    (
        "local:audio-generation-stub",
        "Audio generation (unconfigured stub)",
        ModelCapability.AUDIO_GENERATION,
        AiOperation.AUDIO_RENDER,
    ),
)


def build_registry_from_env(
    env: Mapping[str, str] | None = None,
) -> tuple[dict[str, ModelDescriptor], str | None]:
    """Return ``{model_id: descriptor}`` and the default model id."""
    source = env if env is not None else os.environ
    settings = load_llm_settings(source)
    models: dict[str, ModelDescriptor] = {}
    default_model_id: str | None = None
    creative_ops = creative_chat_operations()

    for provider in settings.providers:
        model_id = f"{provider.provider}:{provider.model}"
        if provider.provider == FAKE_PROVIDER:
            runtime = "fake"
            locality = "local"
            display = f"Fake ({provider.model})"
            version = provider.model
        elif provider.provider == LOCAL_PROVIDER:
            # Registered below with a live health probe (avoids duplicate ids).
            continue
        else:
            runtime = "openai_compatible_chat"
            locality = "remote"
            display = f"{provider.provider}:{provider.model}"
            version = provider.model

        if model_id in models:
            logger.warning("Duplicate model id while bootstrapping LLM env", extra={"model_id": model_id})
            continue

        descriptor = ModelDescriptor(
            id=model_id,
            display_name=display,
            provider=provider.provider,
            runtime=runtime,  # type: ignore[arg-type]
            primary_capability=ModelCapability.LANGUAGE_PLANNER,
            locality=locality,  # type: ignore[arg-type]
            model_version=version,
            supported_operations=creative_ops,
            status="ready",
            health=ModelHealth(
                status="ready",
                detail="credentials_present" if provider.provider != FAKE_PROVIDER else "fake_mode",
                credentials_present=True,
            ),
            provider_model=provider.model,
        )
        models[model_id] = descriptor
        logger.info(
            "Bootstrapped chat model from LLM env",
            extra={
                "model_id": model_id,
                "primary_capability": ModelCapability.LANGUAGE_PLANNER,
                "locality": locality,
                "is_default": provider.is_default,
            },
        )
        if provider.is_default:
            default_model_id = model_id

    local_settings = load_local_llm_settings(source)
    if local_settings.enabled and local_settings.model:
        model_id = local_settings.model_id
        assert model_id is not None
        if model_id in models:
            logger.warning("Duplicate local model id while bootstrapping", extra={"model_id": model_id})
        else:
            probe = probe_local_llm_health(local_settings)
            health = health_from_probe(probe)
            models[model_id] = ModelDescriptor(
                id=model_id,
                display_name=local_settings.display_name,
                provider=LOCAL_PROVIDER,
                runtime=LOCAL_RUNTIME_ID,  # type: ignore[arg-type]
                primary_capability=ModelCapability.LANGUAGE_PLANNER,
                locality="local",
                model_version=local_settings.model,
                supported_operations=creative_ops,
                status=probe.status,
                health=health,
                limits=public_limits(local_settings),
                provider_model=local_settings.model,
            )
            logger.info(
                "Bootstrapped local OpenAI-compatible chat model",
                extra={
                    "model_id": model_id,
                    "runtime": LOCAL_RUNTIME_ID,
                    "status": probe.status,
                    "detail": probe.detail,
                    "locality": "local",
                },
            )
            if settings.default_provider == LOCAL_PROVIDER:
                default_model_id = model_id

    for stub_id, display_name, capability, operation in _STUB_MODELS:
        if stub_id in models:
            logger.warning("Stub model id collides with env entry", extra={"model_id": stub_id})
            continue
        models[stub_id] = ModelDescriptor(
            id=stub_id,
            display_name=display_name,
            provider="local",
            runtime="stub",
            primary_capability=capability,
            locality="local",
            model_version=None,
            supported_operations=(operation,),
            status="unconfigured",
            health=ModelHealth(
                status="unconfigured",
                detail="stub_not_configured",
                credentials_present=False,
            ),
        )
        logger.debug(
            "Registered unconfigured stub model",
            extra={"model_id": stub_id, "primary_capability": capability},
        )

    extra_path = (source.get(REGISTRY_PATH_ENV) or "").strip()
    if extra_path:
        _merge_registry_file(models, Path(extra_path))
    else:
        logger.debug("AI_MODEL_REGISTRY_PATH unset; env-only registry")

    if default_model_id is None and settings.default_provider:
        for mid, desc in models.items():
            if desc.provider == settings.default_provider and desc.runtime != "stub":
                default_model_id = mid
                break

    logger.info(
        "AI registry bootstrap complete",
        extra={
            "model_ids": sorted(models.keys()),
            "default_model_id": default_model_id,
            "provider_count": len(settings.providers),
        },
    )
    return models, default_model_id


def _merge_registry_file(models: dict[str, ModelDescriptor], path: Path) -> None:
    if not path.is_file():
        logger.warning(
            "AI_MODEL_REGISTRY_PATH not found; skipping",
            extra={"path_basename": path.name},
        )
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "Failed to load AI model registry file",
            extra={"path_basename": path.name, "error_type": type(exc).__name__},
        )
        return

    entries = raw.get("models") if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        logger.warning("AI registry file must be a list or {models: [...]}", extra={"path_basename": path.name})
        return

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            descriptor = _descriptor_from_file_entry(entry)
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning(
                "Skipping invalid registry file entry",
                extra={"error_type": type(exc).__name__},
            )
            continue
        if descriptor.id in models:
            logger.warning("Duplicate id from registry file ignored", extra={"model_id": descriptor.id})
            continue
        models[descriptor.id] = descriptor
        logger.info(
            "Loaded model from registry file",
            extra={
                "model_id": descriptor.id,
                "primary_capability": descriptor.primary_capability,
                "locality": descriptor.locality,
            },
        )


def _descriptor_from_file_entry(entry: dict[str, Any]) -> ModelDescriptor:
    model_id = str(entry["id"]).strip()
    if ":" not in model_id:
        raise ValueError("model id must be provider:model")
    provider, _, provider_model = model_id.partition(":")
    ops_raw = entry.get("supported_operations") or []
    operations = tuple(AiOperation(op) for op in ops_raw)
    capability = ModelCapability(entry.get("primary_capability", "language_planner"))
    status = entry.get("status", "unconfigured")
    runtime = entry.get("runtime", "stub")
    locality = entry.get("locality", "local")
    secondary = tuple(
        ModelCapability(c) for c in (entry.get("secondary_capabilities") or [])
    )
    return ModelDescriptor(
        id=model_id,
        display_name=str(entry.get("display_name") or model_id),
        provider=str(entry.get("provider") or provider),
        runtime=runtime,
        primary_capability=capability,
        locality=locality,
        model_version=entry.get("model_version"),
        supported_operations=operations,
        status=status,
        health=ModelHealth(
            status=status,
            detail=entry.get("health_detail"),
            credentials_present=bool(entry.get("credentials_present", False)),
        ),
        secondary_capabilities=secondary,
        limits=dict(entry.get("limits") or {}),
        provider_model=str(entry.get("provider_model") or provider_model or None),
    )
