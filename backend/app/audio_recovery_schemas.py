"""Strict audio.recovery.*.v1 DTOs, issue/error codes, and domain errors.

Recovery previews are session-scoped and non-playable. Confidence and stem
provenance live on preview/result assets — never on composition.v2 note events
(``extra=\"forbid\"``). Durable assets are written only via Bind with
``project_id``. Harmony/structure candidates are metadata only and never invent
playable notes.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)


logger = logging.getLogger(__name__)

AUDIO_RECOVERY_PREVIEW_SCHEMA_VERSION: Literal["audio.recovery.preview.v1"] = (
    "audio.recovery.preview.v1"
)
AUDIO_RECOVERY_RESULT_SCHEMA_VERSION: Literal["audio.recovery.result.v1"] = (
    "audio.recovery.result.v1"
)
AUDIO_RECOVERY_JOB_SCHEMA_VERSION: Literal["audio.recovery.job.v1"] = (
    "audio.recovery.job.v1"
)
AUDIO_RECOVERY_BIND_SCHEMA_VERSION: Literal["audio.recovery.bind.v1"] = (
    "audio.recovery.bind.v1"
)
AUDIO_RECOVERY_STEM_SCHEMA_VERSION: Literal["audio.recovery.stem.v1"] = (
    "audio.recovery.stem.v1"
)

AudioRecoveryStemRole = Literal[
    "vocals",
    "melody",
    "bass",
    "drums",
    "harmonic",
    "other",
]
AUDIO_RECOVERY_STEM_ROLES: frozenset[str] = frozenset(
    {"vocals", "melody", "bass", "drums", "harmonic", "other"}
)

AudioRecoveryIssueSeverity = Literal["info", "warning"]
AudioRecoveryTempoSource = Literal["provided", "estimated", "defaulted"]
AudioRecoveryJobStatus = Literal["queued", "running", "failed", "complete"]
AudioRecoveryOverlayStatus = Literal["recovered", "user_edited", "excluded", "user"]
AudioRecoverySeparationStatus = Literal[
    "skipped_mono",
    "unavailable",
    "partial",
    "complete",
    "off",
]

# Closed stable issue-code registry (preview diagnostics; not HTTP errors).
AUDIO_RECOVERY_ISSUE_CODES: dict[str, str] = {
    "tempo_estimated": "Tempo was estimated from the audio signal.",
    "tempo_defaulted": "Tempo was missing; used the configured default BPM.",
    "meter_defaulted": "Meter was missing; used the compatibility default 4/4.",
    "separation_skipped_mono": "Source looked mono-enough; separation was skipped.",
    "separation_unavailable": "Separation engine unavailable; combined path used.",
    "separation_partial": "Separation produced an incomplete or ambiguous stem set.",
    "melody_derived_from_vocals": "Melody destination was derived from the vocals stem.",
    "drums_transcription_deferred": "Drum pitched transcription was deferred for this job.",
    "polyphony_collapsed": "Overlapping pitch candidates were collapsed where policy requires.",
    "low_energy_segment_omitted": "Low-energy or unvoiced segments were omitted.",
    "sample_rate_normalized": "Audio was resampled toward a supported rate for the engine.",
    "channels_downmixed": "Multi-channel audio was downmixed for analysis.",
    "duration_trimmed": "Audio was trimmed to the configured maximum duration.",
    "notes_truncated": "Detected notes were truncated to the configured preview cap.",
    "scaffolding_low_confidence": "One or more scaffolding estimates are below threshold.",
    "harmony_omitted_low_confidence": "Low-confidence harmony spans were omitted by default.",
    "combined_path_degraded": "Combined transcription ran with reduced confidence.",
    "stem_empty": "A stem produced no note events (not fabricated).",
}

AudioRecoveryIssueCode = Literal[
    "tempo_estimated",
    "tempo_defaulted",
    "meter_defaulted",
    "separation_skipped_mono",
    "separation_unavailable",
    "separation_partial",
    "melody_derived_from_vocals",
    "drums_transcription_deferred",
    "polyphony_collapsed",
    "low_energy_segment_omitted",
    "sample_rate_normalized",
    "channels_downmixed",
    "duration_trimmed",
    "notes_truncated",
    "scaffolding_low_confidence",
    "harmony_omitted_low_confidence",
    "combined_path_degraded",
    "stem_empty",
]

# Stable HTTP-mapped error codes (reuse audio_* where identical; add audio_recovery_*).
AUDIO_RECOVERY_ERROR_CODES: dict[str, str] = {
    "audio_payload_too_large": "Upload exceeded configured audio byte limit.",
    "audio_duration_exceeded": "Audio duration exceeded the configured maximum.",
    "audio_format_unsupported": "Content signature is not an accepted audio format.",
    "audio_malformed_source": "Recognized audio format is malformed or undecodable.",
    "audio_sample_rate_unsupported": "Sample rate exceeds the configured maximum.",
    "audio_empty_upload": "Audio upload is empty.",
    "audio_recovery_engine_unavailable": "Requested or auto-selected recovery engine is unavailable.",
    "audio_recovery_quota_exceeded": "Per-project job or asset quota exceeded.",
    "audio_recovery_job_not_found": "Audio recovery job was not found.",
    "audio_recovery_job_not_ready": "Bind/download requires a complete recovery job.",
    "audio_recovery_project_required": "Bind requires a project_id.",
    "audio_recovery_bind_fingerprint_mismatch": "Preview fingerprint does not match the job.",
    "audio_recovery_invalid_request": "Recovery request failed validation.",
    "audio_recovery_job_timeout": "Recovery job exceeded the configured timeout.",
    "audio_recovery_internal_error": "Unexpected audio recovery failure (sanitized).",
    "audio_recovery_asset_not_found": "Recovery asset was not found.",
}

AudioRecoveryErrorCode = Literal[
    "audio_payload_too_large",
    "audio_duration_exceeded",
    "audio_format_unsupported",
    "audio_malformed_source",
    "audio_sample_rate_unsupported",
    "audio_empty_upload",
    "audio_recovery_engine_unavailable",
    "audio_recovery_quota_exceeded",
    "audio_recovery_job_not_found",
    "audio_recovery_job_not_ready",
    "audio_recovery_project_required",
    "audio_recovery_bind_fingerprint_mismatch",
    "audio_recovery_invalid_request",
    "audio_recovery_job_timeout",
    "audio_recovery_internal_error",
    "audio_recovery_asset_not_found",
]


class AudioRecoveryError(Exception):
    """Domain error mapped to structured HTTP responses."""

    def __init__(
        self,
        code: AudioRecoveryErrorCode,
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
            "Audio recovery domain error",
            extra={
                "error_code": code,
                "http_status": http_status,
                "detail_keys": sorted(self.details.keys()),
            },
        )


class AudioRecoveryIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: AudioRecoveryIssueCode
    severity: AudioRecoveryIssueSeverity = "info"
    message: str = Field(min_length=1, max_length=400)
    stem: AudioRecoveryStemRole | None = None


class AudioRecoveryPreviewNote(BaseModel):
    """Provisional note — not a composition.v2 event (confidence would be forbidden)."""

    model_config = ConfigDict(extra="forbid")

    provisional_id: str = Field(min_length=1, max_length=64)
    stem: AudioRecoveryStemRole
    pitch: int = Field(ge=0, le=127)
    start_tick: int = Field(ge=0)
    duration_ticks: int = Field(ge=1)
    velocity: int = Field(ge=1, le=127, default=80)
    confidence: float = Field(ge=0.0, le=1.0)
    polyphony_group_id: str | None = Field(default=None, min_length=1, max_length=64)


class AudioRecoveryStemV1(BaseModel):
    """Per-stem notes + engine + issues (embedded in preview)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["audio.recovery.stem.v1"] = AUDIO_RECOVERY_STEM_SCHEMA_VERSION
    stem: AudioRecoveryStemRole
    engine_id: str = Field(min_length=1, max_length=64)
    notes: list[AudioRecoveryPreviewNote] = Field(default_factory=list, max_length=4096)
    issues: list[AudioRecoveryIssue] = Field(default_factory=list, max_length=64)

    @field_validator("notes")
    @classmethod
    def _notes_match_stem(
        cls, notes: list[AudioRecoveryPreviewNote], info: ValidationInfo
    ) -> list[AudioRecoveryPreviewNote]:
        stem = info.data.get("stem")
        if stem is None:
            return notes
        for note in notes:
            if note.stem != stem:
                raise ValueError(f"note stem {note.stem!r} does not match stem {stem!r}")
        return notes


