"""Bounded critique → RevisionPlan → targeted agents → validate → re-critique.

Session preview controller only — never writes SQLite / projects.
Critic remains read-only; Apply stays on multi-agent-apply CAS.
"""

from __future__ import annotations

import inspect
import logging
import time
import uuid
from dataclasses import dataclass
from collections.abc import Callable
from typing import Any, Mapping, Awaitable

from app.ai_agents.errors import WorkflowReviseExhaustedError
from app.ai_agents.registry import ensure_registry, get_agent
from app.ai_agents.revision_loop_schemas import (
    REVISION_API_ABSOLUTE_MAX_PASSES,
    RevisionLoopBudgets,
    RevisionLoopUsage,
    RevisionLoopUsageDelta,
    RevisionMode,
    RevisionPassCandidateV1,
    RevisionPassRecordV1,
    RevisionPassValidationResult,
    RevisionStopReason,
    UsageStatus,
    resolve_max_passes,
)
from app.ai_agents.revision_plan_builder import build_revision_plan_from_findings
from app.ai_agents.revision_stop_policy import (
    StopEvaluationInput,
    critique_score_digest_from_payload,
    evaluate_stop_conditions,
    merge_usage_status,
)
from app.ai_agents.schemas import (
    AGENT_SPINE_WORKFLOW_ID,
    AgentArtifactKind,
    AgentArtifactV1,
    AgentOperation,
    AgentRunRequest,
    AgentWorkflowContext,
    CritiqueRecommendation,
)
from app.ai_agents.spine import (
    PIPELINE_ID,
    SPINE_AGENT_IDS,
    SPINE_OPERATIONS,
    SPINE_REVISE_ENTRY_AGENT,
)
from app.ai_agents.workflow import WorkflowPreviewResult, build_initial_context
from app.composition_schemas import CompositionV2
from app.operation_budget_settings import (
    BUDGET_REVISION,
    BUDGET_RUNTIME,
    BUDGET_TOKEN,
    load_operation_budget_settings,
    tighter_wall_ms,
)
from app.operation_trace import (
    OperationBudgetExceeded,
    OperationCancelled,
    current_budget_code,
    current_run_id,
    note_failure,
    note_run_outcome,
    note_run_wall_limit,
    operation_span,
)
from app.revision_loop_settings import (
    cost_class_rank,
    default_prompt_token_cap_for_mode,
    default_wall_ms_for_mode,
    load_revision_loop_settings,
)
from app.services.composition_edit_fingerprint import (
    composition_edit_fingerprint,
    edit_fingerprint_log_prefix,
)
from app.services.composition_revision_preserve import assert_preserve_outside_targets
from app.services.composition_validator import validate_composition_integrity
from app.services.generation_provenance import (
    attach_provenance_fragment,
    build_generation_provenance_v1,
)

logger = logging.getLogger(__name__)

CancelCheck = Callable[[], bool | Awaitable[bool]]


async def _probe_cancel(cancel_check: CancelCheck | None) -> bool:
    if cancel_check is None:
        return False
    try:
        result = cancel_check()
        if inspect.isawaitable(result):
            return bool(await result)
        return bool(result)
    except Exception:  # noqa: BLE001 — cancel probe must not crash loop
        logger.debug("Cancel check raised; treating as not cancelled")
        return False


def _critique_payload(context: AgentWorkflowContext) -> dict[str, Any] | None:
    if context.critique is None:
        return None
    return dict(context.critique.payload or {})


def _findings_from_context(context: AgentWorkflowContext) -> list[Any]:
    payload = _critique_payload(context) or {}
    findings = payload.get("findings")
    return list(findings) if isinstance(findings, list) else []


def _usage_from_stage(stage: dict[str, Any]) -> RevisionLoopUsageDelta:
    prompt = stage.get("prompt_tokens")
    completion = stage.get("completion_tokens")
    latency = stage.get("latency_ms")
    cost = stage.get("estimated_cost_class") or stage.get("cost_class")
    has_tokens = isinstance(prompt, int) or isinstance(completion, int)
    has_latency = isinstance(latency, int)
    if has_tokens or has_latency:
        status = UsageStatus.AVAILABLE
    elif stage.get("runtime") == "fake":
        status = UsageStatus.AVAILABLE
        prompt = 0 if prompt is None else prompt
        completion = 0 if completion is None else completion
        latency = 0 if latency is None else latency
    else:
        status = UsageStatus.UNAVAILABLE
    return RevisionLoopUsageDelta(
        latency_ms=int(latency or 0),
        prompt_tokens=int(prompt) if isinstance(prompt, int) else None,
        completion_tokens=int(completion) if isinstance(completion, int) else None,
        estimated_cost_class=str(cost)[:32] if cost else None,
        usage_status=status,
    )


