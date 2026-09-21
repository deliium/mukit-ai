"""Unified AI runtime: capability-aware model registry, routing, and typed adapters.

Business logic (composition generate/edit/arrange/…) resolves models by
``AiOperation`` and talks to typed protocols. FluidSynth WAV export stays
outside this package.
"""

from __future__ import annotations

import logging

from .capabilities import ModelCapability, default_capability_for_operation
from .errors import (
    AiRuntimeError,
    CapabilityMismatchError,
    FallbackNotConfiguredError,
    ModelNotFoundError,
    ModelUnavailableError,
)
from .operations import AiOperation, creative_chat_operations, fallback_env_key, operation_env_key
from .protocols import (
    AudioGenerationModel,
    AudioTranscriptionModel,
    EmbeddingModel,
    LanguageModel,
    SymbolicEmbeddingModel,
    SymbolicMusicModel,
)
from .types import (
    GenerationParameters,
    ModelDescriptor,
    ModelHealth,
    ResolvedModel,
)

logger = logging.getLogger(__name__)

logger.debug("ai_runtime package imported")

__all__ = [
    "AiOperation",
    "AiRuntimeError",
    "AudioGenerationModel",
    "AudioTranscriptionModel",
    "CapabilityMismatchError",
    "EmbeddingModel",
    "FallbackNotConfiguredError",
    "GenerationParameters",
    "LanguageModel",
    "ModelCapability",
    "ModelDescriptor",
    "ModelHealth",
    "ModelNotFoundError",
    "ModelUnavailableError",
    "ResolvedModel",
    "SymbolicEmbeddingModel",
    "SymbolicMusicModel",
    "creative_chat_operations",
    "default_capability_for_operation",
    "fallback_env_key",
    "operation_env_key",
]
