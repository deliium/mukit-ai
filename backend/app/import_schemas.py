"""Strict MIDI/MusicXML import DTOs, issue codes, and domain errors.

Import diagnostics are session-scoped and distinct from export ProjectionReport.
Canonical CompositionV2 never stores import warnings or source bytes.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .composition_schemas import CompositionV2


logger = logging.getLogger(__name__)

ImportStatus = Literal["exact", "approximated", "partial"]
ImportIssueSeverity = Literal["info", "warning"]
ImportIssueAction = Literal["defaulted", "normalized", "quantized", "omitted"]

# Closed stable issue-code registry shared by backend tests and frontend rendering.
IMPORT_ISSUE_CODES: dict[str, str] = {
    "tempo_defaulted": "Source tempo was missing; used the compatibility default BPM.",
    "tempo_rounded": "Source tempo was rounded to the nearest representable integer BPM.",
    "meter_defaulted": "Source meter was missing; used the compatibility default time signature.",
    "key_defaulted": "Source key was missing; used the compatibility default key (not inferred analysis).",
    "pitch_spelling_inferred": "Pitch spelling was inferred because the source lacked enharmonic identity.",
    "partial_measure_padded": "Composition duration was padded to a complete final bar.",
    "pickup_normalized": "Pickup/partial opening measure was normalized into the complete-bar timeline.",
    "timing_quantized": "Event timing was quantized to the target PPQ grid.",
    "ppq_rescaled": "Source timing resolution was rescaled to the target PPQ.",
    "program_change_split_track": "A program change forced a deterministic track split.",
    "instrument_defaulted": "Instrument identity was defaulted because source metadata was insufficient.",
    "role_inferred": "Track role was inferred from high-confidence source metadata.",
    "role_defaulted": "Track role was set to the neutral import fallback.",
    "section_defaulted": "Sections were set to a single neutral full-score section.",
    "repeat_expanded": "Repeats were expanded into linear playback order.",
    "transposition_normalized": "Transposing instruments were normalized to concert pitch.",
    "unsupported_midi_event_omitted": "Unsupported MIDI events were omitted.",
    "unsupported_notation_omitted": "Unsupported MusicXML notation constructs were omitted.",
    "grace_note_omitted": "Grace or cue notes were omitted.",
    "dangling_note_omitted": "Unmatched or dangling note events were closed or dropped.",
    "source_id_collision": "A deterministic ID collision required a stable suffix.",
}

ImportIssueCode = Literal[
    "tempo_defaulted",
    "tempo_rounded",
    "meter_defaulted",
    "key_defaulted",
    "pitch_spelling_inferred",
    "partial_measure_padded",
    "pickup_normalized",
    "timing_quantized",
    "ppq_rescaled",
    "program_change_split_track",
    "instrument_defaulted",
    "role_inferred",
    "role_defaulted",
    "section_defaulted",
    "repeat_expanded",
    "transposition_normalized",
    "unsupported_midi_event_omitted",
    "unsupported_notation_omitted",
    "grace_note_omitted",
    "dangling_note_omitted",
    "source_id_collision",
]

# Stable HTTP-mapped error codes (endpoint failures; not successful report status).
IMPORT_ERROR_CODES: dict[str, str] = {
    "import_payload_too_large": "Upload or expanded archive exceeded configured import limits.",
    "import_unsupported_media_type": "Content signature does not match the selected import endpoint.",
    "import_malformed_source": "Recognized file format is malformed or truncated.",
    "import_non_representable": "Source cannot be represented as composition.v2 without relocating events.",
    "import_complexity_exceeded": "Source exceeded configured track, note, bar, or metadata limits.",
    "import_dependency_unavailable": "Required import parser dependency is unavailable.",
    "import_internal_error": "Unexpected import failure (sanitized).",
}

ImportErrorCode = Literal[
    "import_payload_too_large",
    "import_unsupported_media_type",
    "import_malformed_source",
    "import_non_representable",
    "import_complexity_exceeded",
    "import_dependency_unavailable",
    "import_internal_error",
]

ImportFormat = Literal["midi", "musicxml", "mxl"]


class CompositionImportError(Exception):
    """Domain error for import failures mapped to structured HTTP responses."""

    def __init__(
        self,
        code: ImportErrorCode,
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
            "Composition import domain error",
            extra={
                "error_code": code,
                "http_status": http_status,
                "detail_keys": sorted(self.details.keys()),
            },
        )


class ImportSourceLocator(BaseModel):
    """Bounded sanitized locator; never carries source bytes or freeform lyrics."""

    model_config = ConfigDict(extra="forbid")

    track_index: int | None = Field(default=None, ge=0)
    part_index: int | None = Field(default=None, ge=0)
    measure_index: int | None = Field(default=None, ge=1)
    channel: int | None = Field(default=None, ge=1, le=16)
    tick: int | None = Field(default=None, ge=0)


class ImportIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: ImportIssueCode
    severity: ImportIssueSeverity = "warning"
    action: ImportIssueAction
    message: str = Field(..., min_length=1, max_length=500)
    count: int = Field(default=1, ge=1)
    locator: ImportSourceLocator | None = None
    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("import issue message must not be empty")
        return normalized

    @field_validator("details")
    @classmethod
    def validate_bounded_details(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(value) > 16:
            raise ValueError("import issue details are limited to 16 keys")
        for key, item in value.items():
            if isinstance(item, (list, dict)) and len(item) > 32:
                raise ValueError(f"import issue detail {key!r} exceeds bounded size")
            if isinstance(item, str) and len(item) > 200:
                raise ValueError(f"import issue detail {key!r} exceeds bounded length")
        return value


class ImportSourceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detected_format: ImportFormat
    display_filename: str = Field(..., min_length=1, max_length=120)
    input_bytes: int = Field(..., ge=0)
    source_ppq: int | None = Field(default=None, ge=1)
    source_divisions: int | None = Field(default=None, ge=1)
    target_ppq: int = Field(..., ge=1)
    source_track_count: int = Field(..., ge=0)
    result_track_count: int = Field(..., ge=0)
    source_note_count: int = Field(..., ge=0)
    result_note_count: int = Field(..., ge=0)
    bar_count: int = Field(..., ge=0)
    duration_ticks: int = Field(..., ge=0)
    tempo_change_count: int = Field(default=0, ge=0)
    time_signature_change_count: int = Field(default=0, ge=0)
    key_change_count: int = Field(default=0, ge=0)
    marker_count: int = Field(default=0, ge=0)
    issue_counts_by_action: dict[str, int] = Field(default_factory=dict)

    @field_validator("display_filename")
    @classmethod
    def validate_display_filename(cls, value: str) -> str:
        # Keep basename-like display names only; strip path separators.
        sanitized = value.replace("\\", "/").split("/")[-1].strip()
        if not sanitized:
            raise ValueError("display_filename must not be empty after sanitization")
        return sanitized[:120]

    @field_validator("issue_counts_by_action")
    @classmethod
    def validate_issue_counts(cls, value: dict[str, int]) -> dict[str, int]:
        allowed = {"defaulted", "normalized", "quantized", "omitted"}
        for key, count in value.items():
            if key not in allowed:
                raise ValueError(f"unknown issue action count key: {key}")
            if not isinstance(count, int) or count < 0:
                raise ValueError(f"issue count for {key!r} must be a non-negative int")
        return value


class ImportReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ImportStatus = "exact"
    issues: list[ImportIssue] = Field(default_factory=list)
    summary: ImportSourceSummary

    def add_issue(
        self,
        *,
        code: ImportIssueCode,
        action: ImportIssueAction,
        severity: ImportIssueSeverity = "warning",
        message: str | None = None,
        count: int = 1,
        locator: ImportSourceLocator | None = None,
        details: dict[str, Any] | None = None,
    ) -> ImportIssue:
        issue = ImportIssue(
            code=code,
            severity=severity,
            action=action,
            message=message or IMPORT_ISSUE_CODES[code],
            count=count,
            locator=locator,
            details=details or {},
        )
        self.issues.append(issue)
        action_counts = dict(self.summary.issue_counts_by_action)
        action_counts[action] = action_counts.get(action, 0) + count
        self.summary.issue_counts_by_action = action_counts
        self._refresh_status()
        logger.debug(
            "Import issue recorded",
            extra={
                "code": issue.code,
                "action": issue.action,
                "severity": issue.severity,
                "count": issue.count,
                "issue_total": len(self.issues),
            },
        )
        return issue

    def _refresh_status(self) -> None:
        if not self.issues:
            self.status = "exact"
            return
        omitted = self.summary.issue_counts_by_action.get("omitted", 0)
        if omitted > 0:
            self.status = "partial"
            return
        self.status = "approximated"


class CompositionImportResponse(BaseModel):
    """Successful import payload: canonical V2, regenerated MusicXML, and report."""

    model_config = ConfigDict(extra="forbid")

    composition: CompositionV2
    musicxml: str = Field(..., min_length=1)
    import_report: ImportReport


def empty_import_report(*, summary: ImportSourceSummary) -> ImportReport:
    report = ImportReport(summary=summary)
    logger.debug(
        "Import report created",
        extra={
            "detected_format": summary.detected_format,
            "input_bytes": summary.input_bytes,
            "target_ppq": summary.target_ppq,
        },
    )
    return report
