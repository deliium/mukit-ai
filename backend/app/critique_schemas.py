"""Strict critique / evaluation DTOs (``agent.critique.v1`` findings + scopes).

Evaluation engine judgment reports are derived and never playable. Canonical notes
remain on ``composition.v2`` ``tracks[].events[]``. Analysis metrics stay in
``composition.analysis.v1``; this module owns structured Critique findings.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

logger = logging.getLogger(__name__)

# Shared with plan / artifact schemas — never allow competing playable blobs.
_FORBIDDEN_PLAYABLE_KEYS: frozenset[str] = frozenset(
    {
        "tracks",
        "musicxml",
        "midi",
        "wav",
        "analysis",
        "composition",
        "music",
        "events",
        "note_events",
        "notes",
    }
)

# --- Contract identity / caps --------------------------------------------------

CRITIQUE_ENGINE_VERSION = "critique.engine.v1"
CRITIQUE_FINDING_MAX = 64
CRITIQUE_FINDING_EXPLANATION_MAX = 500
CRITIQUE_FINDING_ACTION_MAX = 500
CRITIQUE_FINDING_CODE_MAX = 80
CRITIQUE_FINDING_TRACK_MAX = 16
CRITIQUE_EVIDENCE_REF_MAX = 16
CRITIQUE_EVIDENCE_REF_LEN = 120
CRITIQUE_EVIDENCE_METRIC_MAX = 24
CRITIQUE_EVIDENCE_LOCATOR_MAX = 8
CRITIQUE_EVIDENCE_JSON_MAX_BYTES = 2_048
CRITIQUE_SCOPE_ID_MAX = 120

CritiqueSeverity = Literal["info", "warning", "error"]
CritiqueStratum = Literal[
    "hard_constraint",
    "technical",
    "stylistic",
    "subjective",
]
CritiqueFindingCategory = Literal[
    "structure",
    "tonality",
    "harmony",
    "cadence",
    "melody",
    "motif",
    "rhythm",
    "density",
    "orchestration",
    "instrumentation",
    "collision",
    "duplication",
    "contrast",
    "dynamics",
    "tension",
    "other",
]
CritiqueModelStatus = Literal["skipped", "ok", "failed", "unavailable"]
CritiqueScopeKind = Literal["composition", "section", "track", "bars"]

_FINDING_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,79}$")

# Stable starter registry (Task 3 expands usage; codes are machine ids).
CRITIQUE_FINDING_CODES: dict[str, str] = {
    "climax_lacks_contrast": (
        "Requested climax section has nearly identical density/dynamics to the preceding section."
    ),
    "requested_structure_mismatch": "Composition sections do not match requested structure.",
    "requested_key_mismatch": "Composition key does not match requested constraints.",
    "requested_instrumentation_mismatch": (
        "Track instrumentation does not match requested constraints."
    ),
    "analysis_failed": "Deterministic analysis could not produce a usable report.",
    "note_outside_instrument_range": "Notes fall outside practical instrument range.",
    "dense_overlapping_material": "Concurrent note load exceeded density threshold.",
    "overlapping_same_pitch_timing": "Same-pitch notes overlap heavily within a track.",
    "excessive_duplicate_notes": "Exact duplicate notes exceeded the warning threshold.",
    "declared_key_conflicts_with_inference": "Declared key conflicts with inferred tonality.",
    "declared_harmony_conflicts_with_inference": (
        "Declared harmony conflicts with inferred chord spans."
    ),
    "section_lacks_contrast": "Adjacent sections show little density/dynamics contrast.",
    "melodic_contour_flat": "Melodic contour is unusually flat across the scoped range.",
    "motif_recurrence_missing": "Requested motif recurrence was not observed.",
    "rhythmic_diversity_low": "Attack/IOI diversity is unusually low.",
    "orchestration_density_high": "Orchestration simultaneity / role overlap is high.",
    "tension_curve_flat": "Tension/dynamics curve lacks shape across sections.",
    "cadence_weak": "Cadence indicators are weak or absent at section boundaries.",
    "model_subjective_observation": "Model-based subjective observation with concrete locus.",
}

# Domain error codes (Task 5) — map to HTTP in errors helper.
CRITIQUE_INVALID_COMPOSITION = "critique_invalid_composition"
CRITIQUE_INVALID_SCOPE = "critique_invalid_scope"
CRITIQUE_COMPLEXITY_EXCEEDED = "critique_complexity_exceeded"
CRITIQUE_MODEL_UNAVAILABLE = "critique_model_unavailable"
CRITIQUE_PAYLOAD_REJECTED = "critique_payload_rejected"

CRITIQUE_ERROR_HTTP: dict[str, int] = {
    CRITIQUE_INVALID_COMPOSITION: 422,
    CRITIQUE_INVALID_SCOPE: 422,
    CRITIQUE_COMPLEXITY_EXCEEDED: 422,
    CRITIQUE_MODEL_UNAVAILABLE: 503,
    CRITIQUE_PAYLOAD_REJECTED: 422,
}


class CritiqueError(Exception):
    """Domain error for critique evaluate paths — sanitized client detail only."""

    code: str = "critique_error"
    http_status: int = 422

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        http_status: int | None = None,
        details: dict[str, Any] | None = None,
        fingerprint_prefix: str | None = None,
    ) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code
        if http_status is not None:
            self.http_status = http_status
        else:
            self.http_status = CRITIQUE_ERROR_HTTP.get(self.code, self.http_status)
        self.details = details or {}
        self.fingerprint_prefix = fingerprint_prefix
        logger.warning(
            "Critique domain error",
            extra={
                "code": self.code,
                "fingerprint_prefix": (fingerprint_prefix or "")[:12] or None,
            },
        )


def map_critique_error_to_http(exc: CritiqueError) -> tuple[int, dict[str, Any]]:
    """Return (status, sanitized detail dict) for HTTPException."""
    detail: dict[str, Any] = {
        "code": exc.code,
        "message": str(exc)[:240],
    }
    if exc.details:
        safe = {
            key: value
            for key, value in exc.details.items()
            if isinstance(value, (str, int, float, bool)) or value is None
        }
        if safe:
            detail["details"] = safe
    return exc.http_status, detail


# --- Finding evidence / range --------------------------------------------------


class CritiqueAffectedRange(BaseModel):
    """Inclusive bar / optional tick window for a finding."""

    model_config = ConfigDict(extra="forbid")

    start_bar: int | None = Field(default=None, ge=1)
    end_bar: int | None = Field(default=None, ge=1)
    start_tick: int | None = Field(default=None, ge=0)
    end_tick: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _bounds(self) -> CritiqueAffectedRange:
        if (
            self.start_bar is not None
            and self.end_bar is not None
            and self.end_bar < self.start_bar
        ):
            raise ValueError("affected_range end_bar must be >= start_bar")
        if (
            self.start_tick is not None
            and self.end_tick is not None
            and self.end_tick < self.start_tick
        ):
            raise ValueError("affected_range end_tick must be >= start_tick")
        return self


class CritiqueFindingEvidence(BaseModel):
    """Compact evidence — metrics / refs / locators; never playable event arrays."""

    model_config = ConfigDict(extra="forbid")

    metrics: dict[str, float | int | str | bool | None] = Field(default_factory=dict)
    locators: list[dict[str, Any]] = Field(default_factory=list)
    refs: list[str] = Field(default_factory=list)

    @field_validator("metrics")
    @classmethod
    def _cap_metrics(cls, value: dict[str, Any]) -> dict[str, float | int | str | bool | None]:
        if len(value) > CRITIQUE_EVIDENCE_METRIC_MAX:
            raise ValueError(f"evidence.metrics limited to {CRITIQUE_EVIDENCE_METRIC_MAX}")
        forbidden = sorted(_FORBIDDEN_PLAYABLE_KEYS.intersection(value.keys()))
        if forbidden:
            raise ValueError(f"playable fields forbidden in evidence.metrics: {forbidden}")
        out: dict[str, float | int | str | bool | None] = {}
        for key, raw in value.items():
            k = str(key).strip()[:80]
            if not k:
                continue
            if isinstance(raw, bool) or raw is None:
                out[k] = raw
            elif isinstance(raw, int):
                out[k] = raw
            elif isinstance(raw, float):
                out[k] = round(raw, 6)
            else:
                out[k] = str(raw)[:120]
            if len(out) >= CRITIQUE_EVIDENCE_METRIC_MAX:
                break
        return out

    @field_validator("locators")
    @classmethod
    def _cap_locators(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(value) > CRITIQUE_EVIDENCE_LOCATOR_MAX:
            raise ValueError(f"evidence.locators limited to {CRITIQUE_EVIDENCE_LOCATOR_MAX}")
        cleaned: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            forbidden = sorted(_FORBIDDEN_PLAYABLE_KEYS.intersection(item.keys()))
            if forbidden:
                raise ValueError(f"playable fields forbidden in evidence.locators: {forbidden}")
            # Drop nested playable blobs.
            slim = {
                k: v
                for k, v in item.items()
                if k not in _FORBIDDEN_PLAYABLE_KEYS
                and isinstance(v, (str, int, float, bool, type(None)))
            }
            cleaned.append(slim)
        return cleaned

    @field_validator("refs")
    @classmethod
    def _cap_refs(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        for item in value[:CRITIQUE_EVIDENCE_REF_MAX]:
            ref = str(item).strip()[:CRITIQUE_EVIDENCE_REF_LEN]
            if ref:
                out.append(ref)
        return out

    @model_validator(mode="after")
    def _size_cap(self) -> CritiqueFindingEvidence:
        import json

        blob = self.model_dump(mode="json")
        encoded = json.dumps(blob, separators=(",", ":"), sort_keys=True).encode("utf-8")
        if len(encoded) > CRITIQUE_EVIDENCE_JSON_MAX_BYTES:
            raise ValueError(
                f"evidence JSON exceeds {CRITIQUE_EVIDENCE_JSON_MAX_BYTES} bytes"
            )
        return self


class CritiqueFindingV1(BaseModel):
    """One structured evaluation finding (four-strata taxonomy)."""

    model_config = ConfigDict(extra="forbid")

    finding_id: str = Field(default_factory=lambda: str(uuid.uuid4()), max_length=80)
    severity: CritiqueSeverity = "info"
    stratum: CritiqueStratum
    category: CritiqueFindingCategory = "other"
    code: str = Field(..., min_length=1, max_length=CRITIQUE_FINDING_CODE_MAX)
    affected_range: CritiqueAffectedRange | None = None
    affected_tracks: list[str] = Field(default_factory=list, max_length=CRITIQUE_FINDING_TRACK_MAX)
    explanation: str = Field(default="", max_length=CRITIQUE_FINDING_EXPLANATION_MAX)
    evidence: CritiqueFindingEvidence = Field(default_factory=CritiqueFindingEvidence)
    suggested_action: str | None = Field(default=None, max_length=CRITIQUE_FINDING_ACTION_MAX)

    @field_validator("code")
    @classmethod
    def _normalize_code(cls, value: str) -> str:
        code = str(value).strip().lower().replace(" ", "_")[:CRITIQUE_FINDING_CODE_MAX]
        if not _FINDING_CODE_RE.match(code):
            raise ValueError("finding code must be snake_case stable id")
        return code

    @field_validator("affected_tracks")
    @classmethod
    def _normalize_tracks(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for item in value:
            tid = str(item).strip()[:80]
            if not tid or tid in seen:
                continue
            seen.add(tid)
            out.append(tid)
        return out[:CRITIQUE_FINDING_TRACK_MAX]

    @field_validator("explanation", "suggested_action", mode="before")
    @classmethod
    def _truncate_text(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None if value is None else ""
        return text[:CRITIQUE_FINDING_EXPLANATION_MAX]

    @model_validator(mode="after")
    def _subjective_severity_cap(self) -> CritiqueFindingV1:
        if self.stratum == "subjective" and self.severity == "error":
            raise ValueError("subjective findings cannot use severity error")
        # Forbid playable keys smuggled via model_extra (extra=forbid already) —
        # also reject empty code already handled.
        return self


class CritiqueStratumCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hard_constraint: int = Field(default=0, ge=0)
    technical: int = Field(default=0, ge=0)
    stylistic: int = Field(default=0, ge=0)
    subjective: int = Field(default=0, ge=0)


class CritiqueScopeDigest(BaseModel):
    """Compact scope identity embedded in critique payloads (not Analysis API)."""

    model_config = ConfigDict(extra="forbid")

    kind: CritiqueScopeKind = "composition"
    section_index: int | None = Field(default=None, ge=0)
    section_id: str | None = Field(default=None, max_length=CRITIQUE_SCOPE_ID_MAX)
    track_id: str | None = Field(default=None, max_length=80)
    start_bar: int | None = Field(default=None, ge=1)
    end_bar: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _kind_fields(self) -> CritiqueScopeDigest:
        if self.kind == "section" and self.section_index is None and not self.section_id:
            raise ValueError("section scope requires section_index or section_id")
        if self.kind == "track" and not self.track_id:
            raise ValueError("track scope requires track_id")
        if self.kind == "bars":
            if self.start_bar is None or self.end_bar is None:
                raise ValueError("bars scope requires start_bar and end_bar")
            if self.end_bar < self.start_bar:
                raise ValueError("bars scope end_bar must be >= start_bar")
        return self


# --- Request scopes (evaluation HTTP / engine) ---------------------------------


class CritiqueScopeComposition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["composition"] = "composition"


class CritiqueScopeSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["section"] = "section"
    section_index: int = Field(..., ge=0)
    section_id: str | None = Field(default=None, min_length=1, max_length=CRITIQUE_SCOPE_ID_MAX)


class CritiqueScopeTrack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["track"] = "track"
    track_id: str = Field(..., min_length=1, max_length=80)


class CritiqueScopeBars(BaseModel):
    """Inclusive bar window; resolved against composition without mutating V2."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["bars"] = "bars"
    start_bar: int = Field(..., ge=1)
    end_bar: int = Field(..., ge=1)

    @model_validator(mode="after")
    def _order(self) -> CritiqueScopeBars:
        if self.end_bar < self.start_bar:
            raise ValueError("bars scope end_bar must be >= start_bar")
        return self


