"""Resolve AiOperation + request selection to a registered model."""

from __future__ import annotations

import logging
import os
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Literal, Mapping

from app.llm_settings import LLMProviderSettings, LLMSettings, get_provider_settings, load_llm_settings

from .capabilities import default_capability_for_operation
from .errors import (
    CapabilityMismatchError,
    FallbackNotConfiguredError,
    ModelNotFoundError,
    ModelUnavailableError,
)
from .operations import (
    AiOperation,
    fallback_env_key,
    operation_env_key,
)
from .registry import get_default_model_id, get_model, get_registry, list_models, model_id_for_provider_model, reload_registry
from .types import GenerationParameters, ModelDescriptor, ResolvedModel

logger = logging.getLogger(__name__)

ResolutionPath = Literal["explicit", "legacy", "op_default", "global", "fallback"]

_current_resolved: ContextVar[ResolvedModel | None] = ContextVar("ai_runtime_resolved", default=None)


def get_current_resolved_model() -> ResolvedModel | None:
    return _current_resolved.get()


def set_current_resolved_model(resolved: ResolvedModel | None) -> None:
    _current_resolved.set(resolved)


@dataclass(frozen=True)
class ModelSelectionInput:
    """Normalized selection from request DTOs (model_id and/or legacy provider+model)."""

    model_id: str | None = None
    provider: str | None = None
    model: str | None = None

    @classmethod
    def from_selection(cls, selection: Any | None) -> ModelSelectionInput:
        if selection is None:
            return cls()
        if isinstance(selection, ModelSelectionInput):
            return selection
        model_id = getattr(selection, "model_id", None)
        if isinstance(selection, dict):
            model_id = selection.get("model_id") or model_id
            return cls(
                model_id=_clean(model_id),
                provider=_clean(selection.get("provider")),
                model=_clean(selection.get("model")),
            )
        return cls(
            model_id=_clean(model_id),
            provider=_clean(getattr(selection, "provider", None)),
            model=_clean(getattr(selection, "model", None)),
        )


