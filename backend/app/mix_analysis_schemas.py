"""Strict ``mix.analysis.v1`` DTOs — audio-domain mix analysis (never playable).

Measurements are deterministic DSP only. Observations are rule-derived.
Interpretations are optional advisory prose. Never mutates stem/mix WAV or
``composition.v2``. Distinct from symbolic ``composition.analysis.v1``.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

logger = logging.getLogger(__name__)

# --- Contract identity / caps --------------------------------------------------

MIX_ANALYSIS_SCHEMA_VERSION: Literal["mix.analysis.v1"] = "mix.analysis.v1"
MIX_ANALYSIS_ENGINE_VERSION = "mix.analysis.engine.v1"

MIX_ANALYSIS_MEASUREMENT_MAX = 512
MIX_ANALYSIS_OBSERVATION_MAX = 64
MIX_ANALYSIS_INTERPRETATION_MAX = 16
MIX_ANALYSIS_SERIES_MAX = 32
MIX_ANALYSIS_SERIES_POINTS_MAX = 512
MIX_ANALYSIS_WARNING_MAX = 64
MIX_ANALYSIS_STEM_IDS_MAX = 32
MIX_ANALYSIS_TRACK_IDS_MAX = 32
MIX_ANALYSIS_MESSAGE_MAX = 500
MIX_ANALYSIS_REASON_MAX = 400
MIX_ANALYSIS_ACTION_MAX = 400
MIX_ANALYSIS_CODE_MAX = 80
MIX_ANALYSIS_ID_MAX = 80
MIX_ANALYSIS_FINGERPRINT_MAX = 128

_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,79}$")

MixAnalysisDspBackend = Literal["stdlib", "numpy_scipy", "fake"]
MixAnalysisSeverity = Literal["info", "warning", "error"]
MixAnalysisFindingKind = Literal["observation", "interpretation"]
MixAnalysisConfidence = Literal["measured", "estimated", "unavailable"]
MixAnalysisStratum = Literal["technical", "subjective"]

MIX_ANALYSIS_DIMENSIONS: frozenset[str] = frozenset(
    {
        "peak",
        "loudness",
        "dynamic_range",
        "clipping",
        "stereo_balance",
        "spectral_balance",
        "lf_buildup",
        "masking_proxy",
        "section_loudness",
        "headroom",
    }
)

# Stable measurement codes
MIX_ANALYSIS_MEASUREMENT_CODES: frozenset[str] = frozenset(
    {
        "peak_dbfs",
        "true_peak_proxy_dbfs",
        "rms_dbfs",
        "crest_factor_db",
        "clip_count",
        "clip_ratio",
        "stereo_lr_rms_balance_db",
        "stereo_correlation",
        "headroom_db",
        "band_energy_sub",
        "band_energy_low",
        "band_energy_low_mid",
        "band_energy_high_mid",
        "band_energy_high",
        "lf_buildup_score",
        "section_loudness_contrast_db",
        "masking_proxy",
        "lufs_approx",
        "metric_unavailable",
    }
)

# Observation codes
MIX_ANALYSIS_OBSERVATION_CODES: frozenset[str] = frozenset(
    {
        "clipping_detected",
        "low_headroom",
        "stereo_imbalance",
        "lf_buildup",
        "spectral_masking_proxy",
        "section_loudness_flat",
        "peak_hot",
    }
)

# Domain / HTTP error codes
MIX_ANALYSIS_STEM_SET_INCOMPLETE = "mix_analysis_stem_set_incomplete"
MIX_ANALYSIS_STEM_NOT_READY = "mix_analysis_stem_not_ready"
MIX_ANALYSIS_NOT_FOUND = "mix_analysis_not_found"
MIX_ANALYSIS_QUOTA_EXCEEDED = "mix_analysis_quota_exceeded"
MIX_ANALYSIS_INPUT_TOO_LARGE = "mix_analysis_input_too_large"
MIX_ANALYSIS_TIMEOUT = "mix_analysis_timeout"
MIX_ANALYSIS_DIMENSION_UNKNOWN = "mix_analysis_dimension_unknown"
MIX_ANALYSIS_METRIC_UNAVAILABLE = "metric_unavailable"
MIX_ANALYSIS_INTERPRETATION_UNAVAILABLE = "interpretation_unavailable"
MIX_ANALYSIS_INTERNAL_ERROR = "mix_analysis_internal_error"
MIX_ANALYSIS_INVALID_REQUEST = "mix_analysis_invalid_request"
MIX_ANALYSIS_REVERB_UNAVAILABLE = "reverb_estimate_unavailable"

MIX_ANALYSIS_ERROR_HTTP: dict[str, int] = {
    MIX_ANALYSIS_STEM_SET_INCOMPLETE: 422,
    MIX_ANALYSIS_STEM_NOT_READY: 422,
    MIX_ANALYSIS_NOT_FOUND: 404,
    MIX_ANALYSIS_QUOTA_EXCEEDED: 422,
    MIX_ANALYSIS_INPUT_TOO_LARGE: 422,
    MIX_ANALYSIS_TIMEOUT: 504,
    MIX_ANALYSIS_DIMENSION_UNKNOWN: 422,
    MIX_ANALYSIS_METRIC_UNAVAILABLE: 422,
    MIX_ANALYSIS_INTERPRETATION_UNAVAILABLE: 200,  # soft warning only
    MIX_ANALYSIS_INTERNAL_ERROR: 500,
    MIX_ANALYSIS_INVALID_REQUEST: 422,
    MIX_ANALYSIS_REVERB_UNAVAILABLE: 200,
}


class MixAnalysisError(Exception):
    """Domain error for mix analysis — sanitized client detail only."""

    code: str = MIX_ANALYSIS_INTERNAL_ERROR
    http_status: int = 500

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        http_status: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code
        if http_status is not None:
            self.http_status = http_status
        else:
            self.http_status = MIX_ANALYSIS_ERROR_HTTP.get(self.code, self.http_status)
        self.details = details or {}
        logger.warning(
            "Mix analysis domain error",
            extra={"code": self.code, "detail_keys": sorted(self.details.keys())},
        )


def map_mix_analysis_error_to_http(exc: MixAnalysisError) -> tuple[int, dict[str, Any]]:
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


def _validate_code(value: str) -> str:
    text = (value or "").strip()
    if not _CODE_RE.match(text):
        raise ValueError(f"invalid code: {text[:40]!r}")
    return text


# --- Locus / series / layers ---------------------------------------------------


class MixAnalysisLocus(BaseModel):
    """Stem / track / freq / time window for a measurement or finding."""

    model_config = ConfigDict(extra="forbid")

    stem_ids: list[str] = Field(default_factory=list, max_length=MIX_ANALYSIS_STEM_IDS_MAX)
    stem_roles: list[str] = Field(default_factory=list, max_length=MIX_ANALYSIS_STEM_IDS_MAX)
    source_track_ids: list[str] = Field(
        default_factory=list, max_length=MIX_ANALYSIS_TRACK_IDS_MAX
    )
    freq_hz_low: float | None = Field(default=None, ge=0.0, le=96_000.0)
    freq_hz_high: float | None = Field(default=None, ge=0.0, le=96_000.0)
    start_bar: int | None = Field(default=None, ge=1)
    end_bar: int | None = Field(default=None, ge=1)
    start_seconds: float | None = Field(default=None, ge=0.0)
    end_seconds: float | None = Field(default=None, ge=0.0)

    @field_validator("stem_ids", "stem_roles", "source_track_ids", mode="before")
    @classmethod
    def _trim_ids(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("expected list")
        out: list[str] = []
        for item in value:
            text = str(item).strip()
            if text:
                out.append(text[:MIX_ANALYSIS_ID_MAX])
        return out

    @model_validator(mode="after")
    def _bounds(self) -> MixAnalysisLocus:
        if (
            self.start_bar is not None
            and self.end_bar is not None
            and self.end_bar < self.start_bar
        ):
            raise ValueError("end_bar must be >= start_bar")
        if (
            self.start_seconds is not None
            and self.end_seconds is not None
            and self.end_seconds < self.start_seconds
        ):
            raise ValueError("end_seconds must be >= start_seconds")
        if (
            self.freq_hz_low is not None
            and self.freq_hz_high is not None
            and self.freq_hz_high < self.freq_hz_low
        ):
            raise ValueError("freq_hz_high must be >= freq_hz_low")
        return self


class MixAnalysisSeriesPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    t: float = Field(description="Seconds from stem/mix start")
    v: float


class MixAnalysisSeries(BaseModel):
    """Downsampled visualization series — never PCM."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=MIX_ANALYSIS_ID_MAX)
    kind: Literal["peak_envelope", "loudness_envelope", "band_energy"] = "peak_envelope"
    unit: str = Field(default="dbfs", max_length=32)
    stem_id: str | None = Field(default=None, max_length=MIX_ANALYSIS_ID_MAX)
    band: str | None = Field(default=None, max_length=32)
    points: list[MixAnalysisSeriesPoint] = Field(
        default_factory=list, max_length=MIX_ANALYSIS_SERIES_POINTS_MAX
    )


