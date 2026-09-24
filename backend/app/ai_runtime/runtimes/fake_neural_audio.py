"""Deterministic fake neural audio generation for CI / Playwright.

Produces a short WAV from composition fingerprint + seed. No quality claim.
Never loads MusicGen / MIDI-DDSP weights.
"""

from __future__ import annotations

import hashlib
import io
import logging
import math
import struct
import wave
from typing import Any, Mapping

from app.ai_runtime.capabilities import ModelCapability
from app.ai_runtime.operations import AiOperation
from app.ai_runtime.types import ModelDescriptor, ModelHealth
from app.neural_audio_settings import load_neural_audio_settings


logger = logging.getLogger(__name__)

FAKE_NEURAL_AUDIO_MODEL_ID = "fake:neural-audio"
FAKE_NEURAL_AUDIO_RUNTIME = "fake_neural_audio"
FAKE_NEURAL_AUDIO_VERSION = "1"
_SAMPLE_RATE = 22050
_DURATION_SECONDS = 0.5


class FakeNeuralAudioGenerationModel:
    """Always-available CI engine returning a deterministic short WAV."""

    def __init__(self, descriptor: ModelDescriptor) -> None:
        self._descriptor = descriptor
        logger.info(
            "Constructed fake neural audio model",
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

    @property
    def model_version(self) -> str:
        return self._descriptor.model_version or FAKE_NEURAL_AUDIO_VERSION

    @property
    def fidelity_class(self) -> str:
        return "generative"

    @property
    def preferred_adapter_kind(self) -> str:
        return "text_prompt"

    async def render(self, composition_or_spec: Any) -> dict[str, Any]:
        """Return ``{audio_bytes, content_type, model_version, fidelity_class}``."""
        fingerprint = ""
        seed = 0
        stem_role = ""
        if isinstance(composition_or_spec, dict):
            fingerprint = str(composition_or_spec.get("source_fingerprint") or "")
            seed_raw = composition_or_spec.get("seed")
            if isinstance(seed_raw, int):
                seed = seed_raw
            stem_role = str(composition_or_spec.get("stem_role") or "")
        wav_bytes = build_fake_neural_wav(
            fingerprint=fingerprint, seed=seed, stem_role=stem_role
        )
        logger.info(
            "Fake neural audio render complete",
            extra={
                "model_id": self.model_id,
                "byte_size": len(wav_bytes),
                "sha256_prefix": hashlib.sha256(wav_bytes).hexdigest()[:16],
                "fingerprint_prefix": fingerprint[:12] if fingerprint else None,
                "seed": seed,
                "stem_role": stem_role or None,
            },
        )
        return {
            "audio_bytes": wav_bytes,
            "content_type": "audio/wav",
            "model_version": self.model_version,
            "fidelity_class": self.fidelity_class,
            "ext": "wav",
        }

    async def render_stems(self, composition_or_spec: Any) -> dict[str, Any]:
        """One-shot multi-stem map for ``direct_stems`` (role → distinct WAV)."""
        fingerprint = ""
        seed = 0
        roles: list[str] = []
        if isinstance(composition_or_spec, dict):
            fingerprint = str(composition_or_spec.get("source_fingerprint") or "")
            seed_raw = composition_or_spec.get("seed")
            if isinstance(seed_raw, int):
                seed = seed_raw
            raw_roles = composition_or_spec.get("stem_roles") or []
            if isinstance(raw_roles, (list, tuple)):
                roles = [str(r) for r in raw_roles if str(r).strip()]
        if not roles:
            raise ValueError("render_stems requires non-empty stem_roles")
        stems: dict[str, dict[str, Any]] = {}
        prefixes: list[str] = []
        for role in roles:
            wav_bytes = build_fake_neural_wav(
                fingerprint=fingerprint, seed=seed, stem_role=role
            )
            prefix = hashlib.sha256(wav_bytes).hexdigest()[:16]
            prefixes.append(prefix)
            stems[role] = {
                "audio_bytes": wav_bytes,
                "content_type": "audio/wav",
                "ext": "wav",
                "sha256_prefix": prefix,
                "byte_size": len(wav_bytes),
            }
        logger.info(
            "Fake neural direct_stems complete",
            extra={
                "model_id": self.model_id,
                "capability_used": "direct_stems",
                "stem_role_count": len(roles),
                "stem_roles": roles,
                "sha256_prefixes": prefixes,
                "fingerprint_prefix": fingerprint[:12] if fingerprint else None,
                "seed": seed,
            },
        )
        return {
            "stems": stems,
            "model_version": self.model_version,
            "fidelity_class": self.fidelity_class,
            "content_type": "audio/wav",
            "ext": "wav",
        }


def build_fake_neural_wav(
    *,
    fingerprint: str = "",
    seed: int = 0,
    stem_role: str = "",
) -> bytes:
    """Build a short mono PCM WAV deterministic for (fp, seed[, stem_role])."""
    if stem_role:
        material = f"{fingerprint}|{seed}|{stem_role}|fake:neural-audio".encode("utf-8")
    else:
        # Preserve mix-job hash stability when no stem role is in play.
        material = f"{fingerprint}|{seed}|fake:neural-audio".encode("utf-8")
    digest = hashlib.sha256(material).digest()
    # Map digest bytes to a stable pitch class (MIDI 60–71) and amplitude.
    midi = 60 + (digest[0] % 12)
    freq = 440.0 * (2.0 ** ((midi - 69) / 12.0))
    amplitude = 0.15 + (digest[1] / 255.0) * 0.2
    n_frames = int(_SAMPLE_RATE * _DURATION_SECONDS)
    frames = bytearray()
    for i in range(n_frames):
        t = i / _SAMPLE_RATE
        sample = amplitude * math.sin(2.0 * math.pi * freq * t)
        # Soft fade-out to avoid clicks.
        if i > n_frames - 1000:
            sample *= (n_frames - i) / 1000.0
        pcm = max(-32767, min(32767, int(sample * 32767)))
        frames.extend(struct.pack("<h", pcm))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(_SAMPLE_RATE)
        handle.writeframes(bytes(frames))
    return buffer.getvalue()


def fake_neural_audio_sha256_prefix(
    *,
    fingerprint: str = "",
    seed: int = 0,
    stem_role: str = "",
) -> str:
    return hashlib.sha256(
        build_fake_neural_wav(fingerprint=fingerprint, seed=seed, stem_role=stem_role)
    ).hexdigest()[:16]


def default_fake_neural_audio_descriptor() -> ModelDescriptor:
    return ModelDescriptor(
        id=FAKE_NEURAL_AUDIO_MODEL_ID,
        display_name="Neural audio (fake CI)",
        provider="fake",
        runtime=FAKE_NEURAL_AUDIO_RUNTIME,  # type: ignore[arg-type]
        primary_capability=ModelCapability.AUDIO_GENERATION,
        locality="local",
        model_version=FAKE_NEURAL_AUDIO_VERSION,
        supported_operations=(AiOperation.AUDIO_RENDER,),
        status="ready",
        health=ModelHealth(
            status="ready",
            detail="fake_mode",
            credentials_present=True,
        ),
        limits={
            "fidelity_class": "generative",
            "preferred_adapter": "text_prompt",
            "max_audio_seconds": _DURATION_SECONDS,
            "note_perfect": False,
            "stem_capabilities": [
                "direct_stems",
                "per_track",
                "grouped_tracks",
                "section_symbolic_filter",
            ],
        },
        provider_model="neural-audio",
    )


def build_fake_neural_audio_model(
    descriptor: ModelDescriptor,
) -> FakeNeuralAudioGenerationModel:
    return FakeNeuralAudioGenerationModel(descriptor)


def neural_audio_descriptors(env: Mapping[str, str] | None = None) -> list[ModelDescriptor]:
    """Ready discovery entries for fake / sidecar when available."""
    from app.ai_runtime.runtimes.sidecar_musicgen import (
        sidecar_musicgen_descriptor,
    )

    settings = load_neural_audio_settings(env)
    out: list[ModelDescriptor] = []
    if settings.fake_mode or settings.engine == "fake:neural-audio":
        out.append(default_fake_neural_audio_descriptor())
        logger.info(
            "Registered fake neural audio model for discovery",
            extra={"model_id": FAKE_NEURAL_AUDIO_MODEL_ID, "fake_mode": settings.fake_mode},
        )
    sidecar = sidecar_musicgen_descriptor(env)
    if sidecar is not None:
        out.append(sidecar)
    # MIDI-DDSP remains discovery-only when explicitly pinned; heavy extras optional.
    if settings.engine == "local:midi-ddsp":
        from app.ai_runtime.runtimes.local_midi_ddsp import local_midi_ddsp_descriptor

        midi_desc = local_midi_ddsp_descriptor(env)
        if midi_desc is not None:
            out.append(midi_desc)
    logger.debug(
        "Neural audio descriptors resolved",
        extra={"count": len(out), "fake_mode": settings.fake_mode, "engine": settings.engine},
    )
    return out
