"""Reload plugin code and expose cached model descriptors to the AI registry.

This module must not import ``app.ai_runtime.registry``. The registry lazy-imports
``plugin_model_descriptors`` after built-in models are in place.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

from app.ai_runtime.capabilities import ModelCapability
from app.ai_runtime.operations import AiOperation
from app.ai_runtime.types import ModelDescriptor, ModelHealth
from app.plugin_sdk.errors import PluginError

from .catalog import PluginRecord, get_record, list_records
from .loader import activate_loaded_plugin, load_plugins

logger = logging.getLogger(__name__)

_MODEL_CATEGORY: dict[str, tuple[ModelCapability, AiOperation]] = {
    "language_model": (ModelCapability.LANGUAGE_PLANNER, AiOperation.GENERATE_PLANNER),
    "symbolic_composer": (ModelCapability.SYMBOLIC_COMPOSER, AiOperation.GENERATE_COMPOSER),
    "transcription_model": (ModelCapability.AUDIO_TRANSCRIPTION, AiOperation.TRANSCRIBE),
    "neural_renderer": (ModelCapability.AUDIO_GENERATION, AiOperation.AUDIO_RENDER),
}


def reload_plugins(env: Mapping[str, str] | None = None) -> list[PluginRecord]:
    """Discover manifests only. Does not call ``register()``."""
    logger.info("plugin reload start", extra={"has_env": env is not None})
    return load_plugins(env)


def activate_plugin(plugin_id: str, config: Mapping[str, Any]) -> PluginRecord:
    """Register one discovered plugin with a host-merged config. Does not read SQLite."""
    record = get_record(plugin_id)
    if record is None or record.id is None:
        raise PluginError("plugin_not_found", "plugin_not_found")
    logger.info("plugin activate requested", extra={"plugin_id": plugin_id})
    return activate_loaded_plugin(record, config)


def plugin_model_descriptors() -> tuple[ModelDescriptor, ...]:
    """Descriptors for active model-category plugins. Does not import or call ``register``."""
    built: list[ModelDescriptor] = []
    for record in list_records():
        if record.status != "enabled" or record.id is None or record.category not in _MODEL_CATEGORY:
            continue
        capability, operation = _MODEL_CATEGORY[record.category]
        model_id = f"plugin:{record.id}"
        descriptor = ModelDescriptor(
            id=model_id,
            display_name=record.name or record.id,
            provider="plugin",
            runtime="plugin",
            primary_capability=capability,
            locality="local",
            model_version=record.version,
            supported_operations=(operation,),
            status="ready",
            health=ModelHealth(status="ready", credentials_present=False),
            limits={},
        )
        built.append(descriptor)
        logger.debug(
            "plugin model descriptor",
            extra={"model_id": model_id, "capability": str(capability)},
        )
    return tuple(built)