class AudioRecoveryBeatGrid(BaseModel):
    model_config = ConfigDict(extra="forbid")

    downbeat_offset_seconds: float = Field(ge=0.0, default=0.0)
    ticks_per_quarter: int = Field(ge=24, le=9600)
    confidence: float = Field(ge=0.0, le=1.0)


class AudioRecoveryStructureSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=64)
    start_tick: int = Field(ge=0)
    end_tick: int = Field(ge=1)
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _end_after_start(self) -> AudioRecoveryStructureSection:
        if self.end_tick <= self.start_tick:
            raise ValueError("end_tick must be greater than start_tick")
        return self


class AudioRecoveryKeyEstimate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tonic: str = Field(min_length=1, max_length=8)
    mode: str = Field(min_length=1, max_length=16)
    confidence: float = Field(ge=0.0, le=1.0)


class AudioRecoveryHarmonySpan(BaseModel):
    """Harmony candidate — metadata only; never invents playable notes."""

    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1, max_length=32)
    start_tick: int = Field(ge=0)
    end_tick: int = Field(ge=1)
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _end_after_start(self) -> AudioRecoveryHarmonySpan:
        if self.end_tick <= self.start_tick:
            raise ValueError("end_tick must be greater than start_tick")
        return self


class AudioRecoveryScaffolding(BaseModel):
    """Estimated tempo / beat / structure / key / harmony (non-playable)."""

    model_config = ConfigDict(extra="forbid")

    tempo_bpm: int = Field(ge=20, le=400)
    tempo_confidence: float = Field(ge=0.0, le=1.0)
    tempo_source: AudioRecoveryTempoSource
    meter: str = Field(default="4/4", min_length=3, max_length=16)
    beat_grid: AudioRecoveryBeatGrid
    structure: list[AudioRecoveryStructureSection] = Field(
        default_factory=list, max_length=64
    )
    key: AudioRecoveryKeyEstimate | None = None
    harmony: list[AudioRecoveryHarmonySpan] = Field(default_factory=list, max_length=256)


class AudioRecoveryPreviewSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note_count: int = Field(ge=0)
    low_confidence_count: int = Field(ge=0)
    excluded_low_confidence_count: int = Field(ge=0)
    include_threshold: float = Field(ge=0.0, le=1.0)
    stem_count: int = Field(ge=0)
    separation_status: AudioRecoverySeparationStatus


class AudioRecoveryPreviewEngine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    separation_engine_id: str | None = Field(default=None, min_length=1, max_length=64)
    version: str = Field(default="1", min_length=1, max_length=32)
    fake: bool = False


class AudioRecoveryPreviewV1(BaseModel):
    """Session-only mixed-audio recovery preview (non-playable, non-persistent)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["audio.recovery.preview.v1"] = (
        AUDIO_RECOVERY_PREVIEW_SCHEMA_VERSION
    )
    preview_fingerprint: str = Field(min_length=8, max_length=128)
    stems: list[AudioRecoveryStemV1] = Field(default_factory=list, max_length=16)
    notes: list[AudioRecoveryPreviewNote] = Field(default_factory=list, max_length=8192)
    scaffolding: AudioRecoveryScaffolding
    issues: list[AudioRecoveryIssue] = Field(default_factory=list, max_length=128)
    summary: AudioRecoveryPreviewSummary
    engine: AudioRecoveryPreviewEngine
    # Explicit contract reminder for API consumers / UI.
    playable: Literal[False] = False

    @field_validator("notes")
    @classmethod
    def _unique_provisional_ids(
        cls, notes: list[AudioRecoveryPreviewNote]
    ) -> list[AudioRecoveryPreviewNote]:
        seen: set[str] = set()
        for note in notes:
            if note.provisional_id in seen:
                raise ValueError(f"duplicate provisional_id: {note.provisional_id}")
            seen.add(note.provisional_id)
        return notes

    @field_validator("stems")
    @classmethod
    def _unique_stem_roles(
        cls, stems: list[AudioRecoveryStemV1]
    ) -> list[AudioRecoveryStemV1]:
        seen: set[str] = set()
        for stem in stems:
            if stem.stem in seen:
                raise ValueError(f"duplicate stem role: {stem.stem}")
            seen.add(stem.stem)
        return stems


class AudioRecoveryOverlayEntry(BaseModel):
    """Durable confidence/stem linkage keyed by composition.v2 event_id after Bind."""

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1, max_length=64)
    track_id: str = Field(min_length=1, max_length=64)
    confidence: float = Field(ge=0.0, le=1.0)
    stem: AudioRecoveryStemRole
    provisional_id: str | None = Field(default=None, min_length=1, max_length=64)
    status: AudioRecoveryOverlayStatus = "recovered"


class AudioRecoveryResultV1(BaseModel):
    """Durable related asset — confidence overlay + scaffolding snapshot; not a score."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["audio.recovery.result.v1"] = (
        AUDIO_RECOVERY_RESULT_SCHEMA_VERSION
    )
    job_id: str = Field(min_length=1, max_length=64)
    project_id: str = Field(min_length=1, max_length=64)
    preview_fingerprint: str = Field(min_length=8, max_length=128)
    source_audio_asset_id: str = Field(min_length=1, max_length=64)
    scaffolding: AudioRecoveryScaffolding
    overlay: list[AudioRecoveryOverlayEntry] = Field(default_factory=list, max_length=8192)
    issues: list[AudioRecoveryIssue] = Field(default_factory=list, max_length=128)
    engine: AudioRecoveryPreviewEngine
    # Explicit: never a second playable composition.
    playable: Literal[False] = False

    @field_validator("overlay")
    @classmethod
    def _unique_event_ids(
        cls, overlay: list[AudioRecoveryOverlayEntry]
    ) -> list[AudioRecoveryOverlayEntry]:
        seen: set[str] = set()
        for entry in overlay:
            if entry.event_id in seen:
                raise ValueError(f"duplicate event_id in overlay: {entry.event_id}")
            seen.add(entry.event_id)
        return overlay