def _accumulate_usage(
    total: RevisionLoopUsage,
    delta: RevisionLoopUsageDelta,
) -> RevisionLoopUsage:
    prompt = total.prompt_tokens
    completion = total.completion_tokens
    if delta.prompt_tokens is not None:
        prompt = (prompt or 0) + delta.prompt_tokens
    if delta.completion_tokens is not None:
        completion = (completion or 0) + delta.completion_tokens
    peak = total.estimated_cost_class_peak
    if cost_class_rank(delta.estimated_cost_class) > cost_class_rank(peak):
        peak = delta.estimated_cost_class
    status = merge_usage_status(total.usage_status, delta.usage_status)
    # First real observation upgrades unavailable → available/partial.
    if total.usage_status == UsageStatus.UNAVAILABLE and delta.usage_status == UsageStatus.AVAILABLE:
        status = UsageStatus.AVAILABLE
    return RevisionLoopUsage(
        latency_ms_total=total.latency_ms_total + int(delta.latency_ms or 0),
        prompt_tokens=prompt,
        completion_tokens=completion,
        estimated_cost_class_peak=peak,
        usage_status=status,
    )


class _AgentStop(Exception):
    """Control-flow stop from an agent span. Not a process-ending error."""

    def __init__(
        self,
        reason: RevisionStopReason,
        *,
        budget_code: str | None = None,
        failure_code: str | None = None,
    ) -> None:
        super().__init__(reason.value)
        self.reason = reason
        self.budget_code = budget_code
        self.failure_code = failure_code


def _budget_exhausted(
    *,
    started: float,
    usage: RevisionLoopUsage,
    budgets: RevisionLoopBudgets | None,
) -> str | None:
    """Return an ``operation_*`` code, ``revision_loop`` when that budget binds, or None."""
    op = load_operation_budget_settings()
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    request_wall = budgets.max_wall_ms if budgets is not None else None
    wall_limit = op.max_runtime_ms if request_wall is None else min(request_wall, op.max_runtime_ms)
    if elapsed_ms >= wall_limit:
        if request_wall is None or op.max_runtime_ms <= request_wall:
            return BUDGET_RUNTIME
        return "revision_loop"
    request_tokens = budgets.max_prompt_tokens if budgets is not None else None
    reported = usage.prompt_tokens
    token_limits: list[tuple[str, int]] = []
    if request_tokens is not None and reported is not None:
        token_limits.append(("revision_loop", request_tokens))
    if (
        op.max_prompt_tokens is not None
        and usage.usage_status != UsageStatus.UNAVAILABLE
        and reported is not None
    ):
        token_limits.append((BUDGET_TOKEN, op.max_prompt_tokens))
    if token_limits and reported is not None:
        code, limit = min(token_limits, key=lambda item: item[1])
        if reported >= limit:
            return code
    traced = current_budget_code()
    if traced:
        return traced
    return None


def _snapshot_candidate(composition: CompositionV2) -> CompositionV2:
    return composition.model_copy(deep=True)


def _pass_record(
    *,
    pass_index: int,
    context: AgentWorkflowContext,
    candidate_fp: str,
    revision_plan: dict[str, Any] | None,
    composition_patch: dict[str, Any] | None,
    validation: RevisionPassValidationResult,
    usage_delta: RevisionLoopUsageDelta,
    score_digest: Any,
    target_agent_ids: list[str],
    stop_eligible: list[str],
) -> RevisionPassRecordV1:
    critique_art = context.critique
    payload = _critique_payload(context) or {}
    rec = payload.get("recommendation")
    digest = None
    if critique_art is not None:
        digest = f"{critique_art.artifact_id[:12]}:{str(rec or '')}"[:128]
    return RevisionPassRecordV1(
        pass_index=pass_index,
        critique_artifact_id=critique_art.artifact_id if critique_art else None,
        critique_recommendation=str(rec) if rec else (
            context.recommendation.value if context.recommendation else None
        ),
        critique_digest=digest,
        revision_plan=revision_plan,
        composition_patch=composition_patch,
        validation_result=validation,
        candidate_fingerprint=candidate_fp,
        score_digest=score_digest,
        usage_delta=usage_delta,
        stop_eligible_reasons=stop_eligible,
        target_agent_ids=target_agent_ids,
    )


