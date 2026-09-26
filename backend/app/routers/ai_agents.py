"""V4 multi-agent discovery, single-agent run, and workflow preview routes.

Never writes projects. Agents return typed artifacts only; Apply is client-side CAS.
"""

from __future__ import annotations

import asyncio
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
from app.operation_trace import (
    OperationRunIdError,
    adopt_operation_run_id,
    build_summary,
    is_run_cancelled,
    mark_run_cancelled,
    note_failure,
    note_run_outcome,
    operation_span,
)
from app.operation_trace_schemas import OperationSummaryV1

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["ai-agents"])


def _guard_autonomous_run(run_id: str, action: str) -> None:
    """Editor-or-owner gate for autonomous routes that load a project. No-op when the flag is off."""
    from app.collaboration_settings import collaboration_enabled
    from app.routers.collaboration_guard import enforce_current
    from app.services.autonomous_composer_store import get_run

    if not collaboration_enabled():
        return
    run = get_run(run_id)
    enforce_current(run.project_id, action)


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
    operation_run_id: str | None = Field(default=None, max_length=64)


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
    operation_run_id: str | None = Field(default=None, max_length=64)


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
    operation_summary: OperationSummaryV1 | None = None


def _raise_agent_http(exc: AgentError) -> None:
    status, detail = map_agent_error_to_http(exc)
    raise HTTPException(status_code=status, detail=detail) from exc


def _adopt_run_id(raw: str | None) -> str:
    try:
        return adopt_operation_run_id(raw)
    except OperationRunIdError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "operation_run_id_invalid",
                "message": "operation_run_id must be a UUID string",
            },
        ) from exc


async def _watch_disconnect(request: Request, run_id: str) -> None:
    """Poll disconnect until the route cancels this task.

    ``is_disconnected`` reads the ASGI receive channel. Only this watcher calls
    it, so the between-agent probe can read the cancel event without a second
    receive.
    """
    logger.debug("Disconnect watcher started", extra={"run_id": run_id})
    try:
        while True:
            try:
                disconnected = await asyncio.wait_for(request.is_disconnected(), timeout=0.05)
            except TimeoutError:
                disconnected = False
            if disconnected:
                logger.info("Client disconnected; cancelling run", extra={"run_id": run_id})
                mark_run_cancelled(run_id)
                return
            await asyncio.sleep(0.05)
    except asyncio.CancelledError:
        logger.debug("Disconnect watcher stopped", extra={"run_id": run_id})
        raise


async def _stop_watcher(watcher: asyncio.Task[None]) -> None:
    watcher.cancel()
    try:
        await asyncio.wait_for(watcher, timeout=0.2)
    except (asyncio.CancelledError, TimeoutError):
        logger.debug("Disconnect watcher joined", extra={"timed_out": watcher.done() is False})


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
async def run_ai_agent(
    agent_id: str,
    body: AgentRunHttpRequest,
    request: Request,
) -> AgentRunResult:
    decoded = unquote(agent_id)
    run_id = _adopt_run_id(body.operation_run_id)
    started = time.perf_counter()
    logger.info(
        "AI agent run requested",
        extra={"agent_id": decoded, "operation": body.operation.value, "run_id": run_id},
    )
    ensure_registry()
    async with operation_span("run", run_id=run_id) as span:
        watcher = asyncio.create_task(_watch_disconnect(request, run_id))
        try:
            async with operation_span("agent", agent_id=decoded):
                try:
                    agent = get_agent(decoded)
                    context = _context_from_composition(body.composition)
                    agent_request = AgentRunRequest(
                        agent_id=decoded,
                        operation=body.operation,
                        context=context,
                        agent_model_overrides=body.agent_model_overrides,
                        selection=body.selection,
                    )
                    result = await agent.run(agent_request)
                except KeyboardInterrupt:
                    raise
                except SystemExit:
                    note_failure("operation_model_crashed", error_type="SystemExit")
                    logger.error(
                        "Agent run crashed",
                        extra={
                            "failure_code": "operation_model_crashed",
                            "error_type": "SystemExit",
                            "run_id": run_id,
                            "model_id": None,
                        },
                    )
                    note_run_outcome(status="failed", stop_reason="operation_model_crashed")
                    result = AgentRunResult(
                        agent_id=decoded,
                        operation=body.operation,
                        warning_codes=["operation_model_crashed"],
                    )
                except AgentError as exc:
                    _raise_agent_http(exc)
                    raise  # pragma: no cover
        finally:
            await _stop_watcher(watcher)
    summary = build_summary(span)
    duration_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "AI agent run completed",
        extra={
            "agent_id": decoded,
            "operation": body.operation.value,
            "duration_ms": duration_ms,
            "artifact_count": len(result.artifacts),
            "run_id": run_id,
            "status": summary.status,
        },
    )
    logger.debug(
        "AI agent run artifact kinds",
        extra={"kinds": [a.kind.value for a in result.artifacts]},
    )
    return result.model_copy(update={"operation_summary": summary})


