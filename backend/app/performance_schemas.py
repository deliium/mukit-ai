"""Strict ``performance.plan.v1`` and ``performance.realization.v1`` contracts.

Plans never embed note events, pitches, or harmony. Realizations never carry pitch.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from app.performance_conductor_constants import (
    ACCENT_STRENGTH_MAX,
    ARTICULATION_BIAS_ABS_MAX,
    DYNAMICS_CONTRAST_MAX,
    DYNAMICS_STRENGTH_MAX,
    MICROTIMING_HUMANIZE_MAX,
    MICROTIMING_SWING_MAX,
    ORCHESTRAL_BALANCE_GAIN_MAX,
    ORCHESTRAL_BALANCE_GAIN_MIN,
    PEDALING_DEPTH_MAX,
    PHRASING_ARC_MAX,
    PHRASING_BREATH_GAP_MAX,
    PLAN_SCHEMA_VERSION,
    PRESET_IDS,
    REALIZATION_SCHEMA_VERSION,
    TEMPO_RUBATO_DEPTH_MAX,
    TEMPO_RUBATO_RATE_MAX,
    TRACK_GAIN_MAX,
    TRACK_GAIN_MIN,
    TICK_DELTA_HARD_CAP,
    VELOCITY_MAX,
    VELOCITY_MIN,
)

logger = logging.getLogger(__name__)

PerformancePlanSchema = Literal["performance.plan.v1"]
PerformanceRealizationSchema = Literal["performance.realization.v1"]
PerformanceEngine = Literal["deterministic", "ai_augmented"]
PerformancePresetId = Literal[
    "intimate", "dramatic", "restrained", "mechanical", "custom"
]
PhraseAnchor = Literal["bar", "section"]
PedalStyle = Literal["none", "literal", "harmonic", "dry"]

PLAN_ID_RE = re.compile(r"^pplan_[0-9a-f]{16}$")

FORBIDDEN_PLAN_EMBED_KEYS: frozenset[str] = frozenset(
    {
        "events",
        "notes",
        "pitch",
        "pitches",
        "harmony",
        "midi_events",
        "composition",
        "composition_json",
        "note_performances",
    }
)

PERFORMANCE_ERROR_CODES: dict[str, str] = {
    "unsupported_schema_version": "schema_version must be performance.plan.v1.",
    "plan_embeds_events": "Performance plans cannot embed note events.",
    "plan_embeds_harmony": "Performance plans cannot embed harmony.",
    "plan_embeds_pitch": "Performance plans cannot embed pitch fields.",
    "plan_embeds_forbidden": "Performance plans cannot embed playable score material.",
    "performance_plan_invalid": "Performance plan failed schema validation.",
    "performance_plan_not_found": "Performance plan id was not found.",
    "performance_plan_conflict": "expected_document_revision does not match.",
    "performance_plan_stale": "Plan fingerprint does not match request composition.",
    "identity_mismatch": "Plan id or project id does not match the route.",
    "project_not_found": "Project id was not found.",
    "persistence_secret_rejected": "Payload contains a secret field or value.",
    "composition_required": "Realize/compare require a composition body.",
    "composition_invalid": "Request composition failed composition.v2 validation.",
    "ai_augment_refused": "AI augmentation returned forbidden pitch/harmony material.",
    "ai_augment_disabled": "PERFORMANCE_CONDUCTOR_AI_ENABLED is false.",
    "preset_unknown": "Unknown preset_id for catalog clone.",
    "realization_invalid": "Realization failed schema validation.",
    "event_unresolved": "Realization event_id does not resolve on the composition.",
    "performance_plan_store_failed": "Performance plan persistence failed.",
}


class PerformancePlanError(Exception):
    """Domain error for performance plan / conductor paths."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int = 422,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.details = details or {}


def map_performance_error_to_http(exc: PerformancePlanError) -> tuple[int, dict[str, Any]]:
    return exc.http_status, {
        "code": exc.code,
        "message": exc.message,
        "details": exc.details,
    }


def _log_validation(code: str, **extra: Any) -> None:
    logger.debug(
        "Performance validation failed",
        extra={"code": code, **extra},
    )


