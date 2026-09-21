"""Optional MIDI-DDSP / MIDI neural instrument adapter (discovery + stub invoke).

Heavy TensorFlow weights stay optional. Without extras, status is unconfigured
and render raises ModelUnavailableError — never silent FluidSynth fallback.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

from app.ai_runtime.capabilities import ModelCapability
from app.ai_runtime.errors import ModelUnavailableError
from app.ai_runtime.operations import AiOperation
from app.ai_runtime.types import ModelDescriptor, ModelHealth


logger = logging.getLogger(__name__)

LOCAL_MIDI_DDSP_MODEL_ID = "local:midi-ddsp"
LOCAL_MIDI_DDSP_RUNTIME = "local_midi_ddsp"
LOCAL_MIDI_DDSP_VERSION = "midi-ddsp-optional-1"


class LocalMidiDdspAudioModel:
    """Placeholder invoke until optional extras are installed by the operator."""

    def __init__(self, descriptor: ModelDescriptor) -> None:
        self._descriptor = descriptor
        logger.info(
            "Constructed local MIDI-DDSP audio model",
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
        return self._descriptor.model_version or LOCAL_MIDI_DDSP_VERSION

    @property
    def fidelity_class(self) -> str:
        return "neural_instrument"

    @property
    def preferred_adapter_kind(self) -> str:
        return "midi_projection"

    async def render(self, composition_or_spec: Any) -> dict[str, Any]:
        logger.warning(
            "MIDI-DDSP invoke rejected; extras not loaded",
            extra={"model_id": self.model_id},
        )
        raise ModelUnavailableError(
            "local:midi-ddsp requires optional extras and is not loaded in-process",
            code="model_unavailable",
        )


def local_midi_ddsp_descriptor(
    env: Mapping[str, str] | None = None,
) -> ModelDescriptor | None:
    _ = env
    return ModelDescriptor(
        id=LOCAL_MIDI_DDSP_MODEL_ID,
        display_name="MIDI-DDSP (optional neural instrument)",
        provider="local",
        runtime=LOCAL_MIDI_DDSP_RUNTIME,  # type: ignore[arg-type]
        primary_capability=ModelCapability.AUDIO_GENERATION,
        locality="local",
        model_version=LOCAL_MIDI_DDSP_VERSION,
        supported_operations=(AiOperation.AUDIO_RENDER,),
        status="unconfigured",
        health=ModelHealth(
            status="unconfigured",
            detail="optional_extras_not_installed",
            credentials_present=False,
        ),
        limits={
            "fidelity_class": "neural_instrument",
            "preferred_adapter": "midi_projection",
            "note_perfect": False,
        },
        provider_model="midi-ddsp",
    )


def build_local_midi_ddsp_model(descriptor: ModelDescriptor) -> LocalMidiDdspAudioModel:
    return LocalMidiDdspAudioModel(descriptor)
