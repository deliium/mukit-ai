"""V4 multi-agent discovery, single-agent run, and workflow preview routes.

Never writes projects. Agents return typed artifacts only; Apply is client-side CAS.
"""

from __future__ import annotations

import logging
import time
from typing import Annotated, Any, Literal
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.ai_agents.errors import AgentError, map_agent_error_to_http
from app.ai_agents.progressive_realize import initial_working_draft
from app.ai_agents.registry import (
    ensure_registry,
    get_agent,
    get_descriptor,
    list_descriptors,
)
from app.ai_agents.revision_loop_schemas import (
    RevisionLoopBudgets,
    RevisionMode,
    RevisionPassCandidateV1,
)
from app.ai_agents.schemas import (
    AGENT_SPINE_WORKFLOW_ID,
    AgentDescriptor,
    AgentOperation,
    AgentRunRequest,
    AgentRunResult,
    AgentWorkflowContext,
    CritiqueRecommendation,
)
from app.ai_agents.workflow import run_spine_workflow
from app.composition_schemas import CompositionV2
from app.services.composition_edit_fingerprint import (
    composition_edit_fingerprint,
    edit_fingerprint_log_prefix,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["ai-agents"])


class AgentCatalogResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agents: list[AgentDescriptor]
    workflow_ids: list[str] = Field(default_factory=lambda: [AGENT_SPINE_WORKFLOW_ID])


class AgentRunHttpRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: AgentOperation = AgentOperation.RUN
    composition: CompositionV2
    agent_model_overrides: dict[str, str] = Field(default_factory=dict)
    selection: dict[str, Any] = Field(default_factory=dict)


class AgentWorkflowPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    composition: CompositionV2
    brief: str | None = Field(default=None, max_length=500)
    workflow_id: str = Field(default=AGENT_SPINE_WORKFLOW_ID, max_length=64)
    max_revisions: int = Field(default=0, ge=0, le=8)
    revision_mode: RevisionMode = RevisionMode.OFF
    max_wall_ms: int | None = Field(default=None, ge=1, le=3_600_000)
    max_prompt_tokens: int | None = Field(default=None, ge=1, le=10_000_000)
    agent_model_overrides: dict[str, str] = Field(default_factory=dict)
    selection: dict[str, Any] = Field(default_factory=dict)
    # Optional mid-preview inspectability — default remains session-only.
    project_id: str | None = Field(default=None, max_length=64)
    persist_workspace_artifacts: bool = False
    # Test / advanced: critic parameters (e.g. revise_on_technical).
    critic_parameters: dict[str, Any] = Field(default_factory=dict)


class AgentWorkflowPreviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_id: str
    pipeline_id: str
    candidate: CompositionV2
    source_fingerprint: str
    candidate_fingerprint: str
    artifact_log: list[dict[str, Any]] = Field(default_factory=list)
    critique: dict[str, Any] | None = None
    recommendation: CritiqueRecommendation | None = None
    stages: list[dict[str, Any]] = Field(default_factory=list)
    generation_parameters: dict[str, Any] | None = None
    agent_sequence: list[str] = Field(default_factory=list)
    warning_codes: list[str] = Field(default_factory=list)
    duration_ms: int = 0
    mutates_composition: Literal[False] = False
    # Hint for FE Apply envelope
    operation_type: Literal["multi-agent-apply"] = "multi-agent-apply"
    revision_mode: RevisionMode = RevisionMode.OFF
    max_passes: int = Field(default=0, ge=0, le=8)
    stop_reason: str | None = None
    last_valid_fingerprint: str | None = None
    revision_history: list[dict[str, Any]] = Field(default_factory=list)
    # Session-only playable snapshots per pass for FE audition/compare (not durable).
    pass_candidates: list[RevisionPassCandidateV1] = Field(default_factory=list)
    usage: dict[str, Any] | None = None


def _raise_agent_http(exc: AgentError) -> None:
    status, detail = map_agent_error_to_http(exc)
    raise HTTPException(status_code=status, detail=detail) from exc