@router.post("/agents/workflows/preview", response_model=AgentWorkflowPreviewResponse)
async def preview_agent_workflow(
    body: AgentWorkflowPreviewRequest,
    request: Request,
) -> AgentWorkflowPreviewResponse:
    run_id = _adopt_run_id(body.operation_run_id)
    logger.info(
        "AI agent workflow preview requested",
        extra={
            "workflow_id": body.workflow_id,
            "max_revisions": body.max_revisions,
            "revision_mode": body.revision_mode.value,
            "brief_len": len(body.brief or ""),
            "run_id": run_id,
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

    async def _cancel_check() -> bool:
        if is_run_cancelled(run_id):
            logger.info("Workflow preview cancel probe", extra={"run_id": run_id})
            return True
        return False

    async with operation_span("run", run_id=run_id) as span:
        watcher = asyncio.create_task(_watch_disconnect(request, run_id))
        try:
            result = await run_spine_workflow(
                body.composition,
                max_revisions=body.max_revisions,
                revision_mode=body.revision_mode.value,
                budgets=budgets,
                cancel_check=_cancel_check,
                agent_model_overrides=body.agent_model_overrides,
                selection=body.selection,
                workflow_id=AGENT_SPINE_WORKFLOW_ID,
                critic_parameters=body.critic_parameters or None,
            )
        except AgentError as exc:
            _raise_agent_http(exc)
            raise  # pragma: no cover
        finally:
            await _stop_watcher(watcher)
    operation_summary = build_summary(span)

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
            "run_id": run_id,
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
        operation_summary=operation_summary,
    )


def _autonomous_http(exc: Exception) -> None:
    code = getattr(exc, "code", "autonomous_run_invalid")
    status = 404 if code in {"autonomous_run_not_found", "autonomous_stage_not_found"} else 409 if code in {
        "project_revision_conflict",
        "autonomous_run_limit",
        "autonomous_run_not_resumable",
        "autonomous_revision_not_head",
        "autonomous_stage_not_rejectable",
    } else 422
    raise HTTPException(status_code=status, detail={"code": code, "message": code}) from exc


@router.post("/agents/autonomous/plans", response_model=None)
async def preview_autonomous_plan(body: dict[str, Any]) -> dict[str, Any]:
    from app.autonomous_composer_schemas import AutonomousPlanError, CreativeBriefV1
    from app.services.autonomous_composer import preview_compiled_plan

    try:
        brief_body = body.get("brief") if isinstance(body.get("brief"), dict) else body
        brief = CreativeBriefV1.model_validate(brief_body)
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "autonomous_brief_invalid", "message": "autonomous_brief_invalid"},
        ) from exc
    try:
        plan = preview_compiled_plan(brief)
    except AutonomousPlanError as exc:
        _autonomous_http(exc)
        raise
    return plan.model_dump(mode="json")