def reject_plan_embedded_material(payload: dict[str, Any], *, path: str = "") -> None:
    """Refuse plan bodies that embed events, pitches, or harmony arrays.

    Role gain keys such as ``orchestral_balance.role_gains.harmony`` (numeric)
    are allowed — only list/object score material is refused.
    """
    for key, value in payload.items():
        child_path = f"{path}.{key}" if path else key
        if key in ("events", "notes", "midi_events", "note_performances"):
            code = "plan_embeds_events"
            _log_validation(code, path=child_path)
            raise PerformancePlanError(
                code,
                PERFORMANCE_ERROR_CODES[code],
                http_status=422,
                details={"path": child_path},
            )
        if key == "harmony" and isinstance(value, list):
            code = "plan_embeds_harmony"
            _log_validation(code, path=child_path)
            raise PerformancePlanError(
                code,
                PERFORMANCE_ERROR_CODES[code],
                http_status=422,
                details={"path": child_path},
            )
        if key in ("pitch", "pitches") and not isinstance(value, (int, float)):
            # Allow numeric accidental keys; refuse string pitch / pitch lists.
            code = "plan_embeds_pitch"
            _log_validation(code, path=child_path)
            raise PerformancePlanError(
                code,
                PERFORMANCE_ERROR_CODES[code],
                http_status=422,
                details={"path": child_path},
            )
        if key in ("composition", "composition_json"):
            code = "plan_embeds_forbidden"
            _log_validation(code, path=child_path)
            raise PerformancePlanError(
                code,
                PERFORMANCE_ERROR_CODES[code],
                http_status=422,
                details={"path": child_path},
            )
        if isinstance(value, dict):
            reject_plan_embedded_material(value, path=child_path)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, dict):
                    reject_plan_embedded_material(item, path=f"{child_path}[{index}]")


class TempoRubatoDimension(BaseModel):
    model_config = ConfigDict(extra="forbid")

    depth: float = Field(0.0, ge=0.0, le=TEMPO_RUBATO_DEPTH_MAX)
    rate: float = Field(0.0, ge=0.0, le=TEMPO_RUBATO_RATE_MAX)
    phrase_anchor: PhraseAnchor = "bar"


class DynamicsDimension(BaseModel):
    model_config = ConfigDict(extra="forbid")

    curve_strength: float = Field(0.0, ge=0.0, le=DYNAMICS_STRENGTH_MAX)
    contrast: float = Field(0.0, ge=0.0, le=DYNAMICS_CONTRAST_MAX)
    velocity_floor: int = Field(VELOCITY_MIN, ge=VELOCITY_MIN, le=VELOCITY_MAX)
    velocity_ceiling: int = Field(VELOCITY_MAX, ge=VELOCITY_MIN, le=VELOCITY_MAX)

    @model_validator(mode="after")
    def floor_le_ceiling(self) -> DynamicsDimension:
        if self.velocity_floor > self.velocity_ceiling:
            raise ValueError("velocity_floor must be <= velocity_ceiling")
        return self


class PhrasingDimension(BaseModel):
    model_config = ConfigDict(extra="forbid")

    breath_gap_ticks: int = Field(0, ge=0, le=PHRASING_BREATH_GAP_MAX)
    phrase_arc: float = Field(0.0, ge=0.0, le=PHRASING_ARC_MAX)


class ArticulationDimension(BaseModel):
    model_config = ConfigDict(extra="forbid")

    legato_bias: float = Field(
        0.0, ge=-ARTICULATION_BIAS_ABS_MAX, le=ARTICULATION_BIAS_ABS_MAX
    )
    staccato_bias: float = Field(
        0.0, ge=-ARTICULATION_BIAS_ABS_MAX, le=ARTICULATION_BIAS_ABS_MAX
    )


class PedalingDimension(BaseModel):
    model_config = ConfigDict(extra="forbid")

    style: PedalStyle = "literal"
    depth: float = Field(0.0, ge=0.0, le=PEDALING_DEPTH_MAX)


class MicrotimingDimension(BaseModel):
    model_config = ConfigDict(extra="forbid")

    swing: float = Field(0.0, ge=0.0, le=MICROTIMING_SWING_MAX)
    humanize: float = Field(0.0, ge=0.0, le=MICROTIMING_HUMANIZE_MAX)


