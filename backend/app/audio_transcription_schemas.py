"""Strict transcription.preview.v1 DTOs, issue codes, and domain errors.

Preview documents are session-scoped, non-playable, and must never be persisted
in PROJECT_DB_PATH, autosave, or revision snapshots. Confidence lives only on
provisional notes; Apply strips it before writing composition.v2 events
(``extra=\"forbid\"``).
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


logger = logging.getLogger(__name__)

TRANSCRIPTION_PREVIEW_SCHEMA_VERSION: Literal["transcription.preview.v1"] = (
    "transcription.preview.v1"
)

TranscriptionIssueSeverity = Literal["info", "warning"]
TranscriptionTempoSource = Literal["provided", "estimated", "defaulted"]

# Closed stable issue-code registry (preview diagnostics; not HTTP errors).
TRANSCRIPTION_ISSUE_CODES: dict[str, str] = {
    "tempo_estimated": "Tempo was estimated from inter-onset intervals.",
    "tempo_defaulted": "Tempo was missing; used the configured default BPM.",
    "meter_defaulted": "Meter was missing; used the compatibility default 4/4.",
    "polyphony_collapsed": "Overlapping pitch candidates were collapsed to a monophonic stream.",
    "secondary_pitch_omitted": "Secondary concurrent pitches were omitted (monophonic policy).",
    "low_energy_segment_omitted": "Low-energy or unvoiced segments were omitted.",
    "monophonic_ambiguity": "Pitch segmentation was ambiguous; confidence was reduced.",
    "sample_rate_normalized": "Audio was resampled toward a supported rate for the engine.",
    "channels_downmixed": "Multi-channel audio was downmixed to mono for transcription.",
    "audio_not_mono_enough": "Source shows polyphonic energy; monophonic result may be incomplete.",
    "duration_trimmed": "Audio was trimmed to the configured maximum duration.",
    "notes_truncated": "Detected notes were truncated to the configured preview cap.",
}

TranscriptionIssueCode = Literal[
    "tempo_estimated",
    "tempo_defaulted",
    "meter_defaulted",
    "polyphony_collapsed",
    "secondary_pitch_omitted",
    "low_energy_segment_omitted",
    "monophonic_ambiguity",
    "sample_rate_normalized",
    "channels_downmixed",
    "audio_not_mono_enough",
    "duration_trimmed",
    "notes_truncated",
]

# Stable HTTP-mapped error codes.
AUDIO_TRANSCRIPTION_ERROR_CODES: dict[str, str] = {
    "audio_payload_too_large": "Upload exceeded configured audio byte limit.",
    "audio_duration_exceeded": "Audio duration exceeded the configured maximum.",
    "audio_format_unsupported": "Content signature is not an accepted audio format.",
    "audio_malformed_source": "Recognized audio format is malformed or undecodable.",
    "audio_sample_rate_unsupported": "Sample rate exceeds the configured maximum.",
    "audio_engine_unavailable": "Requested or auto-selected transcription engine is unavailable.",
    "audio_empty_upload": "Audio upload is empty.",
    "audio_internal_error": "Unexpected audio transcription failure (sanitized).",
}

AudioTranscriptionErrorCode = Literal[
    "audio_payload_too_large",
    "audio_duration_exceeded",
    "audio_format_unsupported",
    "audio_malformed_source",
    "audio_sample_rate_unsupported",
    "audio_engine_unavailable",
    "audio_empty_upload",
    "audio_internal_error",
]


class AudioTranscriptionError(Exception):
    """Domain error mapped to structured HTTP responses."""

    def __init__(
        self,
        code: AudioTranscriptionErrorCode,
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
            "Audio transcription domain error",
            extra={
                "error_code": code,
                "http_status": http_status,
                "detail_keys": sorted(self.details.keys()),
            },
        )


class TranscriptionPreviewNote(BaseModel):
    """Provisional note — not a composition.v2 event (confidence would be forbidden)."""

    model_config = ConfigDict(extra="forbid")

    provisional_id: str = Field(min_length=1, max_length=64)
    pitch: int = Field(ge=0, le=127)
    start_tick: int = Field(ge=0)
    duration_ticks: int = Field(ge=1)
    velocity: int = Field(ge=1, le=127, default=80)
    confidence: float = Field(ge=0.0, le=1.0)


class TranscriptionPreviewIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: TranscriptionIssueCode
    severity: TranscriptionIssueSeverity = "info"
    message: str = Field(min_length=1, max_length=400)


class TranscriptionPreviewSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note_count: int = Field(ge=0)
    low_confidence_count: int = Field(ge=0)
    excluded_low_confidence_count: int = Field(ge=0)
    include_threshold: float = Field(ge=0.0, le=1.0)


class TranscriptionPreviewTiming(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tempo_bpm: int = Field(ge=20, le=400)
    ticks_per_quarter: int = Field(ge=24, le=9600)
    origin_tick: int = Field(ge=0, default=0)
    tempo_source: TranscriptionTempoSource
    meter: str = Field(default="4/4", min_length=3, max_length=16)


class TranscriptionPreviewEngine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    version: str = Field(default="1", min_length=1, max_length=32)
    fake: bool = False


class TranscriptionPreviewV1(BaseModel):
    """Session-only monophonic transcription preview (non-playable, non-persistent)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["transcription.preview.v1"] = TRANSCRIPTION_PREVIEW_SCHEMA_VERSION
    notes: list[TranscriptionPreviewNote] = Field(default_factory=list, max_length=4096)
    issues: list[TranscriptionPreviewIssue] = Field(default_factory=list, max_length=64)
    summary: TranscriptionPreviewSummary
    timing: TranscriptionPreviewTiming
    engine: TranscriptionPreviewEngine

    @field_validator("notes")
    @classmethod
    def _unique_provisional_ids(
        cls, notes: list[TranscriptionPreviewNote]
    ) -> list[TranscriptionPreviewNote]:
        seen: set[str] = set()
        for note in notes:
            if note.provisional_id in seen:
                raise ValueError(f"duplicate provisional_id: {note.provisional_id}")
            seen.add(note.provisional_id)
        return notes


class AudioTranscriptionRetention(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deleted: bool = True


class AudioTranscriptionResponse(BaseModel):
    """HTTP response — preview only; never includes composition."""

    model_config = ConfigDict(extra="forbid")

    preview: TranscriptionPreviewV1
    engine: TranscriptionPreviewEngine
    retention: AudioTranscriptionRetention = Field(
        default_factory=lambda: AudioTranscriptionRetention(deleted=True)
    )


class AudioTranscriptionErrorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: AudioTranscriptionErrorCode
    message: str = Field(min_length=1, max_length=400)
    details: dict[str, Any] | None = None