@router.post("/agents/autonomous/runs", response_model=None)
async def start_autonomous_run(body: dict[str, Any], request: Request) -> dict[str, Any]:
    from app.autonomous_composer_schemas import AutonomousPlanError, AutonomousRunStartV1, CreativeBriefV1
    from app.operation_trace import build_summary, is_run_cancelled, operation_span
    from app.services.autonomous_composer import execute_autonomous_run, prepare_run, run_view
    from app.services.autonomous_composer_store import AutonomousStoreError

    try:
        if body.get("schema_version") == "creative.brief.v1" or "brief" not in body:
            brief = CreativeBriefV1.model_validate(body)
            start = AutonomousRunStartV1(
                brief=brief,
                project_id=body.get("project_id"),
                include_rendering=bool(body.get("include_rendering", False)),
                render_approval=body.get("render_approval") or "required",
                seed=int(body.get("seed") or 0),
                operation_run_id=body.get("operation_run_id"),
                expected_working_version=body.get("expected_working_version"),
                expected_head_revision_id=body.get("expected_head_revision_id"),
                expected_source_fingerprint=body.get("expected_source_fingerprint"),
                max_agent_operations=body.get("max_agent_operations"),
                autonomy_mode=body.get("autonomy_mode") or "autonomous",
            )
        else:
            start = AutonomousRunStartV1.model_validate(body)
            brief = start.brief
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "autonomous_brief_invalid", "message": "autonomous_brief_invalid"},
        ) from exc
    if start.project_id:
        from app.routers.collaboration_guard import enforce_current

        enforce_current(start.project_id, "write_score")
    operation_run_id = _adopt_run_id(start.operation_run_id)
    try:
        record = prepare_run(
            brief,
            project_id=start.project_id,
            operation_run_id=operation_run_id,
            include_rendering=start.include_rendering,
            seed=start.seed,
            expected_working_version=start.expected_working_version,
            expected_head_revision_id=start.expected_head_revision_id,
            expected_source_fingerprint=start.expected_source_fingerprint,
            autonomy_mode=start.autonomy_mode,
            db_path=None,
        )
    except (AutonomousPlanError, AutonomousStoreError) as exc:
        _autonomous_http(exc)
        raise
    summary = None
    if record.status == "pending":
        async def _cancel_check() -> bool:
            return is_run_cancelled(operation_run_id)

        async with operation_span("run", run_id=operation_run_id) as span:
            watcher = asyncio.create_task(_watch_disconnect(request, operation_run_id))
            try:
                await execute_autonomous_run(
                    record.id,
                    cancel_check=_cancel_check,
                    render_approval=start.render_approval,
                    max_agent_operations=start.max_agent_operations,
                )
            finally:
                await _stop_watcher(watcher)
        summary = build_summary(span).model_dump(mode="json")
    return run_view(record.id, summary=summary).model_dump(mode="json")


@router.get("/agents/autonomous/runs/{run_id}")
async def get_autonomous_run(run_id: str) -> dict[str, Any]:
    from app.services.autonomous_composer import run_view
    from app.services.autonomous_composer_store import (
        AutonomousStoreError,
        reconcile_interrupted_stages,
    )

    try:
        _guard_autonomous_run(run_id, "read")
        reconcile_interrupted_stages(run_id)
        return run_view(run_id).model_dump(mode="json")
    except AutonomousStoreError as exc:
        _autonomous_http(exc)
        raise


@router.post("/agents/autonomous/runs/{run_id}/cancel")
async def cancel_autonomous_run(run_id: str) -> dict[str, Any]:
    from app.operation_trace import mark_run_cancelled
    from app.services.autonomous_composer import run_view
    from app.services.autonomous_composer_store import (
        AutonomousStoreError,
        get_run,
        update_run_fields,
        update_stage_status,
    )

    try:
        run = get_run(run_id)
    except AutonomousStoreError as exc:
        _autonomous_http(exc)
        raise
    if run.status in {"completed", "failed", "cancelled"}:
        return run_view(run_id).model_dump(mode="json")
    mark_run_cancelled(run.operation_run_id)
    for stage in run.stages:
        if stage.status == "running":
            update_stage_status(
                run_id,
                stage.stage_id,
                "failed",
                failure_code="operation_cancelled",
                recoverable=0,
            )
    update_run_fields(run_id, status="cancelled", failure_code="operation_cancelled")
    return run_view(run_id).model_dump(mode="json")


