"""Strict neural_audio_render.job.v1 DTOs, fidelity/adapter enums, and error codes.

Neural audio is an egress / rendering layer only. Jobs never mutate
composition.v2, autosave, or revision snapshots. Responses never include
raw audio bytes (download via dedicated audio route).
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


logger = logging.getLogger(__name__)

NEURAL_AUDIO_JOB_SCHEMA_VERSION: Literal["neural_audio_render.job.v1"] = (
    "neural_audio_render.job.v1"
)

NeuralAudioFidelityClass = Literal[
    "deterministic",
    "neural_instrument",
    "generative",
]

NeuralAudioAdapterKind = Literal[
    "midi_projection",
    "melody_conditioning",
    "text_prompt",
]

NeuralAudioJobStatus = Literal["queued", "running", "failed", "complete"]

NEURAL_AUDIO_FIDELITY_LABELS: dict[str, str] = {
    "deterministic": "Deterministic",
    "neural_instrument": "Neural instrument (approximate notes)",
    "generative": "Generative AI (not note-perfect)",
}

NEURAL_AUDIO_ADAPTER_WARNING_CODES: dict[str, str] = {
    "generative_approximation": "Engine is generative; output is not note-perfect.",
    "polyphony_flattened": "Polyphonic material was flattened to a monophonic melody guide.",
    "midi_projection_lossy": "MIDI projection may omit expressive V2 fields.",
    "text_only_conditioning": "Adapter uses text context only; notes are not preserved.",
    "tempo_override_applied": "User tempo override replaced composition tempo.",
}

# Stable HTTP-mapped error codes.
NEURAL_AUDIO_ERROR_CODES: dict[str, str] = {
    "neural_audio_unavailable": "Neural audio rendering is not configured.",
    "neural_audio_engine_unavailable": "Requested or auto-selected neural engine is unavailable.",
    "neural_audio_quota_exceeded": "Per-project render count or total byte quota exceeded.",
    "source_revision_not_found": "Pinned source revision was not found for the project.",
    "render_not_found": "Neural audio render job was not found.",
    "render_not_ready": "Audio download is only available when the job is complete.",
    "neural_audio_invalid_request": "Enqueue request failed validation.",
    "neural_audio_prompt_too_long": "Production instructions exceed the configured character limit.",
    "neural_audio_internal_error": "Unexpected neural audio failure (sanitized).",
    "neural_audio_job_timeout": "Neural audio job exceeded the configured timeout.",
    "neural_audio_composition_required": "Composition V2 body or project+revision is required.",
}

NeuralAudioErrorCode = Literal[
    "neural_audio_unavailable",
    "neural_audio_engine_unavailable",
    "neural_audio_quota_exceeded",
    "source_revision_not_found",
    "render_not_found",
    "render_not_ready",
    "neural_audio_invalid_request",
    "neural_audio_prompt_too_long",
    "neural_audio_internal_error",
    "neural_audio_job_timeout",
    "neural_audio_composition_required",
]


class NeuralAudioError(Exception):
    """Domain error mapped to structured HTTP responses."""

    def __init__(
        self,
        code: NeuralAudioErrorCode,
        message: str,
        *,
        http_status: int,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.details = details or {}
        logger.error(
            "Neural audio domain error",
            extra={
                "error_code": code,
                "http_status": http_status,
                "detail_keys": sorted(self.details.keys()),
            },
        )


class NeuralAudioEnqueueRequest(BaseModel):
    """Enqueue a neural render. Jobs never write back into composition state."""

    model_config = ConfigDict(extra="forbid")

    project_id: str | None = Field(default=None, min_length=1, max_length=64)
    source_revision_id: str | None = Field(default=None, min_length=1, max_length=64)
    composition: dict[str, Any] | None = None
    instructions: str = Field(default="", max_length=8000)
    genre: str | None = Field(default=None, max_length=256)
    mood: str | None = Field(default=None, max_length=256)
    model_id: str | None = Field(default=None, min_length=1, max_length=128)
    adapter_kind: NeuralAudioAdapterKind | None = None
    tempo_bpm: float | None = Field(default=None, ge=20.0, le=400.0)
    instrumentation_summary: str | None = Field(default=None, max_length=2000)
    seed: int | None = Field(default=None, ge=0, le=2_147_483_647)

    @field_validator("instructions", "genre", "mood", "instrumentation_summary", mode="before")
    @classmethod
    def _strip_optional_text(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, str):
            return value.strip()
        return value

    @model_validator(mode="after")
    def _require_composition_or_revision(self) -> NeuralAudioEnqueueRequest:
        has_composition = isinstance(self.composition, dict) and bool(self.composition)
        has_revision = bool(self.project_id and self.source_revision_id)
        if not has_composition and not has_revision:
            logger.warning(
                "Neural audio enqueue rejected: missing composition or revision",
                extra={"has_project_id": bool(self.project_id)},
            )
            raise ValueError(
                "Provide composition V2 and/or project_id+source_revision_id"
            )
        if self.project_id and not self.source_revision_id and not has_composition:
            raise ValueError("source_revision_id is required when project_id is set")
        return self


class NeuralAudioJobResponse(BaseModel):
    """Job metadata only — never includes PCM / audio bytes."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["neural_audio_render.job.v1"] = NEURAL_AUDIO_JOB_SCHEMA_VERSION
    id: str
    project_id: str | None = None
    source_revision_id: str | None = None
    source_fingerprint: str
    status: NeuralAudioJobStatus
    model_id: str
    model_version: str | None = None
    adapter_kind: NeuralAudioAdapterKind
    fidelity_class: NeuralAudioFidelityClass
    fidelity_label: str
    instructions: str = ""
    genre: str | None = None
    mood: str | None = None
    instrumentation_summary: str | None = None
    tempo_bpm: float | None = None
    seed: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    audio_relpath: str | None = None
    content_type: str | None = None
    byte_size: int | None = Field(default=None, ge=0)
    sha256_prefix: str | None = None
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    adapter_warnings: list[str] = Field(default_factory=list)
    # Explicit contract reminder for API consumers / UI.
    mutates_composition: Literal[False] = False


class NeuralAudioJobListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[NeuralAudioJobResponse]
    total: int = Field(ge=0)


class NeuralAudioErrorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: NeuralAudioErrorCode
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
