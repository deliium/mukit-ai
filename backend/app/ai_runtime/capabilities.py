"""AI model capability taxonomy and operation→capability defaults."""

from __future__ import annotations

import logging
from enum import StrEnum

from .operations import AiOperation

logger = logging.getLogger(__name__)


class ModelCapability(StrEnum):
    """Primary capability categories for discovery grouping."""

    LANGUAGE_PLANNER = "language_planner"
    SYMBOLIC_COMPOSER = "symbolic_composer"
    SYMBOLIC_EDITOR = "symbolic_editor"
    EMBEDDING = "embedding"
    AUDIO_TRANSCRIPTION = "audio_transcription"
    AUDIO_GENERATION = "audio_generation"


# Default capability mapping for each AiOperation (routing preference).
OPERATION_DEFAULT_CAPABILITY: dict[AiOperation, ModelCapability] = {
    AiOperation.GENERATE: ModelCapability.LANGUAGE_PLANNER,
    AiOperation.GENERATE_PLANNER: ModelCapability.LANGUAGE_PLANNER,
    AiOperation.GENERATE_COMPOSER: ModelCapability.LANGUAGE_PLANNER,
    AiOperation.REGION_EDIT: ModelCapability.SYMBOLIC_EDITOR,
    AiOperation.ARRANGE_PREVIEW: ModelCapability.SYMBOLIC_EDITOR,
    AiOperation.DEVELOPMENT_PREVIEW: ModelCapability.SYMBOLIC_EDITOR,
    AiOperation.REHARMONIZE_AI: ModelCapability.SYMBOLIC_EDITOR,
    AiOperation.CREATIVE_MOTIF: ModelCapability.SYMBOLIC_EDITOR,
    AiOperation.TRANSCRIBE: ModelCapability.AUDIO_TRANSCRIPTION,
    AiOperation.EMBED: ModelCapability.EMBEDDING,
    AiOperation.AUDIO_RENDER: ModelCapability.AUDIO_GENERATION,
}


def default_capability_for_operation(operation: AiOperation) -> ModelCapability:
    return OPERATION_DEFAULT_CAPABILITY[operation]


logger.info(
    "AI capability taxonomy loaded",
    extra={
        "capability_count": len(ModelCapability),
        "operation_default_count": len(OPERATION_DEFAULT_CAPABILITY),
    },
)
