"""In-memory AI model registry with reload support for tests and config changes."""

from __future__ import annotations

import logging
import threading
from typing import Mapping

from .capabilities import ModelCapability
from .errors import ModelNotFoundError
from .operations import AiOperation
from .types import ModelDescriptor, ModelStatus

logger = logging.getLogger(__name__)

_lock = threading.RLock()
_registry: dict[str, ModelDescriptor] = {}
_default_model_id: str | None = None


def reload_registry(
    env: Mapping[str, str] | None = None,
    *,
    descriptors: tuple[ModelDescriptor, ...] | None = None,
    default_model_id: str | None = None,
) -> None:
    """Replace the process registry from env bootstrap or an explicit descriptor set.

    Prefer calling this (or ``get_registry`` which bootstraps lazily) after
    pytest ``monkeypatch`` env changes so tests do not see a stale catalog.
    """
    from .bootstrap import build_registry_from_env

    with _lock:
        if descriptors is not None:
            models = {d.id: d for d in descriptors}
            default_id = default_model_id
        else:
            models, default_id = build_registry_from_env(env)
            if default_model_id is not None:
                default_id = default_model_id

        global _registry, _default_model_id
        _registry = dict(models)
        _default_model_id = default_id
        if descriptors is None:
            _attach_plugin_descriptors()
        logger.info(
            "AI model registry reloaded",
            extra={
                "model_count": len(_registry),
                "model_ids": sorted(_registry.keys()),
                "default_model_id": _default_model_id,
            },
        )
        logger.debug(
            "AI model registry reload detail",
            extra={
                "by_capability": _counts_by_capability(_registry),
                "by_status": _counts_by_status(_registry),
            },
        )


def _ensure_loaded(env: Mapping[str, str] | None = None) -> None:
    with _lock:
        if not _registry and env is not None:
            reload_registry(env)
        elif not _registry:
            reload_registry(None)


def get_registry(env: Mapping[str, str] | None = None) -> dict[str, ModelDescriptor]:
    """Return a copy of the current registry (bootstraps from env if empty)."""
    _ensure_loaded(env)
    with _lock:
        return dict(_registry)


def get_default_model_id(env: Mapping[str, str] | None = None) -> str | None:
    _ensure_loaded(env)
    with _lock:
        return _default_model_id


def set_default_model_id(model_id: str | None) -> None:
    with _lock:
        global _default_model_id
        _default_model_id = model_id
        logger.debug("AI registry default model id set", extra={"default_model_id": model_id})


def register_model(descriptor: ModelDescriptor, *, overwrite: bool = False) -> None:
    with _lock:
        if descriptor.id in _registry and not overwrite:
            logger.warning(
                "Duplicate AI model id registration ignored",
                extra={"model_id": descriptor.id},
            )
            return
        if descriptor.id in _registry and overwrite:
            logger.warning(
                "Overwriting AI model registry entry",
                extra={"model_id": descriptor.id},
            )
        _registry[descriptor.id] = descriptor
        logger.info(
            "Registered AI model",
            extra={
                "model_id": descriptor.id,
                "primary_capability": descriptor.primary_capability,
                "locality": descriptor.locality,
                "runtime": descriptor.runtime,
                "status": descriptor.status,
            },
        )


def get_model(model_id: str, *, env: Mapping[str, str] | None = None) -> ModelDescriptor:
    _ensure_loaded(env)
    with _lock:
        descriptor = _registry.get(model_id)
    if descriptor is None:
        logger.warning("AI model not found", extra={"model_id": model_id})
        raise ModelNotFoundError(f"Model not found: {model_id}", code="model_not_found")
    return descriptor


def list_models(
    *,
    capability: ModelCapability | str | None = None,
    operation: AiOperation | str | None = None,
    status: ModelStatus | None = None,
    env: Mapping[str, str] | None = None,
) -> list[ModelDescriptor]:
    _ensure_loaded(env)
    with _lock:
        items = list(_registry.values())

    if capability is not None:
        cap = ModelCapability(capability) if isinstance(capability, str) else capability
        items = [
            m
            for m in items
            if m.primary_capability == cap or cap in m.secondary_capabilities
        ]
    if operation is not None:
        op = AiOperation(operation) if isinstance(operation, str) else operation
        items = [m for m in items if op in m.supported_operations]
    if status is not None:
        items = [m for m in items if m.status == status]

    items.sort(key=lambda m: m.id)
    logger.debug(
        "Listed AI models",
        extra={
            "count": len(items),
            "capability": str(capability) if capability else None,
            "operation": str(operation) if operation else None,
            "status": status,
        },
    )
    return items


def model_id_for_provider_model(provider: str, model: str) -> str:
    return f"{provider.strip().lower()}:{model.strip()}"


def clear_registry_for_tests() -> None:
    """Test helper: empty the process registry without bootstrapping."""
    global _registry, _default_model_id, _last_plugin_descriptor_count
    with _lock:
        _registry = {}
        _default_model_id = None
        _last_plugin_descriptor_count = None
        logger.debug("AI model registry cleared for tests")


_last_plugin_descriptor_count: int | None = None


def _attach_plugin_descriptors() -> None:
    """Reattach already-loaded plugin descriptors. Does not call plugin ``register()``."""
    global _last_plugin_descriptor_count
    try:
        from app.plugin_host.bridge import plugin_model_descriptors

        descriptors = plugin_model_descriptors()
    except Exception as exc:
        logger.warning(
            "Plugin descriptor bridge failed; built-in registry left in place",
            extra={"error_type": type(exc).__name__},
        )
        return
    for descriptor in descriptors:
        register_model(descriptor, overwrite=False)
    count = len(descriptors)
    if _last_plugin_descriptor_count == count:
        logger.debug("plugins attached", extra={"model_descriptor_count": count, "unchanged": True})
    else:
        logger.info("plugins attached", extra={"model_descriptor_count": count})
    _last_plugin_descriptor_count = count


def _counts_by_capability(models: dict[str, ModelDescriptor]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for m in models.values():
        key = str(m.primary_capability)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _counts_by_status(models: dict[str, ModelDescriptor]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for m in models.values():
        counts[m.status] = counts.get(m.status, 0) + 1
    return counts
