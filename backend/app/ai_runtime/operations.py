"""Stable AI operation catalog for routing and provenance."""

from __future__ import annotations

import logging
from enum import StrEnum

logger = logging.getLogger(__name__)


class AiOperation(StrEnum):
    """App intent ids for model routing (env keys: AI_OP_<NAME>)."""

    GENERATE = "generate"
    GENERATE_PLANNER = "generate_planner"
    GENERATE_COMPOSER = "generate_composer"
    REGION_EDIT = "region_edit"
    ARRANGE_PREVIEW = "arrange_preview"
    DEVELOPMENT_PREVIEW = "development_preview"
    REHARMONIZE_AI = "reharmonize_ai"
    CREATIVE_MOTIF = "creative_motif"
    TRANSCRIBE = "transcribe"
    EMBED = "embed"
    AUDIO_RENDER = "audio_render"


# Reserved planner/composer ops fall back to GENERATE for this plan.
_OP_ENV_KEYS: dict[AiOperation, str] = {
    AiOperation.GENERATE: "AI_OP_GENERATE",
    AiOperation.GENERATE_PLANNER: "AI_OP_GENERATE_PLANNER",
    AiOperation.GENERATE_COMPOSER: "AI_OP_GENERATE_COMPOSER",
    AiOperation.REGION_EDIT: "AI_OP_REGION_EDIT",
    AiOperation.ARRANGE_PREVIEW: "AI_OP_ARRANGE_PREVIEW",
    AiOperation.DEVELOPMENT_PREVIEW: "AI_OP_DEVELOPMENT_PREVIEW",
    AiOperation.REHARMONIZE_AI: "AI_OP_REHARMONIZE_AI",
    AiOperation.CREATIVE_MOTIF: "AI_OP_CREATIVE_MOTIF",
    AiOperation.TRANSCRIBE: "AI_OP_TRANSCRIBE",
    AiOperation.EMBED: "AI_OP_EMBED",
    AiOperation.AUDIO_RENDER: "AI_OP_AUDIO_RENDER",
}

_FALLBACK_ENV_KEYS: dict[AiOperation, str] = {
    op: f"AI_FALLBACK_{op.name}" for op in AiOperation
}


def operation_env_key(operation: AiOperation) -> str:
    return _OP_ENV_KEYS[operation]


def fallback_env_key(operation: AiOperation) -> str:
    return _FALLBACK_ENV_KEYS[operation]


def creative_chat_operations() -> tuple[AiOperation, ...]:
    """Operations backed by env-bootstrapped OpenAI/DeepSeek/Fake chat models."""
    return (
        AiOperation.GENERATE,
        AiOperation.REGION_EDIT,
        AiOperation.ARRANGE_PREVIEW,
        AiOperation.DEVELOPMENT_PREVIEW,
        AiOperation.REHARMONIZE_AI,
        AiOperation.CREATIVE_MOTIF,
    )


logger.info(
    "AI operation catalog loaded",
    extra={"operation_count": len(AiOperation)},
)
