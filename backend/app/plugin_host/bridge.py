"""Reload plugin code and expose cached model descriptors to the AI registry.

This module must not import ``app.ai_runtime.registry``. The registry lazy-imports
``plugin_model_descriptors`` after built-in models are in place.
"""

from __future__ import annotations

import logging
from typing import Mapping

from app.ai_runtime.capabilities import ModelCapability
from app.ai_runtime.operations import AiOperation
from app.ai_runtime.types import ModelDescriptor, ModelHealth

from .catalog import PluginRecord, list_records
from .loader import load_plugins

logger = logging.getLogger(__name__)

_MODEL_CATEGORY: dict[str, tuple[ModelCapability, AiOperation]] = {
    "language_model": (ModelCapability.LANGUAGE_PLANNER, AiOperation.GENERATE_PLANNER),
    "symbolic_composer": (ModelCapability.SYMBOLIC_COMPOSER, AiOperation.GENERATE_COMPOSER),
    "transcription_model": (ModelCapability.AUDIO_TRANSCRIPTION, AiOperation.TRANSCRIBE),
    "neural_renderer": (ModelCapability.AUDIO_GENERATION, AiOperation.AUDIO_RENDER),
}


def reload_plugins(env: Mapping[str, str] | None = None) -> list[PluginRecord]:
    """Execute plugin discovery and ``register()``. Replaces the in-memory catalog."""
    logger.info("plugin reload start", extra={"has_env": env is not None})
    return load_plugins(env)


def plugin_model_descriptors() -> tuple[ModelDescriptor, ...]:
    """Descriptors for active model-category plugins. Does not import or call ``register``."""
    built: list[ModelDescriptor] = []
    for record in list_records():
        if record.status != "active" or record.id is None or record.category not in _MODEL_CATEGORY:
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