def _context_from_composition(composition: CompositionV2) -> AgentWorkflowContext:
    fingerprint = composition_edit_fingerprint(composition)
    return AgentWorkflowContext(
        source_composition=composition,
        source_fingerprint=fingerprint,
        working_draft_composition=initial_working_draft(composition),
    )


@router.get("/agents", response_model=AgentCatalogResponse)
async def list_ai_agents(
    capability: Annotated[str | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
) -> AgentCatalogResponse:
    logger.info("AI agent discovery requested")
    logger.debug(
        "AI agent discovery filters",
        extra={"capability": capability, "status": status},
    )
    ensure_registry()
    agents = list_descriptors(capability=capability, status=status)
    logger.info("AI agent list ready", extra={"count": len(agents)})
    return AgentCatalogResponse(agents=agents)


@router.get("/agents/{agent_id}", response_model=AgentDescriptor)
async def get_ai_agent(agent_id: str) -> AgentDescriptor:
    decoded = unquote(agent_id)
    logger.info("AI agent detail requested", extra={"agent_id": decoded})
    ensure_registry()
    try:
        return get_descriptor(decoded)
    except AgentError as exc:
        _raise_agent_http(exc)
        raise  # pragma: no cover


@router.post("/agents/{agent_id}/run", response_model=AgentRunResult)
async def run_ai_agent(agent_id: str, body: AgentRunHttpRequest) -> AgentRunResult:
    decoded = unquote(agent_id)
    started = time.perf_counter()
    logger.info(
        "AI agent run requested",
        extra={"agent_id": decoded, "operation": body.operation.value},
    )
    ensure_registry()
    try:
        agent = get_agent(decoded)
        context = _context_from_composition(body.composition)
        request = AgentRunRequest(
            agent_id=decoded,
            operation=body.operation,
            context=context,
            agent_model_overrides=body.agent_model_overrides,
            selection=body.selection,
        )
        result = await agent.run(request)
    except AgentError as exc:
        _raise_agent_http(exc)
        raise  # pragma: no cover
    duration_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "AI agent run completed",
        extra={
            "agent_id": decoded,
            "operation": body.operation.value,
            "duration_ms": duration_ms,
            "artifact_count": len(result.artifacts),
        },
    )
    logger.debug(
        "AI agent run artifact kinds",
        extra={"kinds": [a.kind.value for a in result.artifacts]},
    )
    return result


