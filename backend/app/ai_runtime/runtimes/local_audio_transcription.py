"""Local monophonic audio transcription adapter for AI runtime discovery.

Primary UX uses POST /transcription/audio; this adapter exists so /ai/models
can list local:audio-mono-* when an engine is available. Never routes through
language-model generate/edit graphs.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.ai_runtime.errors import ModelUnavailableError
from app.ai_runtime.types import ModelDescriptor
from app.audio_transcription_settings import load_audio_transcription_settings
from app.services.audio_transcription import transcribe_audio_bytes
from app.services.audio_transcription.engines import resolve_engine


logger = logging.getLogger(__name__)


class LocalMonoAudioTranscriptionModel:
    """Delegates to the audio transcription engine registry."""

    def __init__(self, descriptor: ModelDescriptor) -> None:
        self._descriptor = descriptor
        logger.info(
            "Constructed local mono audio transcription model",
            extra={
                "model_id": descriptor.id,
                "runtime": descriptor.runtime,
                "status": descriptor.status,
            },
        )

    @property
    def model_id(self) -> str:
        return self._descriptor.id

    @property
    def descriptor(self) -> ModelDescriptor:
        return self._descriptor

    async def transcribe(self, audio_ref: Any) -> Any:
        """Transcribe from a filesystem path or bytes-like payload."""
        settings = load_audio_transcription_settings()
        try:
            resolve_engine(settings)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Local audio transcription model unavailable",
                extra={"model_id": self.model_id, "error_type": type(exc).__name__},
            )
            raise ModelUnavailableError(
                f"Audio transcription engine unavailable: {self.model_id}",
                code="model_unavailable",
            ) from exc

        if isinstance(audio_ref, (bytes, bytearray)):
            payload = bytes(audio_ref)
        elif isinstance(audio_ref, Path):
            payload = audio_ref.read_bytes()
        elif isinstance(audio_ref, str):
            payload = Path(audio_ref).read_bytes()
        elif isinstance(audio_ref, dict) and "path" in audio_ref:
            payload = Path(str(audio_ref["path"])).read_bytes()
        else:
            raise ModelUnavailableError(
                "Unsupported audio_ref for local transcription",
                code="model_unavailable",
            )

        logger.info(
            "Local audio transcription model invoke",
            extra={"model_id": self.model_id, "upload_bytes": len(payload)},
        )
        return transcribe_audio_bytes(payload, settings=settings)


def build_local_mono_audio_transcription_model(
    descriptor: ModelDescriptor,
) -> LocalMonoAudioTranscriptionModel:
    return LocalMonoAudioTranscriptionModel(descriptor)


def local_audio_mono_descriptors(env=None) -> list:
    """Ready discovery entries when fake mode or optional engines are present."""
    from app.ai_runtime.capabilities import ModelCapability
    from app.ai_runtime.operations import AiOperation
    from app.ai_runtime.types import ModelHealth
    from app.audio_transcription_settings import load_audio_transcription_settings
    from app.services.audio_transcription.engines import (
        BasicPitchEngine,
        FakeAudioMonoEngine,
        LibrosaPyinEngine,
    )

    settings = load_audio_transcription_settings(env)
    out: list[ModelDescriptor] = []

    def _ready(model_id: str, display: str, engine_id: str) -> ModelDescriptor:
        return ModelDescriptor(
            id=model_id,
            display_name=display,
            provider="local",
            runtime="local_audio_mono",
            primary_capability=ModelCapability.AUDIO_TRANSCRIPTION,
            locality="local",
            model_version="1",
            supported_operations=(AiOperation.TRANSCRIBE,),
            status="ready",
            health=ModelHealth(
                status="ready",
                detail=f"engine:{engine_id}",
                credentials_present=True,
            ),
            limits={"engine_id": engine_id, "monophonic_only": True},
            provider_model=engine_id,
        )

    if settings.fake_mode or settings.engine == "fake:audio-mono":
        fake = FakeAudioMonoEngine()
        if fake.is_available():
            out.append(
                _ready(
                    "local:audio-mono-fake",
                    "Audio mono (fake CI)",
                    fake.engine_id,
                )
            )
    librosa_engine = LibrosaPyinEngine()
    if librosa_engine.is_available():
        out.append(
            _ready(
                "local:audio-mono-librosa",
                "Audio mono (librosa pyin)",
                librosa_engine.engine_id,
            )
        )
    basic = BasicPitchEngine()
    if basic.is_available():
        out.append(
            _ready(
                "local:audio-mono-basic-pitch",
                "Audio mono (basic pitch)",
                basic.engine_id,
            )
        )
    logger.debug(
        "Local audio mono descriptors resolved",
        extra={"count": len(out), "fake_mode": settings.fake_mode},
    )
    return out