class AudioRecoveryJobV1(BaseModel):
    """Job status envelope — never includes PCM bytes."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["audio.recovery.job.v1"] = AUDIO_RECOVERY_JOB_SCHEMA_VERSION
    id: str = Field(min_length=1, max_length=64)
    project_id: str | None = Field(default=None, min_length=1, max_length=64)
    status: AudioRecoveryJobStatus
    engine_id: str = Field(min_length=1, max_length=64)
    separation_engine_id: str | None = Field(default=None, min_length=1, max_length=64)
    fake: bool = False
    preview_fingerprint: str | None = Field(default=None, min_length=8, max_length=128)
    preview: AudioRecoveryPreviewV1 | None = None
    error_code: str | None = None
    error_message: str | None = None
    source_sha256_prefix: str | None = Field(default=None, min_length=8, max_length=16)
    source_byte_size: int | None = Field(default=None, ge=0)
    bound: bool = False
    source_audio_asset_id: str | None = None
    result_asset_id: str | None = None
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    # Explicit contract reminder.
    mutates_composition: Literal[False] = False


class AudioRecoveryBindEventMapEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provisional_id: str = Field(min_length=1, max_length=64)
    event_id: str = Field(min_length=1, max_length=64)
    track_id: str = Field(min_length=1, max_length=64)


class AudioRecoveryBindInstallFlags(BaseModel):
    model_config = ConfigDict(extra="forbid")

    install_tempo: bool = True
    install_sections: bool = True
    install_harmony: bool = False
    include_low_confidence_harmony: bool = False


class AudioRecoveryBindRequestV1(BaseModel):
    """Bind request — requires project_id; persists source audio + result overlay."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["audio.recovery.bind.v1"] = AUDIO_RECOVERY_BIND_SCHEMA_VERSION
    project_id: str = Field(min_length=1, max_length=64)
    preview_fingerprint: str = Field(min_length=8, max_length=128)
    event_map: list[AudioRecoveryBindEventMapEntry] = Field(
        default_factory=list, max_length=8192
    )
    install_flags: AudioRecoveryBindInstallFlags = Field(
        default_factory=AudioRecoveryBindInstallFlags
    )

    @field_validator("event_map")
    @classmethod
    def _unique_provisional_and_event_ids(
        cls, event_map: list[AudioRecoveryBindEventMapEntry]
    ) -> list[AudioRecoveryBindEventMapEntry]:
        prov: set[str] = set()
        ev: set[str] = set()
        for entry in event_map:
            if entry.provisional_id in prov:
                raise ValueError(f"duplicate provisional_id: {entry.provisional_id}")
            if entry.event_id in ev:
                raise ValueError(f"duplicate event_id: {entry.event_id}")
            prov.add(entry.provisional_id)
            ev.add(entry.event_id)
        return event_map


class AudioRecoveryBindResponseV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["audio.recovery.bind.v1"] = AUDIO_RECOVERY_BIND_SCHEMA_VERSION
    job_id: str
    project_id: str
    source_audio_asset_id: str
    result_asset_id: str
    overlay_entry_count: int = Field(ge=0)


class AudioRecoveryAssetMeta(BaseModel):
    """Metadata only — never PCM."""

    model_config = ConfigDict(extra="forbid")

    id: str
    project_id: str
    job_id: str
    kind: Literal["source_audio", "result_json"]
    content_type: str
    byte_size: int = Field(ge=0)
    sha256_prefix: str = Field(min_length=8, max_length=16)
    created_at: str


class AudioRecoveryErrorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: AudioRecoveryErrorCode
    message: str = Field(min_length=1, max_length=400)
    details: dict[str, Any] = Field(default_factory=dict)