class MixAnalysisMeasurement(BaseModel):
    """Deterministic DSP scalar or series reference."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=MIX_ANALYSIS_CODE_MAX)
    unit: str = Field(default="", max_length=32)
    value: float | None = None
    series_ref: str | None = Field(default=None, max_length=MIX_ANALYSIS_ID_MAX)
    locus: MixAnalysisLocus = Field(default_factory=MixAnalysisLocus)
    confidence: MixAnalysisConfidence = "measured"
    unavailable_code: str | None = Field(default=None, max_length=MIX_ANALYSIS_CODE_MAX)

    @field_validator("code")
    @classmethod
    def _code(cls, value: str) -> str:
        return _validate_code(value)


class MixAnalysisEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    measurement_codes: list[str] = Field(default_factory=list, max_length=24)

    @field_validator("measurement_codes", mode="before")
    @classmethod
    def _codes(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("expected list")
        return [_validate_code(str(item)) for item in value if str(item).strip()]


class MixAnalysisObservation(BaseModel):
    """Rule-derived objective finding from measurements."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["observation"] = "observation"
    code: str = Field(min_length=1, max_length=MIX_ANALYSIS_CODE_MAX)
    severity: MixAnalysisSeverity = "warning"
    stratum: Literal["technical"] = "technical"
    message: str = Field(min_length=1, max_length=MIX_ANALYSIS_MESSAGE_MAX)
    locus: MixAnalysisLocus
    reason: str = Field(min_length=1, max_length=MIX_ANALYSIS_REASON_MAX)
    evidence: MixAnalysisEvidence = Field(default_factory=MixAnalysisEvidence)
    suggested_action: str | None = Field(default=None, max_length=MIX_ANALYSIS_ACTION_MAX)

    @field_validator("code")
    @classmethod
    def _code(cls, value: str) -> str:
        return _validate_code(value)

    @model_validator(mode="after")
    def _suggestion_requires_locus_reason(self) -> MixAnalysisObservation:
        if self.suggested_action and not (
            self.reason
            and (
                self.locus.stem_ids
                or self.locus.stem_roles
                or self.locus.source_track_ids
                or self.locus.start_seconds is not None
                or self.locus.start_bar is not None
            )
        ):
            logger.debug(
                "Mix observation suggestion without locus rejected",
                extra={"code": self.code},
            )
            self.suggested_action = None
        return self