async def _run_agent_node(
    agent_id: str,
    context: AgentWorkflowContext,
    *,
    agent_model_overrides: Mapping[str, str] | None,
    selection: dict[str, Any] | None,
    parameters: dict[str, Any] | None = None,
) -> tuple[AgentWorkflowContext, dict[str, Any], list[str]]:
    agent = get_agent(agent_id)
    operation = SPINE_OPERATIONS.get(agent_id, AgentOperation.RUN)
    request = AgentRunRequest(
        agent_id=agent_id,
        operation=operation,
        context=context,
        agent_model_overrides=dict(agent_model_overrides or {}),
        selection=dict(selection or {}),
        parameters=dict(parameters or {}),
    )
    logger.debug(
        "Agent span start",
        extra={
            "agent_id": agent_id,
            "span_id_prefix": (current_run_id() or "")[:12],
            "run_id": current_run_id(),
        },
    )
    stop: _AgentStop | None = None
    async with operation_span("agent", agent_id=agent_id) as agent_span:
        try:
            result = await agent.run(request)
        except KeyboardInterrupt:
            raise
        except SystemExit:
            note_failure("operation_model_crashed", error_type="SystemExit")
            logger.error(
                "Agent run crashed",
                extra={
                    "failure_code": "operation_model_crashed",
                    "error_type": "SystemExit",
                    "run_id": current_run_id(),
                    "model_id": None,
                },
            )
            stop = _AgentStop(
                RevisionStopReason.VALIDATION_FAILED_KEPT_LAST_VALID,
                failure_code="operation_model_crashed",
            )
            result = None
        except OperationCancelled:
            agent_span.status = "cancelled"
            agent_span.failure_code = "operation_cancelled"
            stop = _AgentStop(RevisionStopReason.CANCELLED)
            result = None
        except OperationBudgetExceeded as exc:
            agent_span.status = "budget_exceeded"
            agent_span.failure_code = exc.budget_code
            note_run_outcome(budget_code=exc.budget_code)
            stop = _AgentStop(
                RevisionStopReason.RESOURCE_BUDGET_EXHAUSTED,
                budget_code=exc.budget_code,
            )
            result = None
    if stop is not None or result is None:
        raise stop or _AgentStop(RevisionStopReason.VALIDATION_FAILED_KEPT_LAST_VALID)
    next_ctx = context
    for slot_name, artifact in result.updated_context_slots.items():
        next_ctx = next_ctx.with_slot(slot_name, artifact)
    if result.artifacts:
        next_ctx = next_ctx.with_artifact_log(*result.artifacts)
    if result.working_draft_update is not None:
        next_ctx = next_ctx.with_working_draft(result.working_draft_update)
    if result.recommendation is not None:
        next_ctx = next_ctx.with_recommendation(result.recommendation)
    stage = dict(result.provenance_stage or {})
    if "agent_id" not in stage:
        stage["agent_id"] = agent_id
    return next_ctx, stage, list(result.warning_codes)


