"""Typed non-playable content schemas for ``agent.artifact.v1`` payloads.

Canonical playable notes live only on ``composition.v2`` ``tracks[].events[]``.
These models mirror ``composition.plan.v1`` forbidden-playable discipline.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal, Type

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from app.ai_agents.schemas import (
    AgentBriefV1,
    AgentCritiqueV1,
    AgentWorkflowPlanV1,
    CritiqueRecommendation,
)
from app.composition_plan_schemas import CompositionPlan
from app.schemas import KEY_PATTERN, SUPPORTED_TRACK_ROLES

logger = logging.getLogger(__name__)

# --- Schema ids ----------------------------------------------------------------

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
COMPOSITION_PLAN_SCHEMA: Literal["composition.plan.v1"] = "composition.plan.v1"
COMPOSITION_ANALYSIS_SCHEMA: Literal["composition.analysis.v1"] = "composition.analysis.v1"
MUSIC_ANALYSIS_BOUNDED_SCHEMA: Literal["composition.analysis.bounded.v1"] = (
    "composition.analysis.bounded.v1"
)

# Shared with composition.plan.v1 — never allow competing playable representations.
FORBIDDEN_PLAYABLE_TOP_LEVEL: frozenset[str] = frozenset(
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

MUSIC_ANALYSIS_DURABLE_MAX_BYTES = 8_192
MUSIC_ANALYSIS_WARNING_CODE_MAX = 32
MUSIC_ANALYSIS_WARNING_CODE_MAX_LEN = 80

PLAN_SECTION_MAX = 32
PLAN_CHORD_EVENT_MAX = 64
PLAN_MOTIF_ENTRY_MAX = 16
PLAN_TRACK_HINT_MAX = 16
PLAN_INSTRUMENT_HINT_MAX = 16
PLAN_REVISION_TARGET_MAX = 16
PATCH_OP_REF_MAX = 32
PATCH_OP_REF_MAX_LEN = 80

DensityBand = Literal["sparse", "moderate", "dense"]
RealizeServiceRef = Literal[
    "reharmonize_candidate",
    "motif_apply",
    "arrangement_candidate",
    "development_candidate",
    "passthrough",
]

TYPED_ARTIFACT_CONTENT_TYPES: frozenset[str] = frozenset(
    {
        "agent.brief.v1",
        "agent.workflow_plan.v1",
        "agent.critique.v1",
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
        COMPOSITION_PLAN_SCHEMA,
        COMPOSITION_ANALYSIS_SCHEMA,
        MUSIC_ANALYSIS_BOUNDED_SCHEMA,
    }
)


def _reject_forbidden_playable(data: Any, *, schema_id: str) -> Any:
    if not isinstance(data, dict):
        return data
    forbidden = sorted(FORBIDDEN_PLAYABLE_TOP_LEVEL.intersection(data.keys()))
    if forbidden:
        logger.debug(
            "Artifact payload rejected: forbidden playable fields",
            extra={"code": "artifact_playable_fields_forbidden", "schema": schema_id},
        )
        raise ValueError(
            "Artifact payload must not include playable score fields: " + ", ".join(forbidden)
        )
    return data


class _NonPlayableBase(BaseModel):
    """Base with extra=forbid and shared playable-field gate."""

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def reject_forbidden_playable_fields(cls, data: Any) -> Any:
        schema_id = getattr(cls, "schema_version", None)
        if hasattr(schema_id, "default"):
            schema_id = schema_id.default
        return _reject_forbidden_playable(data, schema_id=str(schema_id or cls.__name__))


class FormPlanSection(_NonPlayableBase):
    label: str = Field(..., min_length=1, max_length=64)
    start_bar: int = Field(..., ge=1)
    bar_count: int = Field(..., ge=1, le=256)
    density: DensityBand = "moderate"


class AgentFormPlanV1(_NonPlayableBase):
    """Sections / density outline only — not a playable score."""

    schema_version: Literal["agent.form_plan.v1"] = AGENT_FORM_PLAN_SCHEMA
    sections: list[FormPlanSection] = Field(..., min_length=1, max_length=PLAN_SECTION_MAX)
    global_density: DensityBand = "moderate"
    comment: str | None = Field(default=None, max_length=240)


class HarmonyPlanEvent(_NonPlayableBase):
    bar: int = Field(..., ge=1)
    chord: str = Field(..., min_length=1, max_length=32)
    function: str | None = Field(default=None, max_length=64)
    cadence: str | None = Field(default=None, max_length=64)


class AgentHarmonyPlanV1(_NonPlayableBase):
    """Chord / timeline intent — never invent audible notes from this alone."""

    schema_version: Literal["agent.harmony_plan.v1"] = AGENT_HARMONY_PLAN_SCHEMA
    key: str | None = Field(default=None, max_length=32)
    # Named chord_events (not ``events``) to avoid playable-field gate collisions.
    chord_events: list[HarmonyPlanEvent] = Field(
        default_factory=list, max_length=PLAN_CHORD_EVENT_MAX
    )
    comment: str | None = Field(default=None, max_length=240)

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        key = " ".join(value.strip().split())
        if key and not KEY_PATTERN.match(key):
            raise ValueError("Key must use format like 'C minor' or 'F# major'")
        return key or None


class MotifPlanEntry(_NonPlayableBase):
    motif_label: str = Field(..., min_length=1, max_length=64)
    role: str | None = Field(default=None, max_length=40)
    recurrence: str | None = Field(default=None, max_length=80)
    section_labels: list[str] = Field(default_factory=list, max_length=PLAN_SECTION_MAX)

    @field_validator("role")
    @classmethod
    def validate_role(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        if normalized and normalized not in SUPPORTED_TRACK_ROLES:
            raise ValueError(f"Unsupported track role: {value}")
        return normalized or None


class AgentMotifPlanV1(_NonPlayableBase):
    """Motif / theme recurrence plan — relative intent only."""

    schema_version: Literal["agent.motif_plan.v1"] = AGENT_MOTIF_PLAN_SCHEMA
    motifs: list[MotifPlanEntry] = Field(default_factory=list, max_length=PLAN_MOTIF_ENTRY_MAX)
    comment: str | None = Field(default=None, max_length=240)


class ArrangementTrackHint(_NonPlayableBase):
    role: str = Field(..., min_length=1, max_length=40)
    instrument_family: str | None = Field(default=None, max_length=80)
    texture: str | None = Field(default=None, max_length=80)
    density: DensityBand = "moderate"

    @field_validator("role")
    @classmethod
    def validate_role(cls, value: str) -> str:
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        if normalized not in SUPPORTED_TRACK_ROLES:
            raise ValueError(f"Unsupported track role: {value}")
        return normalized


class AgentArrangementPlanV1(_NonPlayableBase):
    """Track topology / texture intent — not a realized arrangement.candidate."""

    schema_version: Literal["agent.arrangement_plan.v1"] = AGENT_ARRANGEMENT_PLAN_SCHEMA
    track_hints: list[ArrangementTrackHint] = Field(
        default_factory=list, max_length=PLAN_TRACK_HINT_MAX
    )
    texture_summary: str | None = Field(default=None, max_length=240)
    comment: str | None = Field(default=None, max_length=240)


class OrchestrationInstrumentHint(_NonPlayableBase):
    family: str = Field(..., min_length=1, max_length=80)
    role: str | None = Field(default=None, max_length=40)
    midi_min: int | None = Field(default=None, ge=0, le=127)
    midi_max: int | None = Field(default=None, ge=0, le=127)

    @model_validator(mode="after")
    def validate_window(self) -> OrchestrationInstrumentHint:
        if self.midi_min is not None and self.midi_max is not None and self.midi_min > self.midi_max:
            raise ValueError("midi_min must be <= midi_max")
        return self


class AgentOrchestrationPlanV1(_NonPlayableBase):
    schema_version: Literal["agent.orchestration_plan.v1"] = AGENT_ORCHESTRATION_PLAN_SCHEMA
    instruments: list[OrchestrationInstrumentHint] = Field(
        default_factory=list, max_length=PLAN_INSTRUMENT_HINT_MAX
    )
    comment: str | None = Field(default=None, max_length=240)


class AgentPerformancePlanV1(_NonPlayableBase):
    schema_version: Literal["agent.performance_plan.v1"] = AGENT_PERFORMANCE_PLAN_SCHEMA
    velocity_curve: str | None = Field(default=None, max_length=64)
    expression_notes: str | None = Field(default=None, max_length=240)
    cc_intent: list[str] = Field(default_factory=list, max_length=16)


class AgentProductionPlanV1(_NonPlayableBase):
    """Mix / render intent — never mutates composition."""

    schema_version: Literal["agent.production_plan.v1"] = AGENT_PRODUCTION_PLAN_SCHEMA
    mix_notes: str | None = Field(default=None, max_length=240)
    render_intent: str | None = Field(default=None, max_length=120)
    mutates_composition: Literal[False] = False


class AgentRevisionPlanV1(_NonPlayableBase):
    """Critic-driven revise targets / stop criteria."""

    schema_version: Literal["agent.revision_plan.v1"] = AGENT_REVISION_PLAN_SCHEMA
    critique_recommendation: CritiqueRecommendation = CritiqueRecommendation.REVISE
    revise_targets: list[str] = Field(default_factory=list, max_length=PLAN_REVISION_TARGET_MAX)
    stop_criteria: list[str] = Field(default_factory=list, max_length=PLAN_REVISION_TARGET_MAX)
    comment: str | None = Field(default=None, max_length=240)

    @field_validator("revise_targets", "stop_criteria")
    @classmethod
    def _truncate_items(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        for item in value:
            cleaned = str(item).strip()[:80]
            if cleaned:
                out.append(cleaned)
        return out


class AgentCompositionPatchV1(_NonPlayableBase):
    """Structured patch descriptor for progressive realize — not a full alternate V2."""

    schema_version: Literal["agent.composition_patch.v1"] = AGENT_COMPOSITION_PATCH_SCHEMA
    realize_service: RealizeServiceRef = "passthrough"
    op_refs: list[str] = Field(default_factory=list, max_length=PATCH_OP_REF_MAX)
    recipe: str | None = Field(default=None, max_length=240)
    source_fingerprint: str | None = Field(default=None, max_length=128)
    # Never embed tracks[].events[] — reject via forbidden gate + nested check.

    @field_validator("op_refs")
    @classmethod
    def _normalize_ops(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        for item in value:
            cleaned = str(item).strip()[:PATCH_OP_REF_MAX_LEN]
            if cleaned:
                out.append(cleaned)
        return out

    @model_validator(mode="before")
    @classmethod
    def reject_embedded_score(cls, data: Any) -> Any:
        data = _reject_forbidden_playable(data, schema_id=AGENT_COMPOSITION_PATCH_SCHEMA)
        if isinstance(data, dict):
            for nested_key in ("candidate_composition", "composition_v2", "score", "draft"):
                nested = data.get(nested_key)
                if isinstance(nested, dict) and (
                    "tracks" in nested or "events" in nested or "notes" in nested
                ):
                    raise ValueError(
                        "CompositionPatch must not embed a playable composition score"
                    )
        return data


class AgentRenderPlanV1(_NonPlayableBase):
    """Neural / WAV egress intent / job refs only."""

    schema_version: Literal["agent.render_plan.v1"] = AGENT_RENDER_PLAN_SCHEMA
    job_ref: str | None = Field(default=None, max_length=120)
    fidelity_class: str | None = Field(default=None, max_length=64)
    comment: str | None = Field(default=None, max_length=240)
    mutates_composition: Literal[False] = False


class MusicAnalysisBoundedProjectionV1(_NonPlayableBase):
    """Durable MusicAnalysis form — warning codes + counts + scope digests only."""

    schema_version: Literal["composition.analysis.bounded.v1"] = MUSIC_ANALYSIS_BOUNDED_SCHEMA
    source_schema_version: Literal["composition.v2"] = "composition.v2"
    source_fingerprint: str = Field(..., min_length=16, max_length=128)
    algorithm_version: str | None = Field(default=None, max_length=80)
    status: str = Field(default="ok", max_length=32)
    warning_codes: list[str] = Field(default_factory=list, max_length=MUSIC_ANALYSIS_WARNING_CODE_MAX)
    warning_count: int = Field(default=0, ge=0)
    scope_digest: str | None = Field(default=None, max_length=128)
    section_count: int = Field(default=0, ge=0)
    track_count: int = Field(default=0, ge=0)

    @field_validator("warning_codes")
    @classmethod
    def _normalize_codes(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for item in value:
            code = str(item).strip()[:MUSIC_ANALYSIS_WARNING_CODE_MAX_LEN]
            if not code or code in seen:
                continue
            seen.add(code)
            out.append(code)
        return out


_CONTENT_TYPE_MODELS: dict[str, Type[BaseModel]] = {
    "agent.brief.v1": AgentBriefV1,
    "agent.workflow_plan.v1": AgentWorkflowPlanV1,
    "agent.critique.v1": AgentCritiqueV1,
    AGENT_FORM_PLAN_SCHEMA: AgentFormPlanV1,
    AGENT_HARMONY_PLAN_SCHEMA: AgentHarmonyPlanV1,
    AGENT_MOTIF_PLAN_SCHEMA: AgentMotifPlanV1,
    AGENT_ARRANGEMENT_PLAN_SCHEMA: AgentArrangementPlanV1,
    AGENT_ORCHESTRATION_PLAN_SCHEMA: AgentOrchestrationPlanV1,
    AGENT_PERFORMANCE_PLAN_SCHEMA: AgentPerformancePlanV1,
    AGENT_PRODUCTION_PLAN_SCHEMA: AgentProductionPlanV1,
    AGENT_REVISION_PLAN_SCHEMA: AgentRevisionPlanV1,
    AGENT_COMPOSITION_PATCH_SCHEMA: AgentCompositionPatchV1,
    AGENT_RENDER_PLAN_SCHEMA: AgentRenderPlanV1,
    COMPOSITION_PLAN_SCHEMA: CompositionPlan,
    MUSIC_ANALYSIS_BOUNDED_SCHEMA: MusicAnalysisBoundedProjectionV1,
}


def project_music_analysis_bounded(payload: dict[str, Any]) -> dict[str, Any]:
    """Project full ``composition.analysis.v1`` into a durable bounded form."""
    warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
    codes: list[str] = []
    for item in warnings[:MUSIC_ANALYSIS_WARNING_CODE_MAX]:
        if isinstance(item, dict) and item.get("code"):
            codes.append(str(item["code"])[:MUSIC_ANALYSIS_WARNING_CODE_MAX_LEN])
        elif isinstance(item, str):
            codes.append(item[:MUSIC_ANALYSIS_WARNING_CODE_MAX_LEN])
    resolved = payload.get("resolved_scope") if isinstance(payload.get("resolved_scope"), dict) else {}
    scope_digest = None
    if resolved:
        scope_digest = json.dumps(resolved, sort_keys=True, separators=(",", ":"))[:128]
    sections = payload.get("section_summaries") if isinstance(payload.get("section_summaries"), list) else []
    projected = MusicAnalysisBoundedProjectionV1(
        source_fingerprint=str(payload.get("source_fingerprint") or "")[:128],
        algorithm_version=(
            str(payload["algorithm_version"])[:80] if payload.get("algorithm_version") else None
        ),
        status=str(payload.get("status") or "ok")[:32],
        warning_codes=codes,
        warning_count=0,  # overwritten from deduped codes below
        scope_digest=scope_digest,
        section_count=len(sections),
        track_count=int(resolved.get("track_count") or 0) if isinstance(resolved, dict) else 0,
    )
    # Align count with deduped warning_codes from the model validator.
    projected = projected.model_copy(update={"warning_count": len(projected.warning_codes)})
    return projected.model_dump(mode="json")


def validate_artifact_payload(content_type: str, payload: dict[str, Any] | Any) -> dict[str, Any]:
    """Validate payload against the typed content model for ``content_type``.

    Raises ``ValueError`` with a stable message; callers map to workspace error codes.
    """
    cleaned_type = str(content_type or "").strip()
    if not isinstance(payload, dict):
        logger.info(
            "Artifact payload rejected",
            extra={"code": "artifact_payload_rejected", "content_type": cleaned_type[:80]},
        )
        raise ValueError("artifact payload must be an object")

    # Soft allow-listed legacy session types: forbid playable fields only.
    if cleaned_type not in _CONTENT_TYPE_MODELS:
        if cleaned_type == COMPOSITION_ANALYSIS_SCHEMA:
            return _validate_or_bound_analysis(payload)
        _reject_forbidden_playable(payload, schema_id=cleaned_type)
        logger.debug(
            "Artifact payload allow-list soft validate",
            extra={"content_type": cleaned_type[:80]},
        )
        return dict(payload)

    model = _CONTENT_TYPE_MODELS[cleaned_type]
    try:
        if cleaned_type == COMPOSITION_PLAN_SCHEMA:
            validated = CompositionPlan.model_validate(payload)
        else:
            validated = model.model_validate(payload)
    except (ValidationError, ValueError) as exc:
        msg = str(exc)
        code = "artifact_payload_rejected"
        if "playable" in msg.lower() or "forbidden" in msg.lower():
            code = "artifact_playable_fields_forbidden"
        logger.info(
            "Artifact payload rejected",
            extra={"code": code, "content_type": cleaned_type[:80]},
        )
        raise ValueError(msg) from exc

    dumped = validated.model_dump(mode="json")
    logger.debug(
        "Artifact payload validated",
        extra={"content_type": cleaned_type[:80], "field_count": len(dumped)},
    )
    return dumped


def _validate_or_bound_analysis(payload: dict[str, Any]) -> dict[str, Any]:
    """Session may carry full analysis; durable promote uses bounded projection + byte cap."""
    _reject_forbidden_playable(payload, schema_id=COMPOSITION_ANALYSIS_SCHEMA)
    if payload.get("schema_version") == MUSIC_ANALYSIS_BOUNDED_SCHEMA:
        return MusicAnalysisBoundedProjectionV1.model_validate(payload).model_dump(mode="json")
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    if len(raw.encode("utf-8")) > MUSIC_ANALYSIS_DURABLE_MAX_BYTES:
        # Oversized full reports are rejected for durable use; session callers may
        # still store the envelope in memory — promote path must call project_*.
        logger.info(
            "Artifact payload rejected",
            extra={
                "code": "artifact_payload_rejected",
                "content_type": COMPOSITION_ANALYSIS_SCHEMA,
                "reason": "analysis_over_durable_cap",
            },
        )
        raise ValueError("composition.analysis.v1 exceeds durable byte cap; use bounded projection")
    # Under cap: accept as session form but still strip nothing here.
    return dict(payload)


def typed_schema_load_count() -> int:
    return len(_CONTENT_TYPE_MODELS)


logger.info(
    "Agent artifact typed schemas loaded",
    extra={
        "schema_load_count": typed_schema_load_count(),
        "typed_content_type_count": len(TYPED_ARTIFACT_CONTENT_TYPES),
        "forbidden_playable_count": len(FORBIDDEN_PLAYABLE_TOP_LEVEL),
    },
)