def resolve_model_for_operation(
    operation: AiOperation,
    selection: ModelSelectionInput | Any | None = None,
    *,
    env: Mapping[str, str] | None = None,
    generation_parameters: GenerationParameters | None = None,
    reload: bool = False,
    collapse_reserved_generate: bool = True,
) -> ResolvedModel:
    """Resolve ``(operation, selection)`` → ``ResolvedModel``.

    Order: explicit ``model_id`` → legacy provider+model → ``AI_OP_*`` → global default.
    Fallback only when ``AI_FALLBACK_<OP>`` is configured and primary is unavailable.

    Set ``collapse_reserved_generate=False`` for hybrid pipelines so
    ``generate_planner`` / ``generate_composer`` keep distinct capability routing.
    """
    source = env if env is not None else os.environ
    if reload or not get_registry(source):
        reload_registry(source)

    # Reserved planner/composer ops inherit generate env when unset (unless hybrid).
    effective_op = _effective_operation(
        operation, source, collapse_reserved_generate=collapse_reserved_generate
    )
    sel = ModelSelectionInput.from_selection(selection)
    requested_id = _requested_model_id(sel)

    logger.debug(
        "Resolving AI model for operation",
        extra={
            "operation": str(operation),
            "effective_operation": str(effective_op),
            "requested_model_id": requested_id,
            "has_legacy": bool(sel.provider or sel.model),
            "collapse_reserved_generate": collapse_reserved_generate,
        },
    )

    try:
        descriptor, path, primary_requested = _resolve_primary(effective_op, sel, source)
        requested_id = requested_id or primary_requested
        _assert_supports_operation(descriptor, effective_op)
        _assert_available(descriptor)
        resolved = ResolvedModel(
            descriptor=descriptor,
            operation=operation,
            resolution_path=path,
            requested_model_id=requested_id,
            resolved_model_id=descriptor.id,
            fallback_applied=False,
            generation_parameters=generation_parameters,
        )
        logger.info(
            "AI model resolved",
            extra={
                "operation": str(operation),
                "resolution_path": path,
                "resolved_model_id": descriptor.id,
                "primary_capability": descriptor.primary_capability,
                "runtime": descriptor.runtime,
                "fallback_applied": False,
            },
        )
        return resolved
    except (ModelNotFoundError, ModelUnavailableError, CapabilityMismatchError) as primary_exc:
        logger.warning(
            "Primary AI model selection unavailable",
            extra={
                "operation": str(operation),
                "requested_model_id": requested_id,
                "error_type": type(primary_exc).__name__,
                "error_code": getattr(primary_exc, "code", None),
            },
        )
        fallback_ids = _fallback_model_ids(effective_op, source)
        if not fallback_ids:
            if isinstance(primary_exc, (ModelUnavailableError, CapabilityMismatchError)):
                raise FallbackNotConfiguredError(
                    f"Model unavailable and no fallback configured for {operation}: {requested_id or 'default'}",
                    code="fallback_not_configured",
                ) from primary_exc
            raise

        # Capture op_default / global attempted id for provenance when selection was empty.
        if requested_id is None:
            try:
                _, _, primary_requested = _resolve_primary(effective_op, sel, source)
                requested_id = primary_requested
            except Exception:  # noqa: BLE001
                pass

        for fallback_id in fallback_ids:
            try:
                descriptor = get_model(fallback_id, env=source)
                _assert_supports_operation(descriptor, effective_op)
                _assert_available(descriptor)
                resolved = ResolvedModel(
                    descriptor=descriptor,
                    operation=operation,
                    resolution_path="fallback",
                    requested_model_id=requested_id,
                    resolved_model_id=descriptor.id,
                    fallback_applied=True,
                    generation_parameters=generation_parameters,
                )
                logger.warning(
                    "AI model fallback applied",
                    extra={
                        "operation": str(operation),
                        "requested_model_id": requested_id,
                        "resolved_model_id": descriptor.id,
                        "fallback_applied": True,
                    },
                )
                return resolved
            except (ModelNotFoundError, ModelUnavailableError, CapabilityMismatchError) as fb_exc:
                logger.warning(
                    "Fallback candidate unavailable",
                    extra={
                        "operation": str(operation),
                        "fallback_model_id": fallback_id,
                        "error_type": type(fb_exc).__name__,
                    },
                )
                continue

        logger.error(
            "AI model fallback chain exhausted",
            extra={"operation": str(operation), "requested_model_id": requested_id},
        )
        raise ModelUnavailableError(
            f"All configured models unavailable for operation {operation}",
            code="model_unavailable",
        ) from primary_exc