@router.post("/agents/autonomous/runs/{run_id}/resume")
async def resume_autonomous_run(run_id: str, request: Request) -> dict[str, Any]:
    from app.operation_trace import build_summary, is_run_cancelled, operation_span
    from app.services.autonomous_composer import execute_autonomous_run, run_view
    from app.services.autonomous_composer_store import (
        AutonomousStoreError,
        get_run,
        reconcile_interrupted_stages,
    )

    try:
        _guard_autonomous_run(run_id, "write_score")
        reconcile_interrupted_stages(run_id)
        run = get_run(run_id)
    except AutonomousStoreError as exc:
        _autonomous_http(exc)
        raise
    if run.status not in {"paused", "failed", "running", "pending"}:
        _autonomous_http(AutonomousStoreError("not resumable", code="autonomous_run_not_resumable"))
    async def _cancel_check() -> bool:
        return is_run_cancelled(run.operation_run_id)

    async with operation_span("run", run_id=run.operation_run_id) as span:
        await execute_autonomous_run(run.id, cancel_check=_cancel_check)
    summary = build_summary(span).model_dump(mode="json")
    return run_view(run.id, summary=summary).model_dump(mode="json")


@router.post("/agents/autonomous/runs/{run_id}/checkpoints/{checkpoint_id}/approve")
async def approve_autonomous_checkpoint(
    run_id: str,
    checkpoint_id: str,
) -> dict[str, Any]:
    from app.operation_trace import build_summary, is_run_cancelled, operation_span
    from app.services.autonomous_composer import (
        approve_checkpoint,
        approve_render_stage,
        execute_autonomous_run,
        run_view,
    )
    from app.services.autonomous_composer_store import AutonomousStoreError, get_run

    try:
        _guard_autonomous_run(run_id, "write_score")
        if checkpoint_id == "render":
            approve_render_stage(run_id)
            return run_view(run_id).model_dump(mode="json")
        approve_checkpoint(run_id, checkpoint_id)
        run = get_run(run_id)
    except AutonomousStoreError as exc:
        _autonomous_http(exc)
        raise

    async def _cancel_check() -> bool:
        return is_run_cancelled(run.operation_run_id)

    async with operation_span("run", run_id=run.operation_run_id) as span:
        await execute_autonomous_run(run.id, cancel_check=_cancel_check)
    summary = build_summary(span).model_dump(mode="json")
    return run_view(run.id, summary=summary).model_dump(mode="json")


@router.post("/agents/autonomous/runs/pause")
async def pause_autonomous_run(body: dict[str, Any]) -> dict[str, Any]:
    from app.services.autonomous_composer import request_pause_by_operation, run_view
    from app.services.autonomous_composer_store import AutonomousStoreError

    operation_run_id = str(body.get("operation_run_id") or "")
    if not operation_run_id:
        raise HTTPException(
            status_code=422,
            detail={"code": "autonomous_brief_invalid", "message": "autonomous_brief_invalid"},
        )
    from app.collaboration_settings import collaboration_enabled
    from app.routers.collaboration_guard import enforce_current
    from app.services.autonomous_composer_store import get_run_by_operation

    if collaboration_enabled():
        existing = get_run_by_operation(operation_run_id)
        if existing is not None:
            enforce_current(existing.project_id, "write_score")
    try:
        run = request_pause_by_operation(operation_run_id)
    except AutonomousStoreError as exc:
        _autonomous_http(exc)
        raise
    return run_view(run.id).model_dump(mode="json")


@router.post("/agents/autonomous/runs/{run_id}/checkpoints/arrangement/reject")
async def reject_autonomous_arrangement(run_id: str) -> dict[str, Any]:
    from app.services.autonomous_composer import reject_arrangement, run_view
    from app.services.autonomous_composer_store import AutonomousStoreError

    try:
        _guard_autonomous_run(run_id, "write_score")
        reject_arrangement(run_id)
    except AutonomousStoreError as exc:
        _autonomous_http(exc)
        raise
    return run_view(run_id).model_dump(mode="json")


@router.post("/agents/autonomous/runs/{run_id}/stages/{stage_id}/retry")
async def retry_autonomous_stage(run_id: str, stage_id: str, request: Request) -> dict[str, Any]:
    from app.operation_trace import build_summary, is_run_cancelled, operation_span
    from app.services.autonomous_composer import execute_autonomous_run, retry_stage, run_view
    from app.services.autonomous_composer_store import AutonomousStoreError, get_run

    try:
        _guard_autonomous_run(run_id, "write_score")
        retry_stage(run_id, stage_id)
        run = get_run(run_id)
    except AutonomousStoreError as exc:
        _autonomous_http(exc)
        raise

    async def _cancel_check() -> bool:
        return is_run_cancelled(run.operation_run_id)

    async with operation_span("run", run_id=run.operation_run_id) as span:
        await execute_autonomous_run(run.id, cancel_check=_cancel_check)
    summary = build_summary(span).model_dump(mode="json")
    return run_view(run.id, summary=summary).model_dump(mode="json")