@router.post("/agents/workflows/preview", response_model=AgentWorkflowPreviewResponse)
async def preview_agent_workflow(
    body: AgentWorkflowPreviewRequest,
    request: Request,
) -> AgentWorkflowPreviewResponse:
    logger.info(
        "AI agent workflow preview requested",
        extra={
            "workflow_id": body.workflow_id,
            "max_revisions": body.max_revisions,
            "revision_mode": body.revision_mode.value,
            "brief_len": len(body.brief or ""),
        },
    )
    if body.workflow_id not in {AGENT_SPINE_WORKFLOW_ID, "multi_agent_v4"}:
        raise HTTPException(
            status_code=404,
            detail={"code": "workflow_not_found", "message": f"Unknown workflow: {body.workflow_id}"},
        )
    ensure_registry()
    budgets = None
    if body.max_wall_ms is not None or body.max_prompt_tokens is not None:
        budgets = RevisionLoopBudgets(
            max_wall_ms=body.max_wall_ms,
            max_prompt_tokens=body.max_prompt_tokens,
        )

    try:
        result = await run_spine_workflow(
            body.composition,
            max_revisions=body.max_revisions,
            revision_mode=body.revision_mode.value,
            budgets=budgets,
            cancel_check=request.is_disconnected,
            agent_model_overrides=body.agent_model_overrides,
            selection=body.selection,
            workflow_id=AGENT_SPINE_WORKFLOW_ID,
            critic_parameters=body.critic_parameters or None,
        )
    except AgentError as exc:
        _raise_agent_http(exc)
        raise  # pragma: no cover

    critique_payload = None
    if result.context.critique is not None:
        critique_payload = result.context.critique.model_dump(mode="json")

    if body.persist_workspace_artifacts and body.project_id:
        from app.services.agent_artifact_workspace import insert_temporary

        try:
            inserted = 0
            for artifact in result.artifact_log[:64]:
                insert_temporary(body.project_id, artifact)
                inserted += 1
        except AgentError as exc:
            _raise_agent_http(exc)
            raise  # pragma: no cover
        logger.info(
            "Optional workspace temporary artifacts staged",
            extra={
                "project_id_prefix": body.project_id[:12],
                "inserted_count": inserted,
                "workflow_id": result.workflow_id,
            },
        )
    elif body.persist_workspace_artifacts and not body.project_id:
        logger.info(
            "persist_workspace_artifacts ignored without project_id",
            extra={"workflow_id": result.workflow_id},
        )

    history_payload = [
        rec.model_dump(mode="json") if hasattr(rec, "model_dump") else dict(rec)
        for rec in (result.revision_history or ())
    ]
    pass_candidate_payload: list[RevisionPassCandidateV1] = []
    for item in result.pass_candidates or ():
        if isinstance(item, RevisionPassCandidateV1):
            pass_candidate_payload.append(item)
        elif hasattr(item, "model_dump"):
            pass_candidate_payload.append(
                RevisionPassCandidateV1.model_validate(item.model_dump(mode="json"))
            )
        else:
            pass_candidate_payload.append(RevisionPassCandidateV1.model_validate(item))
    usage_payload = None
    if result.usage is not None:
        usage_payload = (
            result.usage.model_dump(mode="json")
            if hasattr(result.usage, "model_dump")
            else dict(result.usage)
        )
    stop_reason = (
        result.stop_reason.value
        if result.stop_reason is not None and hasattr(result.stop_reason, "value")
        else (str(result.stop_reason) if result.stop_reason else None)
    )
    revision_mode = (
        result.revision_mode
        if isinstance(result.revision_mode, RevisionMode)
        else RevisionMode(
            getattr(result.revision_mode, "value", None) or body.revision_mode.value
        )
    )

    logger.info(
        "AI agent workflow preview ready",
        extra={
            "workflow_id": result.workflow_id,
            "agent_sequence_len": len(result.agent_sequence),
            "candidate_prefix": edit_fingerprint_log_prefix(result.candidate_fingerprint),
            "duration_ms": result.duration_ms,
            "recommendation": result.recommendation.value if result.recommendation else None,
            "revision_mode": revision_mode.value,
            "stop_reason": stop_reason,
            "pass_count": len(history_payload),
            "pass_candidate_count": len(pass_candidate_payload),
            "persist_workspace": bool(body.persist_workspace_artifacts and body.project_id),
        },
    )
    # Avoid logging full history / candidate payloads at INFO.
    logger.debug(
        "Workflow revision history summary",
        extra={
            "pass_indices": [h.get("pass_index") for h in history_payload[:8]],
            "stop_reason": stop_reason,
            "pass_candidate_indices": [c.pass_index for c in pass_candidate_payload[:8]],
        },
    )
    return AgentWorkflowPreviewResponse(
        workflow_id=result.workflow_id,
        pipeline_id=result.pipeline_id,
        candidate=result.candidate,
        source_fingerprint=result.source_fingerprint,
        candidate_fingerprint=result.candidate_fingerprint,
        artifact_log=[a.model_dump(mode="json") for a in result.artifact_log],
        critique=critique_payload,
        recommendation=result.recommendation,
        stages=result.stages,
        generation_parameters=result.generation_parameters,
        agent_sequence=result.agent_sequence,
        warning_codes=result.warning_codes,
        duration_ms=result.duration_ms,
        mutates_composition=False,
        operation_type="multi-agent-apply",
        revision_mode=revision_mode,
        max_passes=int(result.max_passes or 0),
        stop_reason=stop_reason,
        last_valid_fingerprint=result.last_valid_fingerprint,
        revision_history=history_payload,
        pass_candidates=pass_candidate_payload,
        usage=usage_payload,
    )