class MixAnalysisInterpretation(BaseModel):
    """Optional AI advisory prose — never invents numeric measurements."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["interpretation"] = "interpretation"
    code: str = Field(default="ai_mix_note", max_length=MIX_ANALYSIS_CODE_MAX)
    severity: Literal["info", "warning"] = "info"
    stratum: Literal["subjective"] = "subjective"
    message: str = Field(min_length=1, max_length=MIX_ANALYSIS_MESSAGE_MAX)
    locus: MixAnalysisLocus
    cites_measurement_codes: list[str] = Field(default_factory=list, max_length=24)
    cites_observation_codes: list[str] = Field(default_factory=list, max_length=24)
    suggested_action: str | None = Field(default=None, max_length=MIX_ANALYSIS_ACTION_MAX)

    @field_validator("code")
    @classmethod
    def _code(cls, value: str) -> str:
        return _validate_code(value)

    @field_validator("cites_measurement_codes", "cites_observation_codes", mode="before")
    @classmethod
    def _cite_codes(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("expected list")
        return [_validate_code(str(item)) for item in value if str(item).strip()]


class MixAnalysisWarning(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=MIX_ANALYSIS_CODE_MAX)
    severity: MixAnalysisSeverity = "info"
    message: str = Field(min_length=1, max_length=MIX_ANALYSIS_MESSAGE_MAX)

    @field_validator("code")
    @classmethod
    def _code(cls, value: str) -> str:
        return _validate_code(value)


class MixAnalysisStemFingerprint(BaseModel):
    """Per-stem analyze provenance (prefix only — never full PCM hash dump)."""

    model_config = ConfigDict(extra="forbid")

    stem_id: str = Field(min_length=1, max_length=MIX_ANALYSIS_ID_MAX)
    stem_role: str = Field(min_length=1, max_length=32)
    sha256_prefix: str = Field(min_length=1, max_length=32)
    source_track_ids: list[str] = Field(default_factory=list, max_length=MIX_ANALYSIS_TRACK_IDS_MAX)


class MixAnalysisReport(BaseModel):
    """Versioned mix analysis report — no PCM."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["mix.analysis.v1"] = MIX_ANALYSIS_SCHEMA_VERSION
    engine_version: str = MIX_ANALYSIS_ENGINE_VERSION
    report_id: str | None = Field(default=None, max_length=MIX_ANALYSIS_ID_MAX)
    project_id: str | None = Field(default=None, max_length=MIX_ANALYSIS_ID_MAX)
    stem_set_id: str = Field(min_length=1, max_length=MIX_ANALYSIS_ID_MAX)
    mix_render_id: str | None = Field(default=None, max_length=MIX_ANALYSIS_ID_MAX)
    dsp_backend: MixAnalysisDspBackend = "stdlib"
    mutates_audio: Literal[False] = False
    mutates_composition: Literal[False] = False
    source_stem_set_fingerprint: str = Field(
        min_length=1, max_length=MIX_ANALYSIS_FINGERPRINT_MAX
    )
    source_composition_fingerprint: str | None = Field(
        default=None, max_length=MIX_ANALYSIS_FINGERPRINT_MAX
    )
    analyzed_stems: list[MixAnalysisStemFingerprint] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list, max_length=32)
    measurements: list[MixAnalysisMeasurement] = Field(
        default_factory=list, max_length=MIX_ANALYSIS_MEASUREMENT_MAX
    )
    observations: list[MixAnalysisObservation] = Field(
        default_factory=list, max_length=MIX_ANALYSIS_OBSERVATION_MAX
    )
    interpretations: list[MixAnalysisInterpretation] = Field(
        default_factory=list, max_length=MIX_ANALYSIS_INTERPRETATION_MAX
    )
    series: list[MixAnalysisSeries] = Field(
        default_factory=list, max_length=MIX_ANALYSIS_SERIES_MAX
    )
    warnings: list[MixAnalysisWarning] = Field(
        default_factory=list, max_length=MIX_ANALYSIS_WARNING_MAX
    )
    created_at: str | None = None

    @field_validator("dimensions", mode="before")
    @classmethod
    def _dims(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("expected list")
        out: list[str] = []
        for item in value:
            text = str(item).strip().lower()
            if text and text not in out:
                out.append(text)
        return out


# --- Request / response / persisted metadata -----------------------------------


class MixAnalysisAnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str | None = Field(default=None, max_length=MIX_ANALYSIS_ID_MAX)
    stem_set_id: str = Field(min_length=1, max_length=MIX_ANALYSIS_ID_MAX)
    mix_render_id: str | None = Field(default=None, max_length=MIX_ANALYSIS_ID_MAX)
    stem_ids: list[str] | None = Field(default=None, max_length=MIX_ANALYSIS_STEM_IDS_MAX)
    composition: dict[str, Any] | None = None
    dimensions: list[str] | None = Field(default=None, max_length=32)
    include_ai_interpretation: bool = False
    persist: bool = False

    @field_validator("stem_ids", mode="before")
    @classmethod
    def _stem_ids(cls, value: Any) -> list[str] | None:
        if value is None:
            return None
        if not isinstance(value, list):
            raise ValueError("expected list")
        return [str(item).strip() for item in value if str(item).strip()]

    @model_validator(mode="after")
    def _persist_needs_project(self) -> MixAnalysisAnalyzeRequest:
        if self.persist and not (self.project_id and self.project_id.strip()):
            raise ValueError("persist=true requires project_id")
        if self.dimensions:
            unknown = [d for d in self.dimensions if d not in MIX_ANALYSIS_DIMENSIONS]
            if unknown:
                logger.debug(
                    "Mix analysis schema rejected dimensions",
                    extra={"field_keys": ["dimensions"], "unknown": unknown[:8]},
                )
                raise ValueError(f"unknown dimensions: {unknown}")
        return self


class MixAnalysisAnalyzeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report: MixAnalysisReport
    persisted: bool = False
    mutates_audio: Literal[False] = False
    mutates_composition: Literal[False] = False


class MixAnalysisReportMeta(BaseModel):
    """List/get metadata for a persisted report (no full body required)."""

    model_config = ConfigDict(extra="forbid")

    report_id: str
    project_id: str
    stem_set_id: str
    mix_render_id: str | None = None
    dsp_backend: MixAnalysisDspBackend
    source_stem_set_fingerprint: str
    source_composition_fingerprint: str | None = None
    byte_size: int = Field(ge=0)
    sha256_prefix: str
    created_at: str
    observation_count: int = 0
    measurement_count: int = 0


class MixAnalysisReportListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[MixAnalysisReportMeta] = Field(default_factory=list)


class MixAnalysisReportGetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meta: MixAnalysisReportMeta
    report: MixAnalysisReport