def provider_settings_for_resolved(
    resolved: ResolvedModel,
    settings: LLMSettings | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> LLMProviderSettings:
    """Map a resolved chat/fake/local model back to ``LLMProviderSettings`` for existing orchestrators."""
    active = settings or load_llm_settings(env)
    descriptor = resolved.descriptor
    configured: LLMProviderSettings | None = None
    for provider in active.providers:
        if provider.provider == descriptor.provider:
            configured = provider
            break
    if configured is None:
        configured = get_provider_settings(descriptor.provider, env)
    if descriptor.runtime == "execution_node":
        raise ModelUnavailableError(
            "Execution-node models use ainvoke_text_for_resolved; ChatOpenAI credentials are not invented",
            code="model_unavailable",
        )
    if configured is None:
        # Fake or known provider missing from LLM settings after registry mismatch.
        if descriptor.runtime == "fake":
            return LLMProviderSettings(
                provider="fake",
                model=descriptor.provider_model or "fake-deterministic",
                api_key="fake",
                is_default=True,
            )
        if descriptor.runtime == "local_openai_compatible":
            from app.local_llm_settings import load_local_llm_settings

            local = load_local_llm_settings(env)
            if not local.enabled or not local.model:
                raise ModelUnavailableError(
                    f"No local LLM credentials for provider {descriptor.provider}",
                    code="model_unavailable",
                )
            return LLMProviderSettings(
                provider="local",
                model=descriptor.provider_model or local.model,
                api_key=local.api_key,
                base_url=local.base_url,
                is_default=False,
            )
        raise ModelUnavailableError(
            f"No LLM credentials for provider {descriptor.provider}",
            code="model_unavailable",
        )
    model_name = descriptor.provider_model or configured.model
    if model_name != configured.model or (
        descriptor.runtime == "local_openai_compatible" and configured.base_url
    ):
        return LLMProviderSettings(
            provider=configured.provider,
            model=model_name,
            api_key=configured.api_key,
            base_url=configured.base_url,
            is_default=configured.is_default,
        )
    return configured


def resolve_provider_for_operation(
    operation: AiOperation,
    selection: ModelSelectionInput | Any | None = None,
    settings: LLMSettings | None = None,
    *,
    env: Mapping[str, str] | None = None,
    generation_parameters: GenerationParameters | None = None,
    collapse_reserved_generate: bool = True,
) -> tuple[LLMProviderSettings, ResolvedModel]:
    """Resolve operation selection to ``(LLMProviderSettings, ResolvedModel)`` and stash on ContextVar."""
    active = settings or load_llm_settings(env)
    # Always rebuild from env so monkeypatched keys/tests see a fresh catalog.
    reload_registry(env)
    _ensure_settings_models_registered(active, env=env)
    resolved = resolve_model_for_operation(
        operation,
        selection,
        env=env,
        generation_parameters=generation_parameters,
        collapse_reserved_generate=collapse_reserved_generate,
    )
    if resolved.descriptor.runtime == "execution_node":
        provider = LLMProviderSettings(
            provider="execution_node",
            model=resolved.descriptor.provider_model or resolved.resolved_model_id,
            api_key="execution_node",
            is_default=False,
        )
    else:
        provider = provider_settings_for_resolved(resolved, active, env=env)
    set_current_resolved_model(resolved)
    logger.info(
        "Resolved provider for operation",
        extra={
            "operation": str(operation),
            "model_id": resolved.resolved_model_id,
            "runtime": resolved.descriptor.runtime,
            "primary_capability": resolved.descriptor.primary_capability,
            "provider": provider.provider,
            "fallback_applied": resolved.fallback_applied,
        },
    )
    return provider, resolved


def _ensure_settings_models_registered(
    settings: LLMSettings,
    *,
    env: Mapping[str, str] | None = None,
) -> None:
    """Register chat models from an injected ``LLMSettings`` (tests / per-request overrides)."""
    from app.ai_runtime.capabilities import ModelCapability
    from app.ai_runtime.operations import creative_chat_operations
    from app.ai_runtime.registry import get_default_model_id, get_registry, register_model, reload_registry
    from app.ai_runtime.types import ModelDescriptor, ModelHealth
    from app.llm_settings import FAKE_PROVIDER

    if not get_registry(env):
        reload_registry(env)

    creative_ops = creative_chat_operations()
    for provider in settings.providers:
        model_id = f"{provider.provider}:{provider.model}"
        registry = get_registry(env)
        if model_id in registry:
            continue
        if provider.provider == FAKE_PROVIDER:
            runtime = "fake"
            locality = "local"
        elif provider.provider == "local":
            runtime = "local_openai_compatible"
            locality = "local"
        else:
            runtime = "openai_compatible_chat"
            locality = "remote"
        register_model(
            ModelDescriptor(
                id=model_id,
                display_name=(
                    f"Local ({provider.model})"
                    if provider.provider == "local"
                    else f"{provider.provider}:{provider.model}"
                ),
                provider=provider.provider,
                runtime=runtime,  # type: ignore[arg-type]
                primary_capability=ModelCapability.LANGUAGE_PLANNER,
                locality=locality,  # type: ignore[arg-type]
                model_version=provider.model,
                supported_operations=creative_ops,
                status="ready",
                health=ModelHealth(status="ready", credentials_present=True),
                provider_model=provider.model,
                limits={},
            ),
            overwrite=False,
        )
        logger.debug(
            "Registered injected LLM settings model into AI registry",
            extra={"model_id": model_id, "runtime": runtime},
        )

    # If registry has no default but settings does, prefer settings default for global path.
    if get_default_model_id(env) is None and settings.default_provider:
        from app.ai_runtime.registry import set_default_model_id

        for provider in settings.providers:
            if provider.is_default or provider.provider == settings.default_provider:
                set_default_model_id(f"{provider.provider}:{provider.model}")
                break


def is_fake_resolved(resolved: ResolvedModel) -> bool:
    return resolved.descriptor.runtime == "fake" or resolved.descriptor.id.startswith("fake:")


def resolution_public_fields(resolved: ResolvedModel | None) -> dict[str, Any]:
    """Additive response fields for operation endpoints (ids only)."""
    if resolved is None:
        return {}
    return {
        "model_id": resolved.resolved_model_id,
        "requested_model_id": resolved.requested_model_id,
        "resolved_model_id": resolved.resolved_model_id,
        "fallback_applied": resolved.fallback_applied,
        "runtime": resolved.descriptor.runtime,
        "capability": str(resolved.descriptor.primary_capability),
        "operation": str(resolved.operation),
        "model_version": resolved.descriptor.model_version,
    }


def default_operation_routes(env: Mapping[str, str] | None = None) -> dict[str, str | None]:
    """Non-secret map of operation → configured default model id (for /ready)."""
    source = env if env is not None else os.environ
    routes: dict[str, str | None] = {}
    for operation in AiOperation:
        key = operation_env_key(operation)
        raw = (source.get(key) or "").strip() or None
        if raw is None and operation == AiOperation.GENERATE_PLANNER:
            # Discovery still shows planner falling back to generate env when unset.
            raw = (source.get(operation_env_key(AiOperation.GENERATE)) or "").strip() or None
        routes[str(operation)] = raw

    # Implicit default for embed when AI_OP_EMBED unset: ready symbolic features model.
    if not routes.get("embed"):
        from app.embeddings.settings import EMBEDDING_DEFAULT_MODEL_ID

        try:
            descriptor = get_model(EMBEDDING_DEFAULT_MODEL_ID, env=source)
            if descriptor.status == "ready" and AiOperation.EMBED in descriptor.supported_operations:
                routes["embed"] = EMBEDDING_DEFAULT_MODEL_ID
                logger.debug(
                    "Default embed route set to symbolic features model",
                    extra={"model_id": EMBEDDING_DEFAULT_MODEL_ID},
                )
        except ModelNotFoundError:
            logger.warning(
                "No implicit embed default; symbolic features model not registered",
                extra={"expected_model_id": EMBEDDING_DEFAULT_MODEL_ID},
            )

    # Implicit default for generate_composer: first ready symbolic_composer model.
    if not routes.get("generate_composer"):
        ready_composers = [
            model
            for model in list_models(
                operation=AiOperation.GENERATE_COMPOSER,
                status="ready",
                env=source,
            )
            if model.runtime not in {"plugin", "personal_composer"}
        ]
        if ready_composers:
            routes["generate_composer"] = ready_composers[0].id
            logger.debug(
                "Default generate_composer route set to ready symbolic composer",
                extra={"model_id": ready_composers[0].id},
            )
    return routes


def _effective_operation(
    operation: AiOperation,
    env: Mapping[str, str],
    *,
    collapse_reserved_generate: bool = True,
) -> AiOperation:
    """Collapse reserved planner/composer ops to GENERATE unless explicitly disabled.

    Hybrid pipelines pass ``collapse_reserved_generate=False`` so GENERATE_PLANNER /
    GENERATE_COMPOSER keep distinct capability routing.
    """
    if (
        collapse_reserved_generate
        and operation in (AiOperation.GENERATE_PLANNER, AiOperation.GENERATE_COMPOSER)
    ):
        if not (env.get(operation_env_key(operation)) or "").strip():
            return AiOperation.GENERATE
    return operation


def _requested_model_id(sel: ModelSelectionInput) -> str | None:
    if sel.model_id:
        return sel.model_id
    if sel.provider and sel.model:
        return model_id_for_provider_model(sel.provider, sel.model)
    return None


def _resolve_primary(
    operation: AiOperation,
    sel: ModelSelectionInput,
    env: Mapping[str, str],
) -> tuple[ModelDescriptor, ResolutionPath, str | None]:
    if sel.model_id:
        logger.info("Resolution path: explicit model_id", extra={"model_id": sel.model_id})
        return get_model(sel.model_id, env=env), "explicit", sel.model_id

    if sel.provider:
        model_name = sel.model
        if model_name:
            mid = model_id_for_provider_model(sel.provider, model_name)
            try:
                descriptor = get_model(mid, env=env)
                logger.info("Resolution path: legacy provider+model", extra={"model_id": mid})
                return descriptor, "legacy", mid
            except ModelNotFoundError:
                # Allow legacy override of model string for a registered provider slot.
                matches = [
                    m
                    for m in list_models(env=env)
                    if m.provider == sel.provider.strip().lower() and m.runtime != "stub"
                ]
                if matches:
                    base = matches[0]
                    # Registered provider exists; treat as legacy with overridden model name.
                    overridden = ModelDescriptor(
                        id=mid,
                        display_name=f"{base.provider}:{model_name}",
                        provider=base.provider,
                        runtime=base.runtime,
                        primary_capability=base.primary_capability,
                        locality=base.locality,
                        model_version=model_name,
                        supported_operations=base.supported_operations,
                        status=base.status,
                        health=base.health,
                        secondary_capabilities=base.secondary_capabilities,
                        limits=dict(base.limits),
                        provider_model=model_name,
                    )
                    logger.info(
                        "Resolution path: legacy provider with model override",
                        extra={"model_id": mid, "provider": base.provider},
                    )
                    return overridden, "legacy", mid
                raise
        matches = [
            m
            for m in list_models(env=env)
            if m.provider == sel.provider.strip().lower() and m.runtime != "stub"
        ]
        if matches:
            logger.info(
                "Resolution path: legacy provider default model",
                extra={"model_id": matches[0].id},
            )
            return matches[0], "legacy", matches[0].id
        raise ModelNotFoundError(
            f"No registered model for provider {sel.provider}",
            code="model_not_found",
        )

    op_default = (env.get(operation_env_key(operation)) or "").strip()
    if op_default:
        logger.info("Resolution path: op_default", extra={"model_id": op_default, "operation": str(operation)})
        return get_model(op_default, env=env), "op_default", op_default

    default_id = get_default_model_id(env)
    if default_id:
        try:
            descriptor = get_model(default_id, env=env)
            if operation in descriptor.supported_operations:
                logger.info("Resolution path: global default", extra={"model_id": default_id})
                return descriptor, "global", default_id
            logger.debug(
                "Global default does not support operation; continuing search",
                extra={
                    "model_id": default_id,
                    "operation": str(operation),
                    "supported_operations": [str(op) for op in descriptor.supported_operations],
                },
            )
        except ModelNotFoundError:
            logger.debug(
                "Global default model id not registered",
                extra={"model_id": default_id, "operation": str(operation)},
            )

    # Prefer any ready model that supports this operation (e.g. symbolic embedder).
    ready = list_models(operation=operation, status="ready", env=env)
    if ready:
        logger.info("Resolution path: global first ready", extra={"model_id": ready[0].id})
        return ready[0], "global", ready[0].id

    raise ModelUnavailableError(
        f"No models available for operation {operation}",
        code="model_unavailable",
    )


def _assert_supports_operation(descriptor: ModelDescriptor, operation: AiOperation) -> None:
    if operation not in descriptor.supported_operations:
        expected = default_capability_for_operation(operation)
        logger.error(
            "Capability/operation mismatch",
            extra={
                "model_id": descriptor.id,
                "operation": str(operation),
                "primary_capability": descriptor.primary_capability,
                "expected_capability": expected,
            },
        )
        raise CapabilityMismatchError(
            f"Model {descriptor.id} does not support operation {operation}",
            code="capability_mismatch",
        )


def _assert_available(descriptor: ModelDescriptor) -> None:
    if descriptor.runtime == "stub":
        raise ModelUnavailableError(
            f"Model unavailable: {descriptor.id}",
            code="model_unavailable",
        )
    if descriptor.status in {
        "unconfigured",
        "unavailable",
        "loading",
        "out_of_memory",
        "unsupported_device",
    }:
        raise ModelUnavailableError(
            f"Model unavailable: {descriptor.id}",
            code="model_unavailable",
        )


def _fallback_model_ids(operation: AiOperation, env: Mapping[str, str]) -> list[str]:
    raw = (env.get(fallback_env_key(operation)) or "").strip()
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
