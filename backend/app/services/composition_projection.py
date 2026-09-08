"""Shared projection report contract for Tone / MIDI / MusicXML / WAV renderers."""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


logger = logging.getLogger(__name__)

ProjectionSeverity = Literal["info", "warning", "error"]
ProjectionStatus = Literal["exact", "approximated", "omitted", "failed"]

# Stable issue-code registry consumed by all export tasks.
PROJECTION_ISSUE_CODES: dict[str, str] = {
    "tempo_quantized": "MIDI tempo integer quantization approximated the authored BPM.",
    "automation_sampled": "Linear automation was sampled onto a bounded tick grid.",
    "automation_omitted_from_notation": "Track automation has no MusicXML score notation.",
    "articulation_transformed": "Articulation used deterministic gate/velocity approximation.",
    "key_spelling_unsupported": "Key spelling is unsupported in the target format.",
    "tie_ids_lost": "Semantic tie group IDs cannot be retained in MIDI.",
    "pitch_spelling_lost": "Pitch spelling identity is lost in the target format.",
    "marker_normalized": "Marker text/kind was normalized for the target format.",
    "midi_channel_control_conflict": "Track-local CC streams conflict on a shared MIDI channel.",
    "expression_combined": "Dynamic marks were combined into expression/CC11 values.",
    "sustain_projected": "Sustain spans were projected to CC64 or pedal directions.",
}


class ProjectionIssue(BaseModel):
    """One structured projection diagnostic with a stable code."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(..., min_length=1, max_length=80)
    severity: ProjectionSeverity = "warning"
    status: ProjectionStatus = "approximated"
    message: str = Field(..., min_length=1, max_length=500)
    path: str | None = Field(default=None, max_length=200)
    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("code")
    @classmethod
    def validate_known_or_custom_code(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("projection issue code must not be empty")
        return normalized

    @field_validator("details")
    @classmethod
    def validate_bounded_details(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(value) > 16:
            raise ValueError("projection issue details are limited to 16 keys")
        # Keep details count-based / scalar; reject large nested payloads.
        for key, item in value.items():
            if isinstance(item, (list, dict)) and len(item) > 32:
                raise ValueError(f"projection issue detail {key!r} exceeds bounded size")
            if isinstance(item, str) and len(item) > 200:
                raise ValueError(f"projection issue detail {key!r} exceeds bounded length")
        return value


class ProjectionReport(BaseModel):
    """Renderer-agnostic projection outcome carried with each export artifact."""

    model_config = ConfigDict(extra="forbid")

    status: ProjectionStatus = "exact"
    issues: list[ProjectionIssue] = Field(default_factory=list)
    exact_count: int = Field(default=0, ge=0)
    approximated_count: int = Field(default=0, ge=0)
    omitted_count: int = Field(default=0, ge=0)
    failed_count: int = Field(default=0, ge=0)

    def add_issue(
        self,
        *,
        code: str,
        severity: ProjectionSeverity = "warning",
        status: ProjectionStatus = "approximated",
        message: str | None = None,
        path: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> ProjectionIssue:
        issue = ProjectionIssue(
            code=code,
            severity=severity,
            status=status,
            message=message or PROJECTION_ISSUE_CODES.get(code, code),
            path=path,
            details=details or {},
        )
        self.issues.append(issue)
        if status == "exact":
            self.exact_count += 1
        elif status == "approximated":
            self.approximated_count += 1
        elif status == "omitted":
            self.omitted_count += 1
        else:
            self.failed_count += 1
        self._refresh_status()
        logger.debug(
            "Projection issue recorded",
            extra={
                "code": issue.code,
                "severity": issue.severity,
                "status": issue.status,
                "path": issue.path,
                "issue_count": len(self.issues),
            },
        )
        return issue

    def _refresh_status(self) -> None:
        if self.failed_count:
            self.status = "failed"
        elif self.omitted_count:
            self.status = "omitted" if not self.approximated_count else "approximated"
        elif self.approximated_count:
            self.status = "approximated"
        else:
            self.status = "exact"

    def compact_codes(self) -> list[str]:
        return sorted({issue.code for issue in self.issues})

    def summary_extra(self) -> dict[str, Any]:
        return {
            "projection_status": self.status,
            "exact_count": self.exact_count,
            "approximated_count": self.approximated_count,
            "omitted_count": self.omitted_count,
            "failed_count": self.failed_count,
            "issue_codes": self.compact_codes(),
        }


def empty_projection_report() -> ProjectionReport:
    report = ProjectionReport(status="exact", exact_count=0)
    logger.debug("Created empty projection report", extra=report.summary_extra())
    return report


PROJECTION_EXPOSE_HEADERS: tuple[str, ...] = (
    "X-Mukit-Projection-Status",
    "X-Mukit-Projection-Issues",
    "X-Mukit-Projection-Exact-Count",
    "X-Mukit-Projection-Approximated-Count",
    "X-Mukit-Projection-Omitted-Count",
    "X-Mukit-Projection-Failed-Count",
)

_MAX_PROJECTION_ISSUES_HEADER = 512


def projection_response_headers(report: ProjectionReport) -> dict[str, str]:
    """Build bounded export response headers from a projection report."""
    issues_value = ",".join(report.compact_codes())
    if len(issues_value) > _MAX_PROJECTION_ISSUES_HEADER:
        issues_value = issues_value[: _MAX_PROJECTION_ISSUES_HEADER - 3] + "..."
    return {
        "X-Mukit-Projection-Status": report.status,
        "X-Mukit-Projection-Issues": issues_value,
        "X-Mukit-Projection-Exact-Count": str(report.exact_count),
        "X-Mukit-Projection-Approximated-Count": str(report.approximated_count),
        "X-Mukit-Projection-Omitted-Count": str(report.omitted_count),
        "X-Mukit-Projection-Failed-Count": str(report.failed_count),
    }


def projection_issues_as_warnings(report: ProjectionReport) -> list[str]:
    """Convert structured projection issues into legacy warning strings."""
    return [f"{issue.code}: {issue.message}" for issue in report.issues]
