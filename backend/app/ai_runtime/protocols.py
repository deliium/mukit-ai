"""Typed AI model interfaces (Protocols). Prefer separate surfaces over one mega-invoke."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LanguageModel(Protocol):
    """Chat / instruction-following language model used by LangGraph stages."""

    @property
    def model_id(self) -> str: ...

    async def complete_text(self, prompt: str, *, purpose: str | None = None) -> str:
        """Return raw text completion (no prompts logged by callers)."""
        ...


@runtime_checkable
class SymbolicMusicModel(Protocol):
    """Symbolic composition / edit model. V2 chat adapters may delegate to LanguageModel."""

    @property
    def model_id(self) -> str: ...

    async def compose(self, request: Any) -> Any: ...

    async def edit(self, request: Any) -> Any: ...

    async def draft_arrangement(self, request: Any) -> Any: ...

    async def draft_development(self, request: Any) -> Any: ...


@runtime_checkable
class EmbeddingModel(Protocol):
    """Text embedding model (stub capability in this plan)."""

    @property
    def model_id(self) -> str: ...

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


@runtime_checkable
class AudioTranscriptionModel(Protocol):
    """Audio → text / MIDI-ish transcription (stub)."""

    @property
    def model_id(self) -> str: ...

    async def transcribe(self, audio_ref: Any) -> Any: ...


@runtime_checkable
class AudioGenerationModel(Protocol):
    """Neural audio render distinct from FluidSynth WAV export (stub)."""

    @property
    def model_id(self) -> str: ...

    async def render(self, composition_or_spec: Any) -> Any: ...