async def run_revision_loop(
    source: CompositionV2,
    *,
    revision_mode: RevisionMode | str | None = RevisionMode.OFF,
    max_revisions: int | None = None,
    budgets: RevisionLoopBudgets | None = None,
    cancel_check: CancelCheck | None = None,
    agent_model_overrides: Mapping[str, str] | None = None,
    selection: dict[str, Any] | None = None,
    workflow_id: str = AGENT_SPINE_WORKFLOW_ID,
    env: Mapping[str, str] | None = None,
    critic_parameters: dict[str, Any] | None = None,
    raise_on_revise_exhausted: bool = True,
) -> WorkflowPreviewResult:
    """Run spine + optional bounded critique revision loop.

    Default ``revision_mode=off`` / ``max_passes=0`` preserves legacy spine behavior.
    """
    started = time.perf_counter()
    traced_run = current_run_id()
    run_id = traced_run or str(uuid.uuid4())
    settings = load_revision_loop_settings(dict(env) if env is not None else None)
    op_settings = load_operation_budget_settings(dict(env) if env is not None else None)
    mode, max_passes = resolve_max_passes(
        revision_mode=revision_mode,
        max_revisions=max_revisions,
        revision_ceiling=op_settings.max_revisions,
    )
    _mode_uncapped, passes_without_ceiling = resolve_max_passes(
        revision_mode=revision_mode,
        max_revisions=max_revisions,
        revision_ceiling=REVISION_API_ABSOLUTE_MAX_PASSES,
    )
    ceiling_binding = max_passes < passes_without_ceiling
    budget_code: str | None = None

    resolved_budgets = budgets
    if resolved_budgets is None and mode != RevisionMode.OFF:
        resolved_budgets = RevisionLoopBudgets(
            max_wall_ms=default_wall_ms_for_mode(mode.value, settings),
            max_prompt_tokens=default_prompt_token_cap_for_mode(mode.value, settings),
        )
    request_wall = resolved_budgets.max_wall_ms if resolved_budgets is not None else None
    note_run_wall_limit(tighter_wall_ms(request_wall, op_settings))

    ensure_registry(env)
    context = build_initial_context(source, workflow_id=workflow_id)
    stages: list[dict[str, Any]] = []
    warnings: list[str] = []
    agent_sequence: list[str] = []
    revision_history: list[RevisionPassRecordV1] = []
    pass_candidates: list[RevisionPassCandidateV1] = []
    usage = RevisionLoopUsage(usage_status=UsageStatus.UNAVAILABLE)
    stop_reason: RevisionStopReason | None = None
    last_valid = _snapshot_candidate(context.working_draft_composition)
    last_valid_fp = composition_edit_fingerprint(last_valid)

    def _append_pass_candidate(pass_index: int, fingerprint: str, composition: CompositionV2) -> None:
        pass_candidates.append(
            RevisionPassCandidateV1(
                pass_index=pass_index,
                candidate_fingerprint=fingerprint,
                composition=_snapshot_candidate(composition),
            )
        )
    prior_score = None
    critic_params = dict(critic_parameters or {})
    if settings.revise_on_technical and "revise_on_technical" not in critic_params:
        critic_params["revise_on_technical"] = True

    logger.info(
        "Revision loop start",
        extra={
            "workflow_id": workflow_id,
            "run_id": run_id,
            "revision_mode": mode.value,
            "max_passes": max_passes,
            "source_prefix": edit_fingerprint_log_prefix(context.source_fingerprint),
        },
    )

    def _budget_hit() -> str | None:
        code = _budget_exhausted(started=started, usage=usage, budgets=resolved_budgets)
        nonlocal budget_code
        if code and code.startswith("operation_"):
            budget_code = code
            note_run_outcome(budget_code=code, status="budget_exceeded")
        return code

    def _apply_agent_stop(exc: _AgentStop) -> None:
        nonlocal budget_code, stop_reason
        stop_reason = exc.reason
        if exc.budget_code and exc.budget_code.startswith("operation_"):
            budget_code = exc.budget_code
            note_run_outcome(budget_code=exc.budget_code, status="budget_exceeded")

    # --- Pass 0: full spine -------------------------------------------------
    for agent_id in SPINE_AGENT_IDS:
        if await _probe_cancel(cancel_check):
            stop_reason = RevisionStopReason.CANCELLED
            context = context.with_working_draft(last_valid)
            break
        if _budget_hit():
            stop_reason = RevisionStopReason.RESOURCE_BUDGET_EXHAUSTED
            context = context.with_working_draft(last_valid)
            break
        params = critic_params if agent_id == "critic" else None
        try:
            context, stage, codes = await _run_agent_node(
                agent_id,
                context,
                agent_model_overrides=agent_model_overrides,
                selection=selection,
                parameters=params,
            )
        except _AgentStop as exc:
            _apply_agent_stop(exc)
            context = context.with_working_draft(last_valid)
            logger.info(
                "Revision spine stopped",
                extra={
                    "agent_id": agent_id,
                    "stop_reason": exc.reason.value,
                    "run_id": run_id,
                    "budget_code": exc.budget_code,
                },
            )
            break
        except Exception as exc:  # noqa: BLE001 — last-valid; span already recorded the failure
            logger.warning(
                "Revision spine agent failed",
                extra={
                    "agent_id": agent_id,
                    "error_type": type(exc).__name__,
                    "run_id": run_id,
                    "failure_code": "agent_run_failed",
                },
            )
            stop_reason = RevisionStopReason.VALIDATION_FAILED_KEPT_LAST_VALID
            context = context.with_working_draft(last_valid)
            break
        stages.append(stage)
        warnings.extend(codes)
        agent_sequence.append(agent_id)
        delta = _usage_from_stage(stage)
        usage = _accumulate_usage(usage, delta)

    if stop_reason is None:
        # Validate initial candidate and snapshot last_valid.
        try:
            report = validate_composition_integrity(
                context.working_draft_composition, profile="canonical"
            )
            if not report.ok:
                raise ValueError("integrity_failed")
            last_valid = _snapshot_candidate(context.working_draft_composition)
            last_valid_fp = composition_edit_fingerprint(last_valid)
            validation = RevisionPassValidationResult(status="ok", preserve_ok=True)
        except Exception:  # noqa: BLE001
            context = context.with_working_draft(last_valid)
            validation = RevisionPassValidationResult(
                status="failed",
                error_codes=["working_draft_invalid"],
                message="initial_validate_failed",
            )
            stop_reason = RevisionStopReason.VALIDATION_FAILED_KEPT_LAST_VALID

        score = critique_score_digest_from_payload(
            _critique_payload(context), settings=settings
        )
        prior_score = score
        pass0 = _pass_record(
            pass_index=0,
            context=context,
            candidate_fp=last_valid_fp,
            revision_plan=None,
            composition_patch=None,
            validation=validation,
            usage_delta=RevisionLoopUsageDelta(usage_status=usage.usage_status),
            score_digest=score,
            target_agent_ids=[],
            stop_eligible=[r.value for r in RevisionStopReason],
        )
        revision_history.append(pass0)
        _append_pass_candidate(0, last_valid_fp, last_valid)

        if stop_reason is None:
            stop_reason = evaluate_stop_conditions(
                StopEvaluationInput(
                    recommendation=context.recommendation,
                    score_digest=score,
                    prior_score_digest=None,
                    pass_index=0,
                    max_passes=max_passes,
                    revise_count=context.revise_count,
                    cancelled=await _probe_cancel(cancel_check),
                    budget_exhausted=_budget_hit(),
                    validation_failed=validation.status == "failed",
                    completed_revise_pass=False,
                ),
                settings=settings,
            )

    # --- Passes 1..max_passes: targeted revise ------------------------------
    while (
        stop_reason is None
        and context.recommendation == CritiqueRecommendation.REVISE
        and context.revise_count < max_passes
    ):
        if await _probe_cancel(cancel_check):
            stop_reason = RevisionStopReason.CANCELLED
            context = context.with_working_draft(last_valid)
            logger.info(
                "Revision loop cancelled",
                extra={"workflow_id": workflow_id, "pass_index": context.revise_count},
            )
            break
        if _budget_hit():
            stop_reason = RevisionStopReason.RESOURCE_BUDGET_EXHAUSTED
            context = context.with_working_draft(last_valid)
            break

        next_pass = context.revise_count + 1
        context = context.with_revise_count(next_pass)
        pass_index = next_pass
        plan_model = build_revision_plan_from_findings(
            _findings_from_context(context),
            pass_index=pass_index,
            recommendation=CritiqueRecommendation.REVISE,
            revise_on_technical=bool(critic_params.get("revise_on_technical"))
            or settings.revise_on_technical,
            settings=settings,
        )
        plan_payload = plan_model.model_dump(mode="json")
        target_agents = [
            a
            for a in plan_model.target_agent_ids
            if a in {"harmony", "melody_motif", "arrangement"}
        ]
        if not target_agents:
            target_agents = [SPINE_REVISE_ENTRY_AGENT, "melody_motif"]

        plan_art = AgentArtifactV1(
            kind=AgentArtifactKind.PLAN,
            producer_agent_id="revision_loop",
            content_type="agent.revision_plan.v1",
            payload=plan_payload,
            source_fingerprint=context.source_fingerprint,
        )
        context = context.with_artifact_log(plan_art)

        logger.info(
            "Revision pass start",
            extra={
                "workflow_id": workflow_id,
                "pass_index": pass_index,
                "target_agent_ids": target_agents,
                "revision_mode": mode.value,
            },
        )
        logger.debug(
            "Revision pass sequence",
            extra={"pass_index": pass_index, "agents": target_agents + ["critic"]},
        )

        before_draft = _snapshot_candidate(context.working_draft_composition)
        patch_payload: dict[str, Any] | None = None
        validation = RevisionPassValidationResult(status="skipped")
        pass_failed = False
        pass_usage = RevisionLoopUsageDelta(usage_status=UsageStatus.UNAVAILABLE)

        for agent_id in target_agents:
            if await _probe_cancel(cancel_check):
                stop_reason = RevisionStopReason.CANCELLED
                context = context.with_working_draft(last_valid)
                logger.info(
                    "Revision loop cancelled",
                    extra={"workflow_id": workflow_id, "pass_index": pass_index},
                )
                pass_failed = True
                break
            if _budget_hit():
                stop_reason = RevisionStopReason.RESOURCE_BUDGET_EXHAUSTED
                context = context.with_working_draft(last_valid)
                pass_failed = True
                break
            try:
                context, stage, codes = await _run_agent_node(
                    agent_id,
                    context,
                    agent_model_overrides=agent_model_overrides,
                    selection=selection,
                )
            except _AgentStop as exc:
                _apply_agent_stop(exc)
                context = context.with_working_draft(last_valid)
                pass_failed = True
                logger.info(
                    "Revision pass stopped",
                    extra={
                        "agent_id": agent_id,
                        "pass_index": pass_index,
                        "stop_reason": exc.reason.value,
                        "run_id": run_id,
                        "budget_code": exc.budget_code,
                    },
                )
                break
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Revision agent failed",
                    extra={
                        "agent_id": agent_id,
                        "pass_index": pass_index,
                        "error_type": type(exc).__name__,
                    },
                )
                context = context.with_working_draft(last_valid)
                validation = RevisionPassValidationResult(
                    status="failed",
                    error_codes=["agent_run_failed"],
                    message=type(exc).__name__[:200],
                )
                pass_failed = True
                break
            stages.append(stage)
            warnings.extend(codes)
            agent_sequence.append(agent_id)
            delta = _usage_from_stage(stage)
            usage = _accumulate_usage(usage, delta)
            pass_usage = delta
            # Capture last composition_patch artifact if present.
            for art in context.artifact_log[-3:]:
                if art.content_type == "agent.composition_patch.v1":
                    patch_payload = dict(art.payload or {})

        if stop_reason in {
            RevisionStopReason.CANCELLED,
            RevisionStopReason.RESOURCE_BUDGET_EXHAUSTED,
        }:
            break

        if not pass_failed:
            after_draft = context.working_draft_composition
            preserve_ok = assert_preserve_outside_targets(
                before_draft,
                after_draft,
                affected_ranges=plan_model.affected_ranges,
                affected_tracks=plan_model.affected_tracks,
                preserve_outside_targets=plan_model.preserve_outside_targets,
            )
            try:
                report = validate_composition_integrity(after_draft, profile="canonical")
                if not report.ok:
                    raise ValueError("integrity_failed")
                if not preserve_ok:
                    raise ValueError("preserve_violation")
                last_valid = _snapshot_candidate(after_draft)
                last_valid_fp = composition_edit_fingerprint(last_valid)
                validation = RevisionPassValidationResult(
                    status="ok", preserve_ok=preserve_ok
                )
                logger.info(
                    "Revision realize ok",
                    extra={
                        "pass_index": pass_index,
                        "preserve_ok": preserve_ok,
                        "candidate_prefix": edit_fingerprint_log_prefix(last_valid_fp),
                    },
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Revision validation failed; restoring last_valid",
                    extra={
                        "pass_index": pass_index,
                        "error_type": type(exc).__name__,
                        "restored_prefix": edit_fingerprint_log_prefix(last_valid_fp),
                    },
                )
                context = context.with_working_draft(last_valid)
                validation = RevisionPassValidationResult(
                    status="failed",
                    preserve_ok=False if "preserve" in str(exc).lower() else None,
                    error_codes=["validation_failed"],
                    message=type(exc).__name__[:200],
                )
                pass_failed = True

        if pass_failed:
            if stop_reason is None:
                stop_reason = RevisionStopReason.VALIDATION_FAILED_KEPT_LAST_VALID
            score = prior_score or critique_score_digest_from_payload(
                _critique_payload(context), settings=settings
            )
            revision_history.append(
                _pass_record(
                    pass_index=pass_index,
                    context=context,
                    candidate_fp=last_valid_fp,
                    revision_plan=plan_payload,
                    composition_patch=patch_payload,
                    validation=validation,
                    usage_delta=pass_usage,
                    score_digest=score,
                    target_agent_ids=target_agents,
                    stop_eligible=[RevisionStopReason.VALIDATION_FAILED_KEPT_LAST_VALID.value],
                )
            )
            _append_pass_candidate(pass_index, last_valid_fp, last_valid)
            break

        # Re-critique
        if await _probe_cancel(cancel_check):
            stop_reason = RevisionStopReason.CANCELLED
            context = context.with_working_draft(last_valid)
            break
        if _budget_hit():
            logger.warning(
                "[FIX] Re-critique skipped after operation budget",
                extra={
                    "budget_code": budget_code,
                    "run_id": run_id,
                    "pass_index": pass_index,
                },
            )
            stop_reason = RevisionStopReason.RESOURCE_BUDGET_EXHAUSTED
            context = context.with_working_draft(last_valid)
            score = prior_score or critique_score_digest_from_payload(
                _critique_payload(context), settings=settings
            )
            revision_history.append(
                _pass_record(
                    pass_index=pass_index,
                    context=context,
                    candidate_fp=last_valid_fp,
                    revision_plan=plan_payload,
                    composition_patch=patch_payload,
                    validation=validation,
                    usage_delta=pass_usage,
                    score_digest=score,
                    target_agent_ids=target_agents,
                    stop_eligible=[stop_reason.value],
                )
            )
            _append_pass_candidate(pass_index, last_valid_fp, last_valid)
            break
        try:
            context, stage, codes = await _run_agent_node(
                "critic",
                context,
                agent_model_overrides=agent_model_overrides,
                selection=selection,
                parameters=critic_params,
            )
        except _AgentStop as exc:
            _apply_agent_stop(exc)
            context = context.with_working_draft(last_valid)
            break
        stages.append(stage)
        warnings.extend(codes)
        agent_sequence.append("critic")
        delta = _usage_from_stage(stage)
        usage = _accumulate_usage(usage, delta)

        score = critique_score_digest_from_payload(
            _critique_payload(context), settings=settings
        )
        revision_history.append(
            _pass_record(
                pass_index=pass_index,
                context=context,
                candidate_fp=last_valid_fp,
                revision_plan=plan_payload,
                composition_patch=patch_payload,
                validation=validation,
                usage_delta=delta,
                score_digest=score,
                target_agent_ids=target_agents,
                stop_eligible=[r.value for r in RevisionStopReason],
            )
        )
        _append_pass_candidate(pass_index, last_valid_fp, last_valid)

        stop_reason = evaluate_stop_conditions(
            StopEvaluationInput(
                recommendation=context.recommendation,
                score_digest=score,
                prior_score_digest=prior_score,
                pass_index=pass_index,
                max_passes=max_passes,
                revise_count=context.revise_count,
                cancelled=await _probe_cancel(cancel_check),
                budget_exhausted=_budget_hit(),
                validation_failed=False,
                completed_revise_pass=True,
            ),
            settings=settings,
        )
        prior_score = score

    # If still revise after loop without stop reason → exhausted
    if (
        stop_reason is None
        and context.recommendation == CritiqueRecommendation.REVISE
    ):
        stop_reason = (
            RevisionStopReason.MAX_PASSES_REACHED
            if context.revise_count >= max_passes and max_passes > 0
            else RevisionStopReason.REVISE_EXHAUSTED
        )
        if raise_on_revise_exhausted and max_passes == 0 and mode == RevisionMode.OFF:
            # Preserve legacy spine behavior for max_revisions=0 + revise.
            logger.warning(
                "Workflow revise exhausted",
                extra={
                    "workflow_id": workflow_id,
                    "revise_count": context.revise_count,
                    "max_passes": max_passes,
                },
            )
            raise WorkflowReviseExhaustedError(
                "Critic requested revise but revision budget exhausted",
                workflow_id=workflow_id,
                details={
                    "revise_count": context.revise_count,
                    "max_revisions": max_passes,
                },
            )

    # Ensure final candidate is last_valid
    context = context.with_working_draft(last_valid)
    candidate = last_valid
    candidate_fp = last_valid_fp
    fragment = build_generation_provenance_v1(
        pipeline_id=PIPELINE_ID,
        stages=stages,
    )
    enriched = attach_provenance_fragment(
        {
            "pipeline_id": PIPELINE_ID,
            "stages": fragment["stages"],
            "seed": None,
        }
    )
    duration_ms = int((time.perf_counter() - started) * 1000)
    final_stop = stop_reason or RevisionStopReason.CRITIC_APPROVE
    if ceiling_binding and final_stop == RevisionStopReason.MAX_PASSES_REACHED:
        final_stop = RevisionStopReason.RESOURCE_BUDGET_EXHAUSTED
        budget_code = BUDGET_REVISION
        logger.info(
            "Revision ceiling stopped the loop",
            extra={
                "run_id": run_id,
                "budget_code": budget_code,
                "max_passes": max_passes,
                "stop_reason": final_stop.value,
            },
        )
    outcome_status = None
    if final_stop == RevisionStopReason.CANCELLED:
        outcome_status = "cancelled"
    elif budget_code:
        outcome_status = "budget_exceeded"
    note_run_outcome(
        revision_count=context.revise_count,
        stop_reason=final_stop.value,
        budget_code=budget_code,
        status=outcome_status,
    )
    logger.info(
        "Revision loop end",
        extra={
            "workflow_id": workflow_id,
            "run_id": run_id,
            "revision_mode": mode.value,
            "pass_index": revision_history[-1].pass_index if revision_history else 0,
            "stop_reason": final_stop.value,
            "budget_code": budget_code,
            "max_passes": max_passes,
            "revise_count": context.revise_count,
            "duration_ms": duration_ms,
            "usage_status": usage.usage_status.value,
            "candidate_prefix": edit_fingerprint_log_prefix(candidate_fp),
            "history_len": len(revision_history),
            "pass_candidate_count": len(pass_candidates),
        },
    )
    logger.info(
        "Revision loop usage",
        extra={
            "usage_status": usage.usage_status.value,
            "latency_ms_total": usage.latency_ms_total,
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
        },
    )
    return WorkflowPreviewResult(
        workflow_id=workflow_id,
        pipeline_id=PIPELINE_ID,
        context=context,
        candidate=candidate,
        candidate_fingerprint=candidate_fp,
        source_fingerprint=context.source_fingerprint,
        artifact_log=context.artifact_log,
        stages=list(enriched.get("stages") or fragment["stages"]),
        generation_parameters=enriched.get("generation_parameters"),
        recommendation=context.recommendation,
        warning_codes=warnings,
        duration_ms=duration_ms,
        mutates_composition=False,
        agent_sequence=agent_sequence,
        revision_history=tuple(revision_history),
        pass_candidates=tuple(pass_candidates),
        stop_reason=final_stop,
        last_valid_fingerprint=last_valid_fp,
        usage=usage,
        revision_mode=mode,
        max_passes=max_passes,
        budget_code=budget_code,
    )


