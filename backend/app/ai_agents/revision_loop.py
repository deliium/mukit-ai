"""Bounded critique → RevisionPlan → targeted agents → validate → re-critique.

Session preview controller only — never writes SQLite / projects.
Critic remains read-only; Apply stays on multi-agent-apply CAS.
"""

from __future__ import annotations

import inspect
import logging
import time
import uuid
from collections.abc import Callable
from typing import Any, Mapping, Awaitable

from app.ai_agents.errors import WorkflowReviseExhaustedError
from app.ai_agents.registry import ensure_registry, get_agent
from app.ai_agents.revision_loop_schemas import (
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


def _budget_exhausted(
    *,
    started: float,
    usage: RevisionLoopUsage,
    budgets: RevisionLoopBudgets | None,
) -> bool:
    if budgets is None:
        return False
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    if budgets.max_wall_ms is not None and elapsed_ms >= budgets.max_wall_ms:
        return True
    if (
        budgets.max_prompt_tokens is not None
        and usage.prompt_tokens is not None
        and usage.prompt_tokens >= budgets.max_prompt_tokens
    ):
        return True
    return False


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
    result = await agent.run(request)
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
    run_id = str(uuid.uuid4())
    settings = load_revision_loop_settings(dict(env) if env is not None else None)
    mode, max_passes = resolve_max_passes(
        revision_mode=revision_mode, max_revisions=max_revisions
    )

    resolved_budgets = budgets
    if resolved_budgets is None and mode != RevisionMode.OFF:
        resolved_budgets = RevisionLoopBudgets(
            max_wall_ms=default_wall_ms_for_mode(mode.value, settings),
            max_prompt_tokens=default_prompt_token_cap_for_mode(mode.value, settings),
        )

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

    def _budget_hit() -> bool:
        return _budget_exhausted(started=started, usage=usage, budgets=resolved_budgets)

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
        context, stage, codes = await _run_agent_node(
            agent_id,
            context,
            agent_model_overrides=agent_model_overrides,
            selection=selection,
            parameters=params,
        )
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
        context, stage, codes = await _run_agent_node(
            "critic",
            context,
            agent_model_overrides=agent_model_overrides,
            selection=selection,
            parameters=critic_params,
        )
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
    logger.info(
        "Revision loop end",
        extra={
            "workflow_id": workflow_id,
            "run_id": run_id,
            "revision_mode": mode.value,
            "pass_index": revision_history[-1].pass_index if revision_history else 0,
            "stop_reason": final_stop.value,
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
    )
