"""Non-playable session documents for multi-model ensemble arbitration.

Survivors may embed a full ``composition.v2`` inside the session report only.
These models never invent ``composition.v5``. This module does not import
FastAPI, SQLite, or ``ai_agents``.
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

from app.composition_plan_schemas import CompositionPlan
from app.composition_schemas import CompositionV2
from app.ensemble_arbitration_settings import (
    clamp_top_n,
    hard_max_models,
    min_models,
)
from app.services.generation_constraints import (
    GenerationConstraints,
    LockedSectionConstraint,
)

logger = logging.getLogger(__name__)

POLICY_SCHEMA: Literal["ensemble.policy.v1"] = "ensemble.policy.v1"
CANDIDATE_SCHEMA: Literal["ensemble.candidate.v1"] = "ensemble.candidate.v1"
REJECTED_SCHEMA: Literal["ensemble.rejected_attempt.v1"] = "ensemble.rejected_attempt.v1"
ARBITRATION_SCHEMA: Literal["ensemble.arbitration.v1"] = "ensemble.arbitration.v1"
REQUEST_SCHEMA: Literal["ensemble.arbitration.request.v1"] = (
    "ensemble.arbitration.request.v1"
)
STATUS_SCHEMA: Literal["ensemble.arbitration.status.v1"] = (
    "ensemble.arbitration.status.v1"
)
STRATEGIES_SCHEMA: Literal["ensemble.arbitration.strategies.v1"] = (
    "ensemble.arbitration.strategies.v1"
)

# Shared fake symbolic composer ids (CI ensemble without torch).
FAKE_SYMBOLIC_TINY_MODEL_ID = "fake:symbolic-tiny"
FAKE_SYMBOLIC_SPARSE_MODEL_ID = "fake:symbolic-sparse"
FAKE_SYMBOLIC_DENSE_MODEL_ID = "fake:symbolic-dense"
FAKE_ENSEMBLE_MODEL_IDS: tuple[str, str, str] = (
    FAKE_SYMBOLIC_TINY_MODEL_ID,
    FAKE_SYMBOLIC_SPARSE_MODEL_ID,
    FAKE_SYMBOLIC_DENSE_MODEL_ID,
)

EnsembleStrategy = Literal["parallel_once"]
EnsembleSelectionMode = Literal["auto_suggest", "human", "top_n"]
EnsembleExecution = Literal["sequential", "parallel"]
EnsembleRejectStage = Literal["generate", "validator"]

ENSEMBLE_STRATEGIES: frozenset[str] = frozenset({"parallel_once"})
ENSEMBLE_SELECTION_MODES: frozenset[str] = frozenset(
    {"auto_suggest", "human", "top_n"}
)
ENSEMBLE_EXECUTIONS: frozenset[str] = frozenset({"sequential", "parallel"})

FORBIDDEN_SHELL_KEYS = frozenset({"command", "argv", "shell", "subprocess"})

MODEL_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,159}$")
# Prefer ≥16 total chars so PreferenceCandidateFeaturesV1 accepts the id.
CANDIDATE_ID_PATTERN = re.compile(r"^ens_[0-9a-f]{12,32}$")

EnsembleErrorCode = Literal[
    "ensemble_arbitration_disabled",
    "ensemble_model_limit",
    "ensemble_budget_exceeded",
    "ensemble_payload_refused",
    "ensemble_model_unready",
    "ensemble_no_survivors",
    "ensemble_strategy_unknown",
    "ensemble_selection_invalid",
]

ENSEMBLE_ERROR_HTTP_STATUS: dict[str, int] = {
    "ensemble_arbitration_disabled": 503,
    "ensemble_model_limit": 422,
    "ensemble_budget_exceeded": 422,
    "ensemble_payload_refused": 422,
    "ensemble_model_unready": 422,
    "ensemble_no_survivors": 422,
    "ensemble_strategy_unknown": 422,
    "ensemble_selection_invalid": 422,
}


class EnsembleArbitrationError(Exception):
    """Domain error mapped to a structured HTTP detail."""

    def __init__(
        self,
        code: EnsembleErrorCode | str,
        message: str,
        *,
        http_status: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = str(code)
        self.message = message[:200]
        self.http_status = (
            http_status
            if http_status is not None
            else ENSEMBLE_ERROR_HTTP_STATUS.get(self.code, 422)
        )
        self.details = details or {}


def _log_schema_rejection(field_name: str, code: str) -> None:
    logger.debug(
        "Ensemble arbitration schema rejected",
        extra={"field_name": field_name, "code": code},
    )


def scan_forbidden_keys(payload: Any, forbidden: frozenset[str]) -> list[str]:
    """Return forbidden key names found anywhere in ``payload``."""
    found: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_text = str(key)
            if key_text in forbidden:
                found.append(key_text)
            found.extend(scan_forbidden_keys(value, forbidden))
    elif isinstance(payload, list):
        for item in payload:
            found.extend(scan_forbidden_keys(item, forbidden))
    return found


def reject_ensemble_payload(payload: Any) -> None:
    """Refuse shell/argv fields anywhere in the request body."""
    shell = scan_forbidden_keys(payload, FORBIDDEN_SHELL_KEYS)
    if shell:
        field_name = shell[0]
        _log_schema_rejection(field_name, "ensemble_payload_refused")
        raise EnsembleArbitrationError(
            "ensemble_payload_refused",
            "Ensemble arbitration requests cannot embed shell or argv fields.",
            details={"field_name": field_name},
        )


def map_ensemble_error_to_http(
    exc: EnsembleArbitrationError,
) -> tuple[int, dict[str, Any]]:
    """Map domain errors to ``(status, detail)`` for HTTPException."""
    detail: dict[str, Any] = {"code": exc.code, "message": exc.message}
    if exc.details:
        detail["details"] = exc.details
    return exc.http_status, detail


class EnsembleLockedSectionV1(BaseModel):
    """Contiguous section lock mirrored from hard generation constraints."""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(..., min_length=1, max_length=64)
    start_bar: int = Field(..., ge=1)
    bar_count: int = Field(..., ge=1)


class EnsembleHardConstraintsV1(BaseModel):
    """Client-supplied hard ``GenerationConstraints`` snapshot for preview.

    Soft fields are accepted for completeness but never override hard checks.
    """

    model_config = ConfigDict(extra="forbid")

    key: str | None = None
    key_user_specified: bool = False
    time_signature: str = Field(default="4/4", min_length=3, max_length=16)
    duration_bars: int = Field(..., ge=1, le=512)
    tempo_min: int = Field(default=80, ge=40, le=240)
    tempo_max: int = Field(default=120, ge=40, le=240)
    sections: list[EnsembleLockedSectionV1] | None = None
    sections_user_specified: bool = False
    required_instrument_families: list[str] = Field(default_factory=list, max_length=16)
    requested_instruments: list[str] = Field(default_factory=list, max_length=16)
    allow_extra_instrument_families: bool = False
    mood: str = Field(default="", max_length=120)
    genre: str = Field(default="", max_length=120)
    complexity: Literal["simple", "moderate", "complex"] = "moderate"
    has_instructions: bool = False
    instructions_length: int = Field(default=0, ge=0, le=2000)

    @model_validator(mode="after")
    def validate_tempo_range(self) -> EnsembleHardConstraintsV1:
        if self.tempo_min > self.tempo_max:
            _log_schema_rejection("tempo_min", "ensemble_selection_invalid")
            raise ValueError("tempo_min must be <= tempo_max")
        return self

    def to_generation_constraints(self) -> GenerationConstraints:
        sections: tuple[LockedSectionConstraint, ...] | None = None
        if self.sections is not None:
            sections = tuple(
                LockedSectionConstraint(
                    type=section.type,
                    start_bar=section.start_bar,
                    bar_count=section.bar_count,
                )
                for section in self.sections
            )
        return GenerationConstraints(
            key=self.key,
            key_user_specified=self.key_user_specified,
            time_signature=self.time_signature,
            duration_bars=self.duration_bars,
            tempo_min=self.tempo_min,
            tempo_max=self.tempo_max,
            sections=sections,
            sections_user_specified=self.sections_user_specified,
            required_instrument_families=tuple(self.required_instrument_families),
            requested_instruments=tuple(self.requested_instruments),
            allow_extra_instrument_families=self.allow_extra_instrument_families,
            mood=self.mood,
            genre=self.genre,
            complexity=self.complexity,
            has_instructions=self.has_instructions,
            instructions_length=self.instructions_length,
        )


class EnsembleCandidateProvenanceV1(BaseModel):
    """Per-attempt provenance (no prompts or event arrays)."""

    model_config = ConfigDict(extra="forbid")

    model_id: str = Field(..., min_length=1, max_length=160)
    seed: int
    engine: str = Field(default="symbolic_composer", max_length=64)
    strategy: EnsembleStrategy
    attempt_ordinal: int = Field(..., ge=0)
    validation_digest: str | None = Field(default=None, max_length=128)
    critic_digest: str | None = Field(default=None, max_length=128)
    runtime: str | None = Field(default=None, max_length=64)
    model_version: str | None = Field(default=None, max_length=120)


class EnsembleCriticSummaryV1(BaseModel):
    """Bounded critic annotation (counts/digest only; not musical truth)."""

    model_config = ConfigDict(extra="forbid")

    finding_count: int = Field(default=0, ge=0)
    error_count: int = Field(default=0, ge=0)
    warning_count: int = Field(default=0, ge=0)
    recommendation: str | None = Field(default=None, max_length=64)
    stratum_summary: dict[str, int] = Field(default_factory=dict)
    digest: str | None = Field(default=None, max_length=128)


class EnsemblePolicyV1(BaseModel):
    """``ensemble.policy.v1`` — explicit model list and selection controls."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["ensemble.policy.v1"] = POLICY_SCHEMA
    model_ids: list[str] = Field(..., min_length=2, max_length=4)
    strategy: EnsembleStrategy = "parallel_once"
    selection_mode: EnsembleSelectionMode = "human"
    top_n: int = Field(default=2, ge=1, le=4)
    base_seed: int = Field(default=0)
    max_wall_ms: int | None = Field(default=None, ge=1_000, le=600_000)
    execution: EnsembleExecution = "sequential"

    @field_validator("model_ids")
    @classmethod
    def validate_model_ids(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in value:
            text = str(item).strip()
            if not text or not MODEL_ID_PATTERN.match(text):
                _log_schema_rejection("model_ids", "ensemble_selection_invalid")
                raise ValueError("model_ids entries must be non-empty model ids")
            cleaned.append(text)
        if len(cleaned) != len(set(cleaned)):
            _log_schema_rejection("model_ids", "ensemble_payload_refused")
            raise ValueError("duplicate model ids are not allowed")
        if len(cleaned) < min_models():
            _log_schema_rejection("model_ids", "ensemble_model_limit")
            raise ValueError(f"at least {min_models()} model_ids are required")
        if len(cleaned) > hard_max_models():
            _log_schema_rejection("model_ids", "ensemble_model_limit")
            raise ValueError(f"at most {hard_max_models()} model_ids are allowed")
        return cleaned

    @model_validator(mode="after")
    def validate_top_n_vs_models(self) -> EnsemblePolicyV1:
        if self.selection_mode == "top_n":
            if self.top_n < 1:
                _log_schema_rejection("top_n", "ensemble_selection_invalid")
                raise ValueError("top_n must be >= 1")
            effective = clamp_top_n(self.top_n, max_models=len(self.model_ids))
            if effective != self.top_n:
                object.__setattr__(self, "top_n", effective)
        return self


class EnsembleCandidateV1(BaseModel):
    """``ensemble.candidate.v1`` — one survivor with optional critic summary."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["ensemble.candidate.v1"] = CANDIDATE_SCHEMA
    candidate_id: str = Field(..., min_length=1, max_length=48)
    composition: CompositionV2
    provenance: EnsembleCandidateProvenanceV1
    validation_ok: bool = True
    critic: EnsembleCriticSummaryV1 | None = None
    preference_score: float | None = None
    rank_index: int = Field(default=0, ge=0)

    @field_validator("candidate_id")
    @classmethod
    def validate_candidate_id(cls, value: str) -> str:
        text = value.strip()
        if not CANDIDATE_ID_PATTERN.match(text):
            _log_schema_rejection("candidate_id", "ensemble_selection_invalid")
            raise ValueError("candidate_id must match ens_<hex>")
        return text


class EnsembleRejectedAttemptV1(BaseModel):
    """``ensemble.rejected_attempt.v1`` — generate or validator failure."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["ensemble.rejected_attempt.v1"] = REJECTED_SCHEMA
    model_id: str = Field(..., min_length=1, max_length=160)
    stage: EnsembleRejectStage
    code: str = Field(..., min_length=1, max_length=80)
    message: str = Field(default="", max_length=200)
    provenance: EnsembleCandidateProvenanceV1 | None = None


class EnsembleArbitrationV1(BaseModel):
    """``ensemble.arbitration.v1`` — session-only preview report."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["ensemble.arbitration.v1"] = ARBITRATION_SCHEMA
    policy: EnsemblePolicyV1
    candidates: list[EnsembleCandidateV1] = Field(default_factory=list)
    rejected_attempts: list[EnsembleRejectedAttemptV1] = Field(default_factory=list)
    suggested_candidate_id: str | None = Field(default=None, max_length=48)
    ranking_applied: bool = False
    musical_quality_claim: Literal[False] = False
    critic_is_subjective_layer: Literal[True] = True
    ranking_is_preference_not_quality: Literal[True] = True
    wall_ms: int = Field(default=0, ge=0)

    @field_validator("musical_quality_claim")
    @classmethod
    def forbid_quality_claim(cls, value: bool) -> bool:
        if value is not False:
            _log_schema_rejection("musical_quality_claim", "ensemble_payload_refused")
            raise ValueError("musical_quality_claim must be false")
        return False


class EnsembleArbitrationRequestV1(BaseModel):
    """``ensemble.arbitration.request.v1`` — client plan + constraints + policy."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["ensemble.arbitration.request.v1"] = REQUEST_SCHEMA
    policy: EnsemblePolicyV1
    plan: CompositionPlan
    constraints: EnsembleHardConstraintsV1

    @model_validator(mode="before")
    @classmethod
    def reject_shell_keys(cls, data: Any) -> Any:
        if isinstance(data, dict):
            try:
                reject_ensemble_payload(data)
            except EnsembleArbitrationError as exc:
                raise ValueError(exc.message) from exc
        return data


class EnsembleArbitrationStatusV1(BaseModel):
    """``ensemble.arbitration.status.v1`` — always readable (even when disabled)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["ensemble.arbitration.status.v1"] = STATUS_SCHEMA
    enabled: bool
    max_models: int = Field(..., ge=2, le=4)
    max_wall_ms: int = Field(..., ge=1)
    allow_parallel: bool
    fake: bool = False
    musical_quality_claim: Literal[False] = False


class EnsembleStrategiesCatalogV1(BaseModel):
    """Closed strategy / selection catalogs (readable when disabled)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["ensemble.arbitration.strategies.v1"] = STRATEGIES_SCHEMA
    strategies: list[EnsembleStrategy] = Field(default_factory=lambda: ["parallel_once"])
    selection_modes: list[EnsembleSelectionMode] = Field(
        default_factory=lambda: ["auto_suggest", "human", "top_n"]
    )
    executions: list[EnsembleExecution] = Field(
        default_factory=lambda: ["sequential", "parallel"]
    )
    fake_ensemble_model_ids: list[str] = Field(
        default_factory=lambda: list(FAKE_ENSEMBLE_MODEL_IDS)
    )


def parse_ensemble_arbitration_request(data: Any) -> EnsembleArbitrationRequestV1:
    """Parse a preview request; raise ``EnsembleArbitrationError`` on refuse."""
    if isinstance(data, EnsembleArbitrationRequestV1):
        return data
    try:
        if isinstance(data, dict):
            reject_ensemble_payload(data)
        return EnsembleArbitrationRequestV1.model_validate(data)
    except EnsembleArbitrationError:
        raise
    except ValidationError as exc:
        text = str(exc)
        code: EnsembleErrorCode = "ensemble_selection_invalid"
        if "duplicate model" in text.lower() or "command" in text.lower():
            code = "ensemble_payload_refused"
        elif "at least" in text.lower() or "at most" in text.lower():
            code = "ensemble_model_limit"
        elif "strategy" in text.lower():
            code = "ensemble_strategy_unknown"
        _log_schema_rejection("request", code)
        raise EnsembleArbitrationError(
            code,
            "Ensemble arbitration request failed validation.",
            details={"error_type": "ValidationError"},
        ) from exc
    except ValueError as exc:
        message = str(exc)[:200]
        code = (
            "ensemble_payload_refused"
            if "shell" in message.lower() or "argv" in message.lower()
            else "ensemble_selection_invalid"
        )
        _log_schema_rejection("request", code)
        raise EnsembleArbitrationError(code, message) from exc
