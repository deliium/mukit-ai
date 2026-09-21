"""Registry-facing fake LanguageModel identity (orchestrators short-circuit via fake_llm)."""

from __future__ import annotations

import logging

from ..errors import ModelUnavailableError
from ..types import ModelDescriptor

logger = logging.getLogger(__name__)


class FakeLanguageModel:
    """Marker LanguageModel for ``runtime=fake``.

    Orchestrators must short-circuit to ``fake_llm`` draft helpers and must not
    call ``complete_text`` for network chat stages. This adapter exists so the
    registry can expose a typed identity and tests can assert fake resolution.
    """

    def __init__(self, descriptor: ModelDescriptor) -> None:
        if descriptor.runtime != "fake":
            raise ModelUnavailableError(
                f"Runtime mismatch for {descriptor.id}: expected fake",
                code="model_unavailable",
            )
        self._descriptor = descriptor
        logger.info(
            "Constructed fake LanguageModel identity",
            extra={
                "model_id": descriptor.id,
                "runtime": "fake",
                "timeout_seconds": None,
            },
        )

    @property
    def model_id(self) -> str:
        return self._descriptor.id

    @property
    def descriptor(self) -> ModelDescriptor:
        return self._descriptor

    @property
    def is_fake(self) -> bool:
        return True

    async def complete_text(self, prompt: str, *, purpose: str | None = None) -> str:
        logger.warning(
            "Fake LanguageModel complete_text invoked; orchestrators should short-circuit",
            extra={"model_id": self.model_id, "purpose": purpose, "prompt_length": len(prompt or "")},
        )
        raise ModelUnavailableError(
            "Fake runtime does not perform chat completions; use fake_llm draft helpers",
            code="model_unavailable",
        )


def build_fake_language_model(descriptor: ModelDescriptor) -> FakeLanguageModel:
    return FakeLanguageModel(descriptor)