@dataclass(frozen=True)
class TargetedRevisionResult:
    """Last valid draft after targeted passes. The caller commits."""

    composition: CompositionV2
    changed: bool
    stop_reason: RevisionStopReason
    failure_code: str | None
    budget_code: str | None
    pass_count: int


async def run_targeted_revision_passes(
    composition: CompositionV2,
    critique: AgentArtifactV1,
    *,
    max_revisions: int | None = 1,
    revision_mode: RevisionMode | str | None = RevisionMode.BALANCED,
    agent_model_overrides: Mapping[str, str] | None = None,
    selection: dict[str, Any] | None = None,
    critic_parameters: dict[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
) -> TargetedRevisionResult:
    """Revise only agents named by an existing critique. Does not run the spine."""
    settings = load_revision_loop_settings(dict(env) if env is not None else None)
    op_settings = load_operation_budget_settings(dict(env) if env is not None else None)
    mode, max_passes = resolve_max_passes(
        revision_mode=revision_mode,
        max_revisions=max_revisions,
        revision_ceiling=op_settings.max_revisions,
    )
    recommendation = critique.payload.get("recommendation")
    findings = critique.payload.get("findings")
    findings_list = list(findings) if isinstance(findings, list) else []
    logger.info(
        "Targeted revision start",
        extra={
            "findings_count": len(findings_list),
            "max_passes": max_passes,
            "revision_mode": mode.value,
        },
    )
    if recommendation != CritiqueRecommendation.REVISE.value and recommendation != CritiqueRecommendation.REVISE:
        logger.info(
            "Targeted revision skipped",
            extra={
                "pass_index": 0,
                "target_agent_ids": [],
                "stop_reason": RevisionStopReason.CRITIC_APPROVE.value,
                "budget_code": None,
            },
        )
        return TargetedRevisionResult(
            composition=composition,
            changed=False,
            stop_reason=RevisionStopReason.CRITIC_APPROVE,
            failure_code=None,
            budget_code=None,
            pass_count=0,
        )

    ensure_registry(env)
    context = build_initial_context(composition)
    context = context.with_slot("critique", critique).with_recommendation(
        CritiqueRecommendation.REVISE
    )
    started = time.perf_counter()
    usage = RevisionLoopUsage(usage_status=UsageStatus.UNAVAILABLE)
    last_valid = _snapshot_candidate(context.working_draft_composition)
    last_fp = composition_edit_fingerprint(last_valid)
    origin_fp = last_fp
    stop_reason: RevisionStopReason | None = None
    budget_code: str | None = None
    failure_code: str | None = None
    pass_count = 0
    critic_params = dict(critic_parameters or {})
    resolved_budgets = RevisionLoopBudgets(
        max_wall_ms=default_wall_ms_for_mode(mode.value, settings),
        max_prompt_tokens=default_prompt_token_cap_for_mode(mode.value, settings),
    )

    def _budget_hit() -> str | None:
        nonlocal budget_code
        code = _budget_exhausted(started=started, usage=usage, budgets=resolved_budgets)
        if code and code.startswith("operation_"):
            budget_code = code
        return code

    while stop_reason is None and context.revise_count < max_passes:
        pass_index = context.revise_count + 1
        context = context.with_revise_count(pass_index)
        plan_model = build_revision_plan_from_findings(
            findings_list,
            pass_index=pass_index,
            recommendation=CritiqueRecommendation.REVISE,
            revise_on_technical=bool(critic_params.get("revise_on_technical"))
            or settings.revise_on_technical,
            settings=settings,
        )
        target_agents = [
            agent_id
            for agent_id in plan_model.target_agent_ids
            if agent_id in {"harmony", "melody_motif", "arrangement"}
        ]
        logger.info(
            "Targeted revision pass",
            extra={
                "pass_index": pass_index,
                "target_agent_ids": target_agents,
                "stop_reason": None,
                "budget_code": budget_code,
                "findings_count": len(findings_list),
            },
        )
        before_draft = _snapshot_candidate(context.working_draft_composition)
        pass_failed = False
        for agent_id in target_agents:
            if _budget_hit():
                stop_reason = RevisionStopReason.RESOURCE_BUDGET_EXHAUSTED
                context = context.with_working_draft(last_valid)
                pass_failed = True
                break
            try:
                context, stage, _codes = await _run_agent_node(
                    agent_id,
                    context,
                    agent_model_overrides=agent_model_overrides,
                    selection=selection,
                )
            except _AgentStop as exc:
                stop_reason = exc.reason
                budget_code = exc.budget_code or budget_code
                failure_code = exc.failure_code
                context = context.with_working_draft(last_valid)
                pass_failed = True
                break
            delta = _usage_from_stage(stage)
            usage = _accumulate_usage(usage, delta)
        if failure_code == "operation_model_crashed":
            logger.info(
                "Targeted revision pass",
                extra={
                    "pass_index": pass_index,
                    "target_agent_ids": target_agents,
                    "stop_reason": stop_reason.value if stop_reason else None,
                    "budget_code": budget_code,
                },
            )
            break
        if not pass_failed:
            after_draft = context.working_draft_composition
            preserve_ok = assert_preserve_outside_targets(
                before_draft,
                after_draft,
                affected_ranges=plan_model.affected_ranges,
                affected_tracks=plan_model.affected_tracks,
                preserve_outside_targets=plan_model.preserve_outside_targets,
            )
            try:
                report = validate_composition_integrity(after_draft, profile="canonical")
                if not report.ok or not preserve_ok:
                    raise ValueError("preserve_violation" if not preserve_ok else "integrity_failed")
                last_valid = _snapshot_candidate(after_draft)
                last_fp = composition_edit_fingerprint(last_valid)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Targeted revision preserve failed",
                    extra={
                        "pass_index": pass_index,
                        "fingerprint_prefix": edit_fingerprint_log_prefix(last_fp),
                        "error_type": type(exc).__name__,
                    },
                )
                context = context.with_working_draft(last_valid)
                stop_reason = RevisionStopReason.VALIDATION_FAILED_KEPT_LAST_VALID
                pass_failed = True
        if pass_failed:
            if stop_reason is None:
                stop_reason = RevisionStopReason.VALIDATION_FAILED_KEPT_LAST_VALID
            logger.info(
                "Targeted revision pass",
                extra={
                    "pass_index": pass_index,
                    "target_agent_ids": target_agents,
                    "stop_reason": stop_reason.value,
                    "budget_code": budget_code,
                },
            )
            break
        pass_count = pass_index
        try:
            context, stage, _codes = await _run_agent_node(
                "critic",
                context,
                agent_model_overrides=agent_model_overrides,
                selection=selection,
                parameters=critic_params,
            )
        except _AgentStop as exc:
            stop_reason = exc.reason
            failure_code = exc.failure_code
            budget_code = exc.budget_code or budget_code
            context = context.with_working_draft(last_valid)
            break
        delta = _usage_from_stage(stage)
        usage = _accumulate_usage(usage, delta)
        if context.critique is not None:
            raw_findings = (context.critique.payload or {}).get("findings")
            if isinstance(raw_findings, list):
                findings_list = raw_findings
        logger.info(
            "Targeted revision pass",
            extra={
                "pass_index": pass_index,
                "target_agent_ids": target_agents,
                "stop_reason": (
                    context.recommendation.value if context.recommendation else None
                ),
                "budget_code": budget_code,
            },
        )
        if context.recommendation != CritiqueRecommendation.REVISE:
            stop_reason = RevisionStopReason.CRITIC_APPROVE
            break
    if stop_reason is None:
        stop_reason = RevisionStopReason.MAX_PASSES_REACHED
    logger.info(
        "Targeted revision finished",
        extra={
            "pass_index": pass_count,
            "target_agent_ids": [],
            "stop_reason": stop_reason.value,
            "budget_code": budget_code,
            "findings_count": len(findings_list),
        },
    )
    return TargetedRevisionResult(
        composition=last_valid,
        changed=last_fp != origin_fp and failure_code is None,
        stop_reason=stop_reason,
        failure_code=failure_code,
        budget_code=budget_code,
        pass_count=pass_count,
    )