@router.post("/agents/autonomous/runs/{run_id}/stages/{stage_id}/instruction")
async def instruct_autonomous_stage(run_id: str, stage_id: str, body: dict[str, Any]) -> dict[str, Any]:
    from app.autonomous_composer_schemas import AutonomousPlanError
    from app.services.autonomous_composer import run_view, store_stage_instruction
    from app.services.autonomous_composer_store import AutonomousStoreError

    try:
        _guard_autonomous_run(run_id, "write_score")
        store_stage_instruction(run_id, stage_id, str(body.get("text") or ""))
    except (AutonomousPlanError, AutonomousStoreError) as exc:
        _autonomous_http(exc)
        raise
    return run_view(run_id).model_dump(mode="json")


@router.post("/agents/autonomous/runs/{run_id}/stages/{stage_id}/open")
async def open_autonomous_stage(run_id: str, stage_id: str) -> dict[str, Any]:
    from app.services.autonomous_composer import open_stage_revision, run_view
    from app.services.autonomous_composer_store import AutonomousStoreError

    try:
        _guard_autonomous_run(run_id, "write_score")
        open_stage_revision(run_id, stage_id)
    except AutonomousStoreError as exc:
        _autonomous_http(exc)
        raise
    return run_view(run_id).model_dump(mode="json")


@router.post("/agents/autonomous/runs/{run_id}/stages/{stage_id}/branch")
async def branch_autonomous_stage(run_id: str, stage_id: str, body: dict[str, Any]) -> dict[str, str]:
    from pydantic import ValidationError

    from app.services.autonomous_composer import branch_from_stage
    from app.services.autonomous_composer_store import AutonomousStoreError
    from app.services.project_history_store import ProjectHistoryError

    try:
        _guard_autonomous_run(run_id, "write_score")
        return branch_from_stage(run_id, stage_id, str(body.get("name") or ""))
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "autonomous_brief_invalid", "message": "autonomous_brief_invalid"},
        ) from exc
    except (AutonomousStoreError, ProjectHistoryError) as exc:
        _autonomous_http(exc)
        raise


@router.get("/agents/autonomous/runs/{run_id}/artifacts")
async def get_autonomous_artifacts(run_id: str) -> dict[str, Any]:
    from app.services.autonomous_composer import list_run_artifacts
    from app.services.autonomous_composer_store import AutonomousStoreError

    try:
        return {"artifacts": list_run_artifacts(run_id)}
    except AutonomousStoreError as exc:
        _autonomous_http(exc)
        raise


@router.post("/agents/autonomous/runs/{run_id}/stages/{stage_id}/approve")
async def approve_autonomous_stage(run_id: str, stage_id: str) -> dict[str, Any]:
    from app.services.autonomous_composer import approve_render_stage, run_view
    from app.services.autonomous_composer_store import AutonomousStoreError

    if stage_id != "render":
        raise HTTPException(
            status_code=422,
            detail={"code": "autonomous_brief_invalid", "message": "autonomous_brief_invalid"},
        )
    try:
        approve_render_stage(run_id)
    except AutonomousStoreError as exc:
        _autonomous_http(exc)
        raise
    return run_view(run_id).model_dump(mode="json")


@router.post("/agents/autonomous/runs/{run_id}/stages/{stage_id}/skip")
async def skip_autonomous_stage(run_id: str, stage_id: str) -> dict[str, Any]:
    from app.services.autonomous_composer import run_view, skip_render_stage
    from app.services.autonomous_composer_store import AutonomousStoreError

    if stage_id != "render":
        raise HTTPException(
            status_code=422,
            detail={"code": "autonomous_brief_invalid", "message": "autonomous_brief_invalid"},
        )
    try:
        skip_render_stage(run_id)
    except AutonomousStoreError as exc:
        _autonomous_http(exc)
        raise
    return run_view(run_id).model_dump(mode="json")
