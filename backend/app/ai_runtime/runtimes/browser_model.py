"""Discovery-only BrowserModel descriptors (``runtime=browser_model``).

SPA ``BrowserModelHost`` executes these clientside. The server never constructs
a Python executor for them and never selects them for embed resolve or
scheduling candidates.
"""

from __future__ import annotations

import logging

from app.embeddings.settings import (
    EMBEDDING_ALGORITHM_VERSION,
    EMBEDDING_PROFILE_ID,
)

from ..capabilities import ModelCapability
from ..operations import AiOperation
from ..types import ModelDescriptor, ModelHealth

logger = logging.getLogger(__name__)

BROWSER_MODEL_RUNTIME = "browser_model"
BROWSER_SYMBOLIC_FEATURES_MODEL_ID = "browser:symbolic-features-v1"
BROWSER_SYMBOLIC_FEATURES_MAX_NOTE_COUNT = 20000


def default_browser_symbolic_features_descriptor() -> ModelDescriptor:
    """Ship-1 BrowserModel: public handcrafted twin + optional WebGPU cosine."""
    return ModelDescriptor(
        id=BROWSER_SYMBOLIC_FEATURES_MODEL_ID,
        display_name="Browser symbolic features (handcrafted v1)",
        provider="browser",
        runtime=BROWSER_MODEL_RUNTIME,  # type: ignore[arg-type]
        primary_capability=ModelCapability.EMBEDDING,
        locality="local",
        model_version=EMBEDDING_PROFILE_ID,
        supported_operations=(AiOperation.EMBED,),
        status="ready",
        health=ModelHealth(
            status="ready",
            detail="browser_model_discovery_only",
            credentials_present=False,
        ),
        limits={
            "profile_id": EMBEDDING_PROFILE_ID,
            "algorithm_version": EMBEDDING_ALGORITHM_VERSION,
            "max_note_count": BROWSER_SYMBOLIC_FEATURES_MAX_NOTE_COUNT,
            "webgpu_optional": True,
            "webgpu_sub_path": "batched_cosine_wgsl",
            "torch_required": False,
            "artist_as_style_id": False,
            "server_executable": False,
        },
        provider_model="symbolic-features-v1",
    )


def register_browser_models(models: dict[str, ModelDescriptor]) -> int:
    """Insert BrowserModel descriptors into a bootstrap map. Returns count added."""
    added = 0
    descriptor = default_browser_symbolic_features_descriptor()
    if descriptor.id in models:
        logger.warning(
            "Browser model id collides with existing entry",
            extra={"model_id": descriptor.id},
        )
        return 0
    models[descriptor.id] = descriptor
    added += 1
    logger.info(
        "Registered browser_model descriptors",
        extra={
            "browser_model_count": added,
            "model_id": descriptor.id,
            "runtime": BROWSER_MODEL_RUNTIME,
            "primary_capability": str(descriptor.primary_capability),
        },
    )
    logger.debug(
        "Browser model descriptor limits (public only)",
        extra={
            "model_id": descriptor.id,
            "profile_id": descriptor.limits.get("profile_id"),
            "max_note_count": descriptor.limits.get("max_note_count"),
            "webgpu_optional": descriptor.limits.get("webgpu_optional"),
        },
    )
    return added
