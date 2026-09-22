"""Session DTOs for bounded critique → revise → re-evaluate loops.

Non-playable; never embed ``composition.v2`` scores. Loop is preview-only.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.ai_agents.artifact_schemas import (
    FORBIDDEN_PLAYABLE_TOP_LEVEL,
    AgentCompositionPatchV1,
    AgentRevisionPlanV1,
)
from app.composition_schemas import CompositionV2

logger = logging.getLogger(__name__)

REVISION_PASS_RECORD_SCHEMA: Literal["revision_pass_record.v1"] = "revision_pass_record.v1"

REVISION_MODE_MAX_PASSES: dict[str, int] = {
    "off": 0,
    "fast": 1,
    "balanced": 2,
    "thorough": 3,
}

# Product modes never exceed Thorough; raw API may still pass max_revisions ≤ 8.
REVISION_PRODUCT_MAX_PASSES = 3
REVISION_API_ABSOLUTE_MAX_PASSES = 8
REVISION_AFFECTED_RANGE_MAX = 8
REVISION_PASS_ARTIFACT_DIGEST_MAX = 128


class RevisionMode(StrEnum):
    OFF = "off"
    FAST = "fast"
    BALANCED = "balanced"
    THOROUGH = "thorough"


class RevisionStopReason(StrEnum):
    CRITIC_APPROVE = "critic_approve"
    HARD_REQUIREMENTS_SATISFIED = "hard_requirements_satisfied"
    IMPROVEMENT_BELOW_THRESHOLD = "improvement_below_threshold"
    MAX_PASSES_REACHED = "max_passes_reached"
    RESOURCE_BUDGET_EXHAUSTED = "resource_budget_exhausted"
    CANCELLED = "cancelled"
    REVISE_EXHAUSTED = "revise_exhausted"
    VALIDATION_FAILED_KEPT_LAST_VALID = "validation_failed_kept_last_valid"


class UsageStatus(StrEnum):
    AVAILABLE = "available"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class RevisionFailurePolicy(StrEnum):
    KEEP_LAST_VALID_AND_STOP = "keep_last_valid_and_stop"


class RevisionAffectedRange(BaseModel):
    """Inclusive bar window for targeted revision (non-playable)."""

    model_config = ConfigDict(extra="forbid")

    start_bar: int = Field(..., ge=1)
    end_bar: int = Field(..., ge=1)

    @model_validator(mode="after")
    def _bounds(self) -> Self:
        if self.end_bar < self.start_bar:
            raise ValueError("affected_range end_bar must be >= start_bar")
        return self


class CritiqueScoreDigest(BaseModel):
    """Bounded numeric digest — never full findings prose."""

    model_config = ConfigDict(extra="forbid")

    score: int = Field(..., ge=0)
    hard_errors: int = Field(default=0, ge=0)
    technical_warnings: int = Field(default=0, ge=0)
    stylistic_count: int = Field(default=0, ge=0)
    subjective_count: int = Field(default=0, ge=0)


class RevisionLoopUsageDelta(BaseModel):
    """Per-pass usage delta when adapters expose it."""

    model_config = ConfigDict(extra="forbid")

    latency_ms: int = Field(default=0, ge=0)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    estimated_cost_class: str | None = Field(default=None, max_length=32)
    usage_status: UsageStatus = UsageStatus.UNAVAILABLE


class RevisionLoopUsage(BaseModel):
    """Accumulated loop usage — never invents currency."""

    model_config = ConfigDict(extra="forbid")

    latency_ms_total: int = Field(default=0, ge=0)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    estimated_cost_class_peak: str | None = Field(default=None, max_length=32)
    usage_status: UsageStatus = UsageStatus.UNAVAILABLE


class RevisionPassValidationResult(BaseModel):
    """Validation outcome for one revise pass."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "failed", "skipped"] = "skipped"
    preserve_ok: bool | None = None
    error_codes: list[str] = Field(default_factory=list, max_length=16)
    message: str | None = Field(default=None, max_length=200)

    @field_validator("error_codes")
    @classmethod
    def _normalize_codes(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        for item in value:
            code = str(item).strip()[:80]
            if code:
                out.append(code)
        return out[:16]


class RevisionPassCandidateV1(BaseModel):
    """Session-only playable snapshot for a revision pass (audition / compare).

    Kept separate from ``RevisionPassRecordV1``, which forbids embedded scores.
    Never persisted as a durable project revision.
    """

    model_config = ConfigDict(extra="forbid")

    pass_index: int = Field(..., ge=0, le=REVISION_API_ABSOLUTE_MAX_PASSES)
    candidate_fingerprint: str = Field(..., min_length=1, max_length=128)
    composition: CompositionV2


class RevisionPassRecordV1(BaseModel):
    """Inspectable session envelope linking critique / plan / patch / validation."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["revision_pass_record.v1"] = REVISION_PASS_RECORD_SCHEMA
    pass_index: int = Field(..., ge=0, le=REVISION_API_ABSOLUTE_MAX_PASSES)
    critique_artifact_id: str | None = Field(default=None, max_length=80)
    critique_recommendation: str | None = Field(default=None, max_length=32)
    critique_digest: str | None = Field(default=None, max_length=REVISION_PASS_ARTIFACT_DIGEST_MAX)
    revision_plan: dict[str, Any] | None = None
    composition_patch: dict[str, Any] | None = None
    validation_result: RevisionPassValidationResult = Field(
        default_factory=RevisionPassValidationResult
    )
    candidate_fingerprint: str = Field(..., min_length=1, max_length=128)
    score_digest: CritiqueScoreDigest | None = None
    usage_delta: RevisionLoopUsageDelta = Field(default_factory=RevisionLoopUsageDelta)
    stop_eligible_reasons: list[str] = Field(default_factory=list, max_length=16)
    target_agent_ids: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="before")
    @classmethod
    def reject_playable(cls, data: Any) -> Any:
        if isinstance(data, dict):
            forbidden = sorted(FORBIDDEN_PLAYABLE_TOP_LEVEL.intersection(data.keys()))
            if forbidden:
                logger.debug(
                    "Revision pass record rejected",
                    extra={"code": "revision_pass_playable_forbidden"},
                )
                raise ValueError(
                    "revision_pass_record must not include playable fields: "
                    + ", ".join(forbidden)
                )
            for nested_key in ("revision_plan", "composition_patch"):
                nested = data.get(nested_key)
                if isinstance(nested, dict):
                    nested_forbidden = sorted(
                        FORBIDDEN_PLAYABLE_TOP_LEVEL.intersection(nested.keys())
                    )
                    if nested_forbidden:
                        raise ValueError(
                            f"{nested_key} must not include playable fields: "
                            + ", ".join(nested_forbidden)
                        )
        return data

    @field_validator("revision_plan")
    @classmethod
    def _validate_plan(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        return AgentRevisionPlanV1.model_validate(value).model_dump(mode="json")

    @field_validator("composition_patch")
    @classmethod
    def _validate_patch(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        return AgentCompositionPatchV1.model_validate(value).model_dump(mode="json")

    @field_validator("stop_eligible_reasons", "target_agent_ids")
    @classmethod
    def _truncate_lists(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        for item in value:
            cleaned = str(item).strip()[:80]
            if cleaned:
                out.append(cleaned)
        return out


class RevisionLoopBudgets(BaseModel):
    """Optional wall-clock / token caps for one preview run."""

    model_config = ConfigDict(extra="forbid")

    max_wall_ms: int | None = Field(default=None, ge=1, le=3_600_000)
    max_prompt_tokens: int | None = Field(default=None, ge=1, le=10_000_000)


def max_passes_for_mode(mode: RevisionMode | str) -> int:
    """Map named product mode → hard pass cap."""
    key = mode.value if isinstance(mode, RevisionMode) else str(mode).strip().lower()
    if key not in REVISION_MODE_MAX_PASSES:
        logger.debug(
            "Revision mode rejected",
            extra={"code": "revision_mode_unknown", "mode": key[:32]},
        )
        raise ValueError(f"Unknown revision_mode: {key}")
    return REVISION_MODE_MAX_PASSES[key]


def resolve_max_passes(
    *,
    revision_mode: RevisionMode | str | None = None,
    max_revisions: int | None = None,
) -> tuple[RevisionMode, int]:
    """Resolve mode + effective max_passes with product clamps.

    When ``revision_mode`` is set (including ``off``), mode caps apply.
    Raw ``max_revisions`` alone (mode omitted / None) remains the escape hatch ≤ 8.
    """
    if revision_mode is None:
        mode = RevisionMode.OFF
        if max_revisions is None:
            return mode, 0
        capped = max(0, min(int(max_revisions), REVISION_API_ABSOLUTE_MAX_PASSES))
        logger.debug(
            "Revision max_passes from raw max_revisions",
            extra={"max_passes": capped},
        )
        return mode, capped

    mode = (
        revision_mode
        if isinstance(revision_mode, RevisionMode)
        else RevisionMode(str(revision_mode).strip().lower())
    )
    mode_cap = max_passes_for_mode(mode)
    # Mode ``off`` keeps raw max_revisions as the ≤8 escape hatch (legacy spine).
    if mode == RevisionMode.OFF:
        if max_revisions is None:
            return mode, 0
        capped = max(0, min(int(max_revisions), REVISION_API_ABSOLUTE_MAX_PASSES))
        logger.debug(
            "Revision max_passes from off+raw",
            extra={"max_passes": capped},
        )
        return mode, capped
    # Named product modes: max_revisions=0 / omitted means "use mode cap".
    # A positive max_revisions may only further clamp downward (never above mode).
    if max_revisions is None or int(max_revisions) <= 0:
        logger.debug(
            "Revision max_passes from mode default",
            extra={"revision_mode": mode.value, "max_passes": mode_cap},
        )
        return mode, mode_cap
    effective = min(
        mode_cap,
        REVISION_PRODUCT_MAX_PASSES,
        min(int(max_revisions), REVISION_API_ABSOLUTE_MAX_PASSES),
    )
    logger.debug(
        "Revision max_passes resolved",
        extra={"revision_mode": mode.value, "max_passes": effective},
    )
    return mode, effective


logger.info(
    "Revision loop schemas loaded",
    extra={
        "mode_count": len(RevisionMode),
        "stop_reason_count": len(RevisionStopReason),
        "product_max_passes": REVISION_PRODUCT_MAX_PASSES,
    },
)