class AccentDimension(BaseModel):
    model_config = ConfigDict(extra="forbid")

    downbeat: float = Field(0.0, ge=0.0, le=ACCENT_STRENGTH_MAX)
    offbeat: float = Field(0.0, ge=0.0, le=ACCENT_STRENGTH_MAX)


class OrchestralBalanceDimension(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_gains: dict[str, float] = Field(default_factory=dict)
    track_gains: dict[str, float] = Field(default_factory=dict)

    @field_validator("role_gains", "track_gains")
    @classmethod
    def validate_gains(cls, value: dict[str, float]) -> dict[str, float]:
        for key, gain in value.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError("gain keys must be non-empty strings")
            if not (ORCHESTRAL_BALANCE_GAIN_MIN <= float(gain) <= ORCHESTRAL_BALANCE_GAIN_MAX):
                raise ValueError(
                    f"gain out of range [{ORCHESTRAL_BALANCE_GAIN_MIN}, {ORCHESTRAL_BALANCE_GAIN_MAX}]"
                )
        return {str(k): float(v) for k, v in value.items()}


class PerformanceDimensions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tempo_rubato: TempoRubatoDimension = Field(default_factory=TempoRubatoDimension)
    dynamics: DynamicsDimension = Field(default_factory=DynamicsDimension)
    phrasing: PhrasingDimension = Field(default_factory=PhrasingDimension)
    articulation: ArticulationDimension = Field(default_factory=ArticulationDimension)
    pedaling: PedalingDimension = Field(default_factory=PedalingDimension)
    microtiming: MicrotimingDimension = Field(default_factory=MicrotimingDimension)
    accent: AccentDimension = Field(default_factory=AccentDimension)
    orchestral_balance: OrchestralBalanceDimension = Field(
        default_factory=OrchestralBalanceDimension
    )


class PerformancePlanV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: PerformancePlanSchema = PLAN_SCHEMA_VERSION
    id: str | None = None
    project_id: str | None = None
    name: str = Field(..., min_length=1, max_length=120)
    preset_id: PerformancePresetId = "custom"
    engine: PerformanceEngine = "deterministic"
    source_composition_fingerprint: str = Field(..., min_length=8, max_length=128)
    dimensions: PerformanceDimensions = Field(default_factory=PerformanceDimensions)
    seed: int = Field(0, ge=0, le=2_147_483_647)

    @field_validator("preset_id")
    @classmethod
    def validate_preset(cls, value: str) -> str:
        if value not in PRESET_IDS:
            raise ValueError(f"preset_id must be one of {sorted(PRESET_IDS)}")
        return value

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError("name must not be empty")
        return name


class RealizationNoteV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(..., min_length=1, max_length=64)
    track_id: str = Field(..., min_length=1, max_length=64)
    tick_delta: int = Field(0, ge=-TICK_DELTA_HARD_CAP, le=TICK_DELTA_HARD_CAP)
    duration_delta: int = Field(0, ge=-10_000, le=10_000)
    velocity: int = Field(..., ge=VELOCITY_MIN, le=VELOCITY_MAX)


class RealizationSustainSpanV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    track_id: str = Field(..., min_length=1, max_length=64)
    start_tick: int = Field(..., ge=0)
    end_tick: int = Field(..., ge=0)

    @model_validator(mode="after")
    def ordered(self) -> RealizationSustainSpanV1:
        if self.end_tick < self.start_tick:
            raise ValueError("end_tick must be >= start_tick")
        return self


class RealizationTrackGainV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    track_id: str = Field(..., min_length=1, max_length=64)
    gain: float = Field(..., ge=TRACK_GAIN_MIN, le=TRACK_GAIN_MAX)


class RealizationMetricsV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mean_abs_tick_delta: float = Field(0.0, ge=0.0)
    mean_abs_velocity_delta: float = Field(0.0, ge=0.0)
    mean_abs_duration_delta: float = Field(0.0, ge=0.0)
    sustain_span_count: int = Field(0, ge=0)
    note_count: int = Field(0, ge=0)
    capture_performance_ignored: bool = True


class PerformanceRealizationV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: PerformanceRealizationSchema = REALIZATION_SCHEMA_VERSION
    plan_id: str
    plan_revision: int = Field(..., ge=1)
    engine_version: str
    source_composition_fingerprint: str
    notes: list[RealizationNoteV1] = Field(default_factory=list)
    sustain_spans: list[RealizationSustainSpanV1] = Field(default_factory=list)
    track_gains: list[RealizationTrackGainV1] = Field(default_factory=list)
    metrics: RealizationMetricsV1 = Field(default_factory=RealizationMetricsV1)

    @model_validator(mode="before")
    @classmethod
    def refuse_pitch_on_notes(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        notes = data.get("notes")
        if isinstance(notes, list):
            for index, note in enumerate(notes):
                if isinstance(note, dict) and (
                    "pitch" in note or "pitches" in note or "harmony" in note
                ):
                    _log_validation("realization_invalid", path=f"notes[{index}].pitch")
                    raise ValueError("realization notes must not include pitch or harmony")
        return data


class PerformancePlanSummaryV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    project_id: str
    name: str
    preset_id: PerformancePresetId
    engine: PerformanceEngine
    document_revision: int
    source_composition_fingerprint: str
    created_at: str
    updated_at: str


class PerformancePlanGetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: PerformancePlanV1
    document_revision: int
    created_at: str
    updated_at: str


class PerformancePlanListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plans: list[PerformancePlanSummaryV1]


class PerformancePresetCatalogItemV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preset_id: PerformancePresetId
    name: str
    description: str
    dimensions: PerformanceDimensions
    seed: int


class PerformancePresetCatalogResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    presets: list[PerformancePresetCatalogItemV1]


class PerformancePlanCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    preset_id: PerformancePresetId | None = None
    plan: PerformancePlanV1 | None = None
    source_composition_fingerprint: str | None = None
    composition: dict[str, Any] | None = None
    engine: PerformanceEngine = "deterministic"
    seed: int | None = None


class PerformancePlanUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: PerformancePlanV1
    expected_document_revision: int = Field(..., ge=1)


class PerformanceRealizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    composition: dict[str, Any]


class PerformanceRealizeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    realization: PerformanceRealizationV1
    stale: bool
    plan_id: str
    document_revision: int


class PerformanceCompareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    composition: dict[str, Any]


class PerformanceCompareResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stale: bool
    plan_id: str
    mechanical_metrics: RealizationMetricsV1
    performed_metrics: RealizationMetricsV1
    sample_deltas: list[dict[str, Any]] = Field(default_factory=list)
    max_sample: int = 8


def parse_performance_plan(payload: dict[str, Any]) -> PerformancePlanV1:
    if not isinstance(payload, dict):
        raise PerformancePlanError(
            "performance_plan_invalid",
            PERFORMANCE_ERROR_CODES["performance_plan_invalid"],
        )
    reject_plan_embedded_material(payload)
    schema = payload.get("schema_version")
    if schema != PLAN_SCHEMA_VERSION:
        _log_validation("unsupported_schema_version", schema_version=schema)
        raise PerformancePlanError(
            "unsupported_schema_version",
            PERFORMANCE_ERROR_CODES["unsupported_schema_version"],
            details={"schema_version": schema},
        )
    try:
        return PerformancePlanV1.model_validate(payload)
    except ValidationError as exc:
        _log_validation("performance_plan_invalid", error_count=exc.error_count())
        raise PerformancePlanError(
            "performance_plan_invalid",
            PERFORMANCE_ERROR_CODES["performance_plan_invalid"],
            details={"error_count": exc.error_count()},
        ) from exc


def parse_performance_realization(payload: dict[str, Any]) -> PerformanceRealizationV1:
    try:
        return PerformanceRealizationV1.model_validate(payload)
    except ValidationError as exc:
        _log_validation("realization_invalid", error_count=exc.error_count())
        raise PerformancePlanError(
            "realization_invalid",
            PERFORMANCE_ERROR_CODES["realization_invalid"],
            details={"error_count": exc.error_count()},
        ) from exc


def is_server_plan_id(value: str | None) -> bool:
    return bool(value and PLAN_ID_RE.match(value))
