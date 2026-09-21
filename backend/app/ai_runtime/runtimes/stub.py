"""Unavailable stub adapters for embedding / transcription / audio generation."""

from __future__ import annotations

import logging
from typing import Any

from ..errors import ModelUnavailableError
from ..types import ModelDescriptor

logger = logging.getLogger(__name__)


class StubModelBase:
    """Base for unconfigured stub models that always raise ModelUnavailableError."""

    def __init__(self, descriptor: ModelDescriptor) -> None:
        if descriptor.runtime != "stub":
            raise ModelUnavailableError(
                f"Runtime mismatch for {descriptor.id}: expected stub",
                code="model_unavailable",
            )
        self._descriptor = descriptor
        logger.info(
            "Constructed stub model (unconfigured)",
            extra={
                "model_id": descriptor.id,
                "runtime": "stub",
                "status": descriptor.status,
                "primary_capability": descriptor.primary_capability,
            },
        )

    @property
    def model_id(self) -> str:
        return self._descriptor.id

    @property
    def descriptor(self) -> ModelDescriptor:
        return self._descriptor

    def _raise_unavailable(self, method: str) -> None:
        logger.warning(
            "Stub model invoke rejected",
            extra={"model_id": self.model_id, "method": method, "error_type": "ModelUnavailableError"},
        )
        raise ModelUnavailableError(
            f"Model unavailable (unconfigured stub): {self.model_id}",
            code="model_unavailable",
        )


class StubEmbeddingModel(StubModelBase):
    async def embed(self, texts: list[str]) -> list[list[float]]:
        self._raise_unavailable("embed")
        return []  # pragma: no cover


class StubAudioTranscriptionModel(StubModelBase):
    async def transcribe(self, audio_ref: Any) -> Any:
        self._raise_unavailable("transcribe")


class StubAudioGenerationModel(StubModelBase):
    async def render(self, composition_or_spec: Any) -> Any:
        self._raise_unavailable("render")


def build_stub_model(descriptor: ModelDescriptor) -> StubModelBase:
    from ..capabilities import ModelCapability

    if descriptor.primary_capability == ModelCapability.EMBEDDING:
        return StubEmbeddingModel(descriptor)
    if descriptor.primary_capability == ModelCapability.AUDIO_TRANSCRIPTION:
        return StubAudioTranscriptionModel(descriptor)
    if descriptor.primary_capability == ModelCapability.AUDIO_GENERATION:
        return StubAudioGenerationModel(descriptor)
    return StubModelBase(descriptor)
