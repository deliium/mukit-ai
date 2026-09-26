"""Typed contracts for the V4 multi-agent music layer (``agent.*.v1``).

``MusicAgentCapability`` is distinct from ``ai_runtime.ModelCapability``.
Agents never mutate durable Composition; ``mutates_composition`` is always false.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal, Mapping, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.ai_runtime.capabilities import ModelCapability
from app.composition_schemas import CompositionV2
from app.operation_trace_schemas import OperationSummaryV1
from app.critique_schemas import (
    CRITIQUE_ENGINE_VERSION,
    CRITIQUE_FINDING_MAX,
    CritiqueFindingV1,
    CritiqueModelStatus,
    CritiqueScopeDigest,
    CritiqueStratumCounts,
    count_strata,
    merge_reason_codes_from_findings,
)

logger = logging.getLogger(__name__)

# --- Contract identity ---------------------------------------------------------

AGENT_DESCRIPTOR_SCHEMA: Literal["agent.descriptor.v1"] = "agent.descriptor.v1"
AGENT_ARTIFACT_SCHEMA: Literal["agent.artifact.v1"] = "agent.artifact.v1"
AGENT_BRIEF_SCHEMA: Literal["agent.brief.v1"] = "agent.brief.v1"
AGENT_WORKFLOW_PLAN_SCHEMA: Literal["agent.workflow_plan.v1"] = "agent.workflow_plan.v1"
AGENT_CRITIQUE_SCHEMA: Literal["agent.critique.v1"] = "agent.critique.v1"
AGENT_FORM_PLAN_SCHEMA: Literal["agent.form_plan.v1"] = "agent.form_plan.v1"
AGENT_HARMONY_PLAN_SCHEMA: Literal["agent.harmony_plan.v1"] = "agent.harmony_plan.v1"
AGENT_MOTIF_PLAN_SCHEMA: Literal["agent.motif_plan.v1"] = "agent.motif_plan.v1"
AGENT_ARRANGEMENT_PLAN_SCHEMA: Literal["agent.arrangement_plan.v1"] = "agent.arrangement_plan.v1"
AGENT_ORCHESTRATION_PLAN_SCHEMA: Literal["agent.orchestration_plan.v1"] = (
    "agent.orchestration_plan.v1"
)
AGENT_PERFORMANCE_PLAN_SCHEMA: Literal["agent.performance_plan.v1"] = "agent.performance_plan.v1"
AGENT_PRODUCTION_PLAN_SCHEMA: Literal["agent.production_plan.v1"] = "agent.production_plan.v1"
AGENT_REVISION_PLAN_SCHEMA: Literal["agent.revision_plan.v1"] = "agent.revision_plan.v1"
AGENT_COMPOSITION_PATCH_SCHEMA: Literal["agent.composition_patch.v1"] = (
    "agent.composition_patch.v1"
)
AGENT_RENDER_PLAN_SCHEMA: Literal["agent.render_plan.v1"] = "agent.render_plan.v1"

AGENT_SPINE_WORKFLOW_ID = "agent_spine_v1"
AGENT_SPINE_PIPELINE_ALIAS = "multi_agent_v4"

CRITIQUE_REASON_MAX_LEN = 400
CRITIQUE_REASON_CODE_MAX_COUNT = 16
CRITIQUE_REASON_CODE_MAX_LEN = 80
BRIEF_INTENT_MAX_LEN = 500
BRIEF_CONSTRAINT_MAX_COUNT = 16
BRIEF_CONSTRAINT_MAX_LEN = 120
WORKFLOW_PLAN_STEP_MAX = 16
ARTIFACT_WARNING_MAX = 32
ARTIFACT_WARNING_MAX_LEN = 80
ARTIFACT_PARENT_MAX = 16
ARTIFACT_DEPENDS_ON_MAX = 32
BOUND_MODEL_ID_MAX = 160

RetentionClass = Literal["temporary", "durable"]
ArtifactDependencyRelation = Literal[
    "requires",
    "derived_from",
    "supersedes",
    "critiques",
    "realizes",
]

# Named context slots that hold AgentArtifactV1 (not compositions).
ARTIFACT_SLOT_NAMES: frozenset[str] = frozenset(
    {
        "brief",
        "workflow_plan",
        "structure_plan",
        "harmony_artifact",
        "melody_artifact",
        "arrangement_candidate",
        "orchestration_artifact",
        "expression_artifact",
        "production_artifact",
        "critique",
        "analysis",
    }
)


class MusicAgentCapability(StrEnum):
    """Agent role taxonomy — never overload ``ModelCapability``."""

    CREATIVE_DIRECTOR = "creative_director"
    STRUCTURE_FORM = "structure_form"
    HARMONY = "harmony"
    MELODY_MOTIF = "melody_motif"
    ARRANGEMENT = "arrangement"
    ORCHESTRATION = "orchestration"
    PERFORMANCE_EXPRESSION = "performance_expression"
    PRODUCTION = "production"
    CRITIC = "critic"


class AgentOperation(StrEnum):
    """Supported per-agent operations."""

    PLAN = "plan"
    PROPOSE = "propose"
    CRITIQUE = "critique"
    REALIZE_DRAFT = "realize_draft"
    ADVISE = "advise"
    RUN = "run"


class AgentArtifactKind(StrEnum):
    PLAN = "plan"
    ANALYSIS = "analysis"
    RECOMMENDATION = "recommendation"
    CANDIDATE_PATCH = "candidate_patch"
    GENERATED_ARTIFACT = "generated_artifact"
    CRITIQUE = "critique"
    BRIEF = "brief"
    WORKFLOW_PLAN = "workflow_plan"


class AgentStatus(StrEnum):
    READY = "ready"
    UNAVAILABLE = "unavailable"
    DEGRADED = "degraded"


class CritiqueRecommendation(StrEnum):
    APPROVE = "approve"
    REVISE = "revise"


AGENT_CONTENT_TYPES: frozenset[str] = frozenset(
    {
        AGENT_BRIEF_SCHEMA,
        AGENT_WORKFLOW_PLAN_SCHEMA,
        AGENT_CRITIQUE_SCHEMA,
        AGENT_FORM_PLAN_SCHEMA,
        AGENT_HARMONY_PLAN_SCHEMA,
        AGENT_MOTIF_PLAN_SCHEMA,
        AGENT_ARRANGEMENT_PLAN_SCHEMA,
        AGENT_ORCHESTRATION_PLAN_SCHEMA,
        AGENT_PERFORMANCE_PLAN_SCHEMA,
        AGENT_PRODUCTION_PLAN_SCHEMA,
        AGENT_REVISION_PLAN_SCHEMA,
        AGENT_COMPOSITION_PATCH_SCHEMA,
        AGENT_RENDER_PLAN_SCHEMA,
        "composition.v2",
        "composition.plan.v1",
        "project.plan.v1",
        "composition.analysis.v1",  # critic deterministic sidecar
        "composition.analysis.bounded.v1",
        "arrangement.candidate",
        "reharmonize.candidate",
        "motif.draft",
        "melody.draft",
        "orchestration.recommendation",
        "expression.recommendation",
        "production.notes",
        "neural_audio_render.job.v1",
        "agent.recommendation.v1",
    }
)

KNOWN_AGENT_IDS: tuple[str, ...] = (
    "creative_director",
    "structure_form",
    "harmony",
    "melody_motif",
    "arrangement",
    "orchestration",
    "performance_expression",
    "production",
    "critic",
)

CostClass = Literal["free_local", "low", "medium", "high"]
LocalityPreference = Literal["local", "remote", "any"]


class AgentResourceHints(BaseModel):
    """Resource / cost hints derived from config + bound model limits."""

    model_config = ConfigDict(extra="forbid")

    locality_preference: LocalityPreference = "any"
    estimated_cost_class: CostClass = "low"
    parallelizable: bool = False
    uses_gpu: bool = False


class AgentArtifactProvenance(BaseModel):
    """Secret-safe provenance fragment attached to an artifact."""

    model_config = ConfigDict(extra="forbid")

    operation: str = Field(..., min_length=1, max_length=64)
    model_id: str | None = Field(default=None, max_length=BOUND_MODEL_ID_MAX)
    runtime: str | None = Field(default=None, max_length=64)
    capability: str | None = Field(default=None, max_length=64)
    agent_id: str | None = Field(default=None, max_length=64)
    agent_capability: str | None = Field(default=None, max_length=64)
    model_version: str | None = Field(default=None, max_length=120)


class AgentBriefV1(BaseModel):
    """Creative Director brief — non-playable intent."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["agent.brief.v1"] = AGENT_BRIEF_SCHEMA
    intent: str = Field(..., min_length=1, max_length=BRIEF_INTENT_MAX_LEN)
    mood: str | None = Field(default=None, max_length=64)
    genre: str | None = Field(default=None, max_length=64)
    constraints: list[str] = Field(default_factory=list, max_length=BRIEF_CONSTRAINT_MAX_COUNT)
    stop_criteria: list[str] = Field(default_factory=list, max_length=BRIEF_CONSTRAINT_MAX_COUNT)

    @field_validator("constraints", "stop_criteria")
    @classmethod
    def _truncate_items(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        for item in value:
            cleaned = str(item).strip()[:BRIEF_CONSTRAINT_MAX_LEN]
            if cleaned:
                out.append(cleaned)
        return out


class AgentWorkflowPlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(..., min_length=1, max_length=64)
    operation: AgentOperation = AgentOperation.RUN
    notes: str | None = Field(default=None, max_length=200)


class AgentWorkflowPlanV1(BaseModel):
    """Ordered stage plan produced by Creative Director."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["agent.workflow_plan.v1"] = AGENT_WORKFLOW_PLAN_SCHEMA
    workflow_id: str = Field(default=AGENT_SPINE_WORKFLOW_ID, max_length=64)
    steps: list[AgentWorkflowPlanStep] = Field(..., min_length=1, max_length=WORKFLOW_PLAN_STEP_MAX)
    max_revisions: int = Field(default=0, ge=0, le=8)


class AgentCritiqueV1(BaseModel):
    """Critic output — approve/revise with structured findings (backward compatible)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["agent.critique.v1"] = AGENT_CRITIQUE_SCHEMA
    recommendation: CritiqueRecommendation
    reason_codes: list[str] = Field(default_factory=list, max_length=CRITIQUE_REASON_CODE_MAX_COUNT)
    summary: str = Field(default="", max_length=CRITIQUE_REASON_MAX_LEN)
    analysis_warning_count: int = Field(default=0, ge=0)
    findings: list[CritiqueFindingV1] = Field(default_factory=list, max_length=CRITIQUE_FINDING_MAX)
    scope: CritiqueScopeDigest | None = None
    stratum_counts: CritiqueStratumCounts = Field(default_factory=CritiqueStratumCounts)
    engine_version: str = Field(default=CRITIQUE_ENGINE_VERSION, max_length=80)
    algorithm_version: str | None = Field(default=None, max_length=80)
    model_critique_status: CritiqueModelStatus | None = None

    @field_validator("reason_codes")
    @classmethod
    def _normalize_codes(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for item in value:
            code = str(item).strip().lower().replace(" ", "_")[:CRITIQUE_REASON_CODE_MAX_LEN]
            if not code or code in seen:
                continue
            seen.add(code)
            out.append(code)
        return out

    @field_validator("summary", mode="before")
    @classmethod
    def _truncate_summary(cls, value: object) -> str:
        return str(value or "").strip()[:CRITIQUE_REASON_MAX_LEN]

    @field_validator("findings")
    @classmethod
    def _cap_findings(cls, value: list[CritiqueFindingV1]) -> list[CritiqueFindingV1]:
        if len(value) > CRITIQUE_FINDING_MAX:
            raise ValueError(f"findings limited to {CRITIQUE_FINDING_MAX}")
        return value

    @model_validator(mode="after")
    def _merge_codes_and_counts(self) -> Self:
        # Auto-fill reason_codes from finding codes when empty.
        if not self.reason_codes and self.findings:
            merged = merge_reason_codes_from_findings(self.findings)
            object.__setattr__(self, "reason_codes", merged)
        # Refresh stratum_counts from findings when findings present.
        if self.findings:
            object.__setattr__(self, "stratum_counts", count_strata(list(self.findings)))
        return self


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class ArtifactDependencyEdge(BaseModel):
    """Typed depends_on edge on ``agent.artifact.v1``."""

    model_config = ConfigDict(extra="forbid")

    artifact_id: str = Field(..., min_length=1, max_length=80)
    relation: ArtifactDependencyRelation = "requires"


class AgentArtifactV1(BaseModel):
    """Cross-agent typed envelope — payload must use a known content_type."""

    model_config = ConfigDict(extra="forbid")

    artifact_schema: Literal["agent.artifact.v1"] = AGENT_ARTIFACT_SCHEMA
    artifact_id: str = Field(default_factory=lambda: str(uuid.uuid4()), max_length=80)
    kind: AgentArtifactKind
    producer_agent_id: str = Field(..., min_length=1, max_length=64)
    content_type: str = Field(..., min_length=1, max_length=80)
    payload: dict[str, Any] = Field(default_factory=dict)
    source_fingerprint: str | None = Field(default=None, max_length=128)
    source_revision_id: str | None = Field(default=None, max_length=80)
    created_at: str = Field(default_factory=_utc_now_iso, max_length=40)
    parent_artifact_ids: list[str] = Field(default_factory=list, max_length=ARTIFACT_PARENT_MAX)
    depends_on: list[ArtifactDependencyEdge] = Field(
        default_factory=list, max_length=ARTIFACT_DEPENDS_ON_MAX
    )
    supersedes_artifact_id: str | None = Field(default=None, max_length=80)
    retention_class: RetentionClass = "temporary"
    expires_at: str | None = Field(default=None, max_length=40)
    provenance: AgentArtifactProvenance | None = None
    warning_codes: list[str] = Field(default_factory=list, max_length=ARTIFACT_WARNING_MAX)
    mutates_composition: Literal[False] = False

    @field_validator("content_type")
    @classmethod
    def _known_content_type(cls, value: str) -> str:
        cleaned = value.strip()
        if cleaned not in AGENT_CONTENT_TYPES:
            logger.debug(
                "Agent artifact content_type rejected",
                extra={"content_type": cleaned[:80]},
            )
            raise ValueError(f"Unknown artifact content_type: {cleaned}")
        return cleaned

    @field_validator("kind")
    @classmethod
    def _known_kind(cls, value: AgentArtifactKind) -> AgentArtifactKind:
        return value

    @field_validator("warning_codes")
    @classmethod
    def _normalize_warnings(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        for item in value:
            code = str(item).strip()[:ARTIFACT_WARNING_MAX_LEN]
            if code:
                out.append(code)
        return out

    @field_validator("created_at", "expires_at")
    @classmethod
    def _validate_iso_timestamp(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        # Accept Z-suffix UTC ISO-8601; normalize space separator.
        probe = cleaned.replace("Z", "+00:00") if cleaned.endswith("Z") else cleaned
        try:
            datetime.fromisoformat(probe)
        except ValueError as exc:
            raise ValueError("timestamp must be UTC ISO-8601") from exc
        return cleaned

    @model_validator(mode="after")
    def _force_non_mutating_and_validate_payload(self) -> Self:
        if self.mutates_composition is not False:
            raise ValueError("mutates_composition must be false on agent artifacts")
        if self.retention_class == "temporary" and self.expires_at is None:
            # In-memory / session envelopes may omit expires_at; only durable
            # promote / temp SQLite insert enforce TTL. Log at DEBUG.
            logger.debug(
                "Session artifact missing expires_at (allowed in-memory)",
                extra={
                    "artifact_id_prefix": self.artifact_id[:12],
                    "content_type": self.content_type[:80],
                    "retention_class": self.retention_class,
                },
            )
        if self.retention_class == "durable" and self.expires_at is not None:
            raise ValueError("durable artifacts must not set expires_at")
        # Typed payload validate (lazy import avoids circular module init).
        from app.ai_agents.artifact_schemas import validate_artifact_payload

        try:
            validated = validate_artifact_payload(self.content_type, self.payload)
        except ValueError as exc:
            logger.info(
                "Agent artifact payload rejected",
                extra={
                    "code": "artifact_payload_rejected",
                    "content_type": self.content_type[:80],
                },
            )
            raise ValueError(str(exc)) from exc
        # Prefer in-place assignment: returning model_copy from after-validator
        # is unsupported when constructing via __init__.
        object.__setattr__(self, "payload", validated)
        logger.debug(
            "Agent artifact envelope fields",
            extra={
                "artifact_id_prefix": self.artifact_id[:12],
                "content_type": self.content_type[:80],
                "has_source_revision": bool(self.source_revision_id),
                "depends_on_count": len(self.depends_on),
                "retention_class": self.retention_class,
                "has_expires_at": bool(self.expires_at),
                "has_supersedes": bool(self.supersedes_artifact_id),
            },
        )
        return self


class AgentDescriptor(BaseModel):
    """Public discovery descriptor (``agent.descriptor.v1``)."""

    model_config = ConfigDict(extra="forbid")

    descriptor_schema: Literal["agent.descriptor.v1"] = AGENT_DESCRIPTOR_SCHEMA
    id: str = Field(..., min_length=1, max_length=64)
    display_name: str = Field(..., min_length=1, max_length=120)
    capability: MusicAgentCapability
    supported_operations: list[AgentOperation] = Field(..., min_length=1)
    accepted_artifact_types: list[str] = Field(default_factory=list)
    produced_artifact_types: list[str] = Field(default_factory=list)
    required_model_capabilities: list[ModelCapability] = Field(default_factory=list)
    resource: AgentResourceHints = Field(default_factory=AgentResourceHints)
    status: AgentStatus = AgentStatus.READY
    health_detail: str | None = Field(default=None, max_length=200)
    bound_model_id: str | None = Field(default=None, max_length=BOUND_MODEL_ID_MAX)
    mutates_composition: Literal[False] = False

    @model_validator(mode="after")
    def _force_non_mutating(self) -> Self:
        if self.mutates_composition is not False:
            raise ValueError("mutates_composition must be false on agent descriptors")
        return self


class BoundModelPublic(BaseModel):
    """Public fields for a resolved model binding (no secrets/paths)."""

    model_config = ConfigDict(extra="forbid")

    model_id: str = Field(..., max_length=BOUND_MODEL_ID_MAX)
    runtime: str | None = Field(default=None, max_length=64)
    capability: str | None = Field(default=None, max_length=64)
    status: str | None = Field(default=None, max_length=32)
    locality: str | None = Field(default=None, max_length=16)


class AgentWorkflowContext(BaseModel):
    """Immutable typed workflow context — not a mutable blackboard.

    Updates go through ``with_slot`` / ``with_working_draft`` / ``with_artifact_log``
    which return a new frozen instance.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_composition: CompositionV2
    source_fingerprint: str = Field(..., min_length=1, max_length=128)
    working_draft_composition: CompositionV2
    brief: AgentArtifactV1 | None = None
    workflow_plan: AgentArtifactV1 | None = None
    structure_plan: AgentArtifactV1 | None = None
    harmony_artifact: AgentArtifactV1 | None = None
    melody_artifact: AgentArtifactV1 | None = None
    arrangement_candidate: AgentArtifactV1 | None = None
    orchestration_artifact: AgentArtifactV1 | None = None
    expression_artifact: AgentArtifactV1 | None = None
    production_artifact: AgentArtifactV1 | None = None
    critique: AgentArtifactV1 | None = None
    analysis: AgentArtifactV1 | None = None
    artifact_log: tuple[AgentArtifactV1, ...] = ()
    bound_models: dict[str, BoundModelPublic] = Field(default_factory=dict)
    revise_count: int = Field(default=0, ge=0)
    recommendation: CritiqueRecommendation | None = None
    workflow_id: str = Field(default=AGENT_SPINE_WORKFLOW_ID, max_length=64)

    def with_slot(self, name: str, artifact: AgentArtifactV1 | None) -> AgentWorkflowContext:
        if name not in ARTIFACT_SLOT_NAMES:
            raise ValueError(f"Unknown context slot: {name}")
        logger.debug(
            "Workflow context slot update",
            extra={"slot": name, "has_artifact": artifact is not None},
        )
        return self.model_copy(update={name: artifact})

    def with_working_draft(self, composition: CompositionV2) -> AgentWorkflowContext:
        logger.debug("Workflow working_draft replaced")
        return self.model_copy(update={"working_draft_composition": composition})

    def with_artifact_log(self, *artifacts: AgentArtifactV1) -> AgentWorkflowContext:
        if not artifacts:
            return self
        return self.model_copy(update={"artifact_log": self.artifact_log + tuple(artifacts)})

    def with_recommendation(
        self, recommendation: CritiqueRecommendation | None
    ) -> AgentWorkflowContext:
        return self.model_copy(update={"recommendation": recommendation})

    def with_revise_count(self, count: int) -> AgentWorkflowContext:
        return self.model_copy(update={"revise_count": max(0, int(count))})

    def with_bound_models(
        self, bound: Mapping[str, BoundModelPublic]
    ) -> AgentWorkflowContext:
        return self.model_copy(update={"bound_models": dict(bound)})


class AgentRunRequest(BaseModel):
    """Request to invoke a single agent."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(..., min_length=1, max_length=64)
    operation: AgentOperation = AgentOperation.RUN
    context: AgentWorkflowContext
    agent_model_overrides: dict[str, str] = Field(default_factory=dict)
    selection: dict[str, Any] = Field(default_factory=dict)
    # Non-secret agent options (e.g. include_model_critique, revise_on_technical).
    parameters: dict[str, Any] = Field(default_factory=dict)


class AgentRunResult(BaseModel):
    """Result of a single agent invoke — never mutates durable Composition."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(..., min_length=1, max_length=64)
    operation: AgentOperation
    artifacts: list[AgentArtifactV1] = Field(default_factory=list)
    updated_context_slots: dict[str, AgentArtifactV1] = Field(default_factory=dict)
    working_draft_update: CompositionV2 | None = None
    provenance_stage: dict[str, Any] = Field(default_factory=dict)
    warning_codes: list[str] = Field(default_factory=list, max_length=ARTIFACT_WARNING_MAX)
    recommendation: CritiqueRecommendation | None = None
    mutates_composition: Literal[False] = False
    # Set by the single-agent HTTP route. Spine-internal results leave this null.
    operation_summary: OperationSummaryV1 | None = None

    @model_validator(mode="after")
    def _force_non_mutating(self) -> Self:
        if self.mutates_composition is not False:
            raise ValueError("mutates_composition must be false on agent run results")
        return self


logger.info(
    "AI agent schemas loaded",
    extra={
        "capability_count": len(MusicAgentCapability),
        "artifact_kind_count": len(AgentArtifactKind),
        "content_type_count": len(AGENT_CONTENT_TYPES),
        "known_agent_count": len(KNOWN_AGENT_IDS),
    },
)