CritiqueScope = (
    CritiqueScopeComposition
    | CritiqueScopeSection
    | CritiqueScopeTrack
    | CritiqueScopeBars
)


def count_strata(findings: list[CritiqueFindingV1]) -> CritiqueStratumCounts:
    counts = CritiqueStratumCounts()
    for finding in findings:
        if finding.stratum == "hard_constraint":
            counts.hard_constraint += 1
        elif finding.stratum == "technical":
            counts.technical += 1
        elif finding.stratum == "stylistic":
            counts.stylistic += 1
        elif finding.stratum == "subjective":
            counts.subjective += 1
    return counts


def merge_reason_codes_from_findings(
    findings: list[CritiqueFindingV1],
    *,
    existing: list[str] | None = None,
    max_count: int = 16,
    code_max_len: int = 80,
) -> list[str]:
    """Derive bounded reason_codes from finding codes (backward compatible)."""
    out: list[str] = []
    seen: set[str] = set()
    for code in existing or []:
        normalized = str(code).strip().lower().replace(" ", "_")[:code_max_len]
        if normalized and normalized not in seen:
            seen.add(normalized)
            out.append(normalized)
        if len(out) >= max_count:
            return out
    for finding in findings:
        code = finding.code[:code_max_len]
        if code and code not in seen:
            seen.add(code)
            out.append(code)
        if len(out) >= max_count:
            break
    return out


logger.info(
    "Critique schemas loaded",
    extra={
        "finding_field_count": len(CritiqueFindingV1.model_fields),
        "finding_code_registry_count": len(CRITIQUE_FINDING_CODES),
        "error_code_count": len(CRITIQUE_ERROR_HTTP),
    },
)
