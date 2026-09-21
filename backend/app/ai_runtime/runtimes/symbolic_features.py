"""Handcrafted symbolic features embedding runtime (``symbolic_features``)."""

from __future__ import annotations

import logging
from typing import Any

from app.composition_schemas import CompositionV2
from app.embeddings.features import embed_composition_scope as compute_embed
from app.embeddings.schemas import CompositionEmbedScope, CompositionEmbeddingV1, EmbedScopeComposition
from app.embeddings.settings import (
    EMBEDDING_DEFAULT_MODEL_ID,
    EMBEDDING_PROFILE_ID,
    EMBEDDING_DEFAULT_RUNTIME,
)

from ..errors import ModelUnavailableError
from ..types import ModelDescriptor

logger = logging.getLogger(__name__)

SYMBOLIC_FEATURES_RUNTIME = EMBEDDING_DEFAULT_RUNTIME  # "symbolic_features"


class SymbolicFeaturesEmbeddingModel:
    """Ready local embedder: ``local:symbolic-features-v1`` / profile ``symbolic.features.v1``."""

    def __init__(self, descriptor: ModelDescriptor) -> None:
        if descriptor.runtime != SYMBOLIC_FEATURES_RUNTIME:
            raise ModelUnavailableError(
                f"Runtime mismatch for {descriptor.id}: expected {SYMBOLIC_FEATURES_RUNTIME}",
                code="model_unavailable",
            )
        if descriptor.status != "ready":
            logger.error(
                "Symbolic features model not ready",
                extra={"model_id": descriptor.id, "status": descriptor.status},
            )
            raise ModelUnavailableError(
                f"Embedding model unavailable: {descriptor.id}",
                code="model_unavailable",
            )
        self._descriptor = descriptor
        logger.info(
            "Constructed symbolic features embedding model",
            extra={
                "model_id": descriptor.id,
                "runtime": SYMBOLIC_FEATURES_RUNTIME,
                "status": descriptor.status,
                "profile_id": EMBEDDING_PROFILE_ID,
            },
        )

    @property
    def model_id(self) -> str:
        return self._descriptor.id

    @property
    def descriptor(self) -> ModelDescriptor:
        return self._descriptor

    def embed_composition_scope(
        self,
        composition: CompositionV2 | dict[str, Any],
        scope: CompositionEmbedScope | None = None,
    ) -> CompositionEmbeddingV1:
        if isinstance(composition, dict):
            validated = CompositionV2.model_validate(composition)
        else:
            validated = composition
        resolved_scope: CompositionEmbedScope = scope or EmbedScopeComposition()
        logger.debug(
            "Symbolic embed_composition_scope entry",
            extra={
                "model_id": self.model_id,
                "scope_kind": getattr(resolved_scope, "kind", None),
            },
        )
        return compute_embed(validated, resolved_scope, model_id=self.model_id)


def build_symbolic_features_embedding_model(
    descriptor: ModelDescriptor,
) -> SymbolicFeaturesEmbeddingModel:
    return SymbolicFeaturesEmbeddingModel(descriptor)


def default_symbolic_features_descriptor() -> ModelDescriptor:
    """Registry descriptor for the ready handcrafted embedder."""
    from ..capabilities import ModelCapability
    from ..operations import AiOperation
    from ..types import ModelHealth

    return ModelDescriptor(
        id=EMBEDDING_DEFAULT_MODEL_ID,
        display_name="Symbolic features (handcrafted v1)",
        provider="local",
        runtime=SYMBOLIC_FEATURES_RUNTIME,  # type: ignore[arg-type]
        primary_capability=ModelCapability.EMBEDDING,
        locality="local",
        model_version=EMBEDDING_PROFILE_ID,
        supported_operations=(AiOperation.EMBED,),
        status="ready",
        health=ModelHealth(
            status="ready",
            detail="symbolic_features_handcrafted",
            credentials_present=True,
        ),
        limits={
            "profile_id": EMBEDDING_PROFILE_ID,
            "torch_required": False,
            "artist_as_style_id": False,
        },
        provider_model="symbolic-features-v1",
    )
