"""Music Transformer / fake symbolic composer runtime descriptors."""

from __future__ import annotations

import logging
import os
from typing import Mapping

from app.services.fake_symbolic_composer import (
    FAKE_ENSEMBLE_MODEL_IDS,
    FAKE_SYMBOLIC_DENSE_MODEL_ID,
    FAKE_SYMBOLIC_DENSE_VERSION,
    FAKE_SYMBOLIC_MODEL_ID,
    FAKE_SYMBOLIC_RUNTIME,
    FAKE_SYMBOLIC_SPARSE_MODEL_ID,
    FAKE_SYMBOLIC_SPARSE_VERSION,
    FAKE_SYMBOLIC_TINY_MODEL_ID,
    FAKE_SYMBOLIC_VERSION,
)
from app.services.symbolic_composition_generate import (
    SYMBOLIC_COMPOSER_MODEL_ID_MT,
    symbolic_composer_available,
)

from ..capabilities import ModelCapability
from ..operations import AiOperation
from ..types import ModelDescriptor, ModelHealth


logger = logging.getLogger(__name__)

MUSIC_TRANSFORMER_RUNTIME = "music_transformer"
FAKE_SYMBOLIC_RUNTIME_ID = "fake_symbolic"

_FAKE_DESCRIPTOR_SPECS: tuple[tuple[str, str, str, str], ...] = (
    (
        FAKE_SYMBOLIC_TINY_MODEL_ID,
        "Fake symbolic tiny",
        FAKE_SYMBOLIC_VERSION,
        "symbolic-tiny",
    ),
    (
        FAKE_SYMBOLIC_SPARSE_MODEL_ID,
        "Fake symbolic sparse",
        FAKE_SYMBOLIC_SPARSE_VERSION,
        "symbolic-sparse",
    ),
    (
        FAKE_SYMBOLIC_DENSE_MODEL_ID,
        "Fake symbolic dense",
        FAKE_SYMBOLIC_DENSE_VERSION,
        "symbolic-dense",
    ),
)


def default_fake_symbolic_descriptor() -> ModelDescriptor:
    """Always-ready tiny symbolic composer for fake mode / pytest."""
    return _fake_symbolic_descriptor(
        model_id=FAKE_SYMBOLIC_TINY_MODEL_ID,
        display_name="Fake symbolic tiny",
        model_version=FAKE_SYMBOLIC_VERSION,
        provider_model="symbolic-tiny",
    )


def fake_symbolic_ensemble_descriptors() -> tuple[ModelDescriptor, ...]:
    """Ready descriptors for tiny / sparse / dense fake ensemble composers."""
    return tuple(
        _fake_symbolic_descriptor(
            model_id=model_id,
            display_name=display_name,
            model_version=model_version,
            provider_model=provider_model,
        )
        for model_id, display_name, model_version, provider_model in _FAKE_DESCRIPTOR_SPECS
    )


def _fake_symbolic_descriptor(
    *,
    model_id: str,
    display_name: str,
    model_version: str,
    provider_model: str,
) -> ModelDescriptor:
    density = provider_model.removeprefix("symbolic-")
    return ModelDescriptor(
        id=model_id,
        display_name=display_name,
        provider="fake",
        runtime=FAKE_SYMBOLIC_RUNTIME_ID,  # type: ignore[arg-type]
        primary_capability=ModelCapability.SYMBOLIC_COMPOSER,
        locality="local",
        model_version=model_version,
        supported_operations=(AiOperation.GENERATE_COMPOSER,),
        status="ready",
        health=ModelHealth(
            status="ready",
            detail=f"fake_symbolic_{density}",
            credentials_present=True,
        ),
        limits={
            "torch_required": False,
            "deterministic": True,
            "pitch_rule": "diatonic_scale_degree_from_form_key",
            "density": density,
            "ensemble_fake": True,
        },
        provider_model=provider_model,
    )


def music_transformer_descriptor(env: Mapping[str, str] | None = None) -> ModelDescriptor:
    """Register Music Transformer with ready/unavailable based on checkpoint policy."""
    source = env if env is not None else os.environ
    ready, model_id, reason = symbolic_composer_available(prefer_fake=False, env=source)
    status = "ready" if ready else "unavailable"
    detail = "music_transformer_checkpoint_ready" if ready else (reason or "symbolic_composer_unavailable")
    logger.info(
        "Built music transformer descriptor",
        extra={
            "model_id": model_id,
            "status": status,
            "detail": detail,
            "runtime": MUSIC_TRANSFORMER_RUNTIME,
        },
    )
    return ModelDescriptor(
        id=SYMBOLIC_COMPOSER_MODEL_ID_MT,
        display_name="Music Transformer (local symbolic)",
        provider="local",
        runtime=MUSIC_TRANSFORMER_RUNTIME,  # type: ignore[arg-type]
        primary_capability=ModelCapability.SYMBOLIC_COMPOSER,
        locality="local",
        model_version="music_transformer.v1",
        supported_operations=(AiOperation.GENERATE_COMPOSER,),
        status=status,  # type: ignore[arg-type]
        health=ModelHealth(
            status=status,  # type: ignore[arg-type]
            detail=detail,
            credentials_present=ready,
        ),
        limits={
            "torch_required": True,
            "api_enabled_env": "MUSIC_TRANSFORMER_API_ENABLED",
            "graph_enabled_env": "MUSIC_TRANSFORMER_GRAPH_ENABLED",
        },
        provider_model="music-transformer",
    )


__all__ = [
    "FAKE_ENSEMBLE_MODEL_IDS",
    "FAKE_SYMBOLIC_MODEL_ID",
    "FAKE_SYMBOLIC_RUNTIME",
    "FAKE_SYMBOLIC_RUNTIME_ID",
    "MUSIC_TRANSFORMER_RUNTIME",
    "default_fake_symbolic_descriptor",
    "fake_symbolic_ensemble_descriptors",
    "music_transformer_descriptor",
]
