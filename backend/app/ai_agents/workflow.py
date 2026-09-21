"""Sequential multi-agent workflow orchestrator (acceptance spine).

Uses immutable ``AgentWorkflowContext`` updates. Prefer sequential orchestration
over LangGraph for v1 so immutability tests stay simple; node order matches the
acceptance spine. Never writes projects / SQLite.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping

from app.ai_agents.errors import WorkflowReviseExhaustedError
from app.ai_agents.progressive_realize import initial_working_draft
from app.ai_agents.registry import ensure_registry, get_agent
from app.ai_agents.schemas import (
    AGENT_SPINE_WORKFLOW_ID,
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
from app.composition_schemas import CompositionV2
from app.services.composition_edit_fingerprint import (
    composition_edit_fingerprint,
    edit_fingerprint_log_prefix,
)
from app.services.generation_provenance import (
    attach_provenance_fragment,
    build_generation_provenance_v1,
)

logger = logging.getLogger(__name__)


@dataclass
class WorkflowPreviewResult:
    """Session-only workflow result — never a durable mutation."""

    workflow_id: str
    pipeline_id: str
    context: AgentWorkflowContext
    candidate: CompositionV2
    candidate_fingerprint: str
    source_fingerprint: str
    artifact_log: tuple[Any, ...]
    stages: list[dict[str, Any]] = field(default_factory=list)
    generation_parameters: dict[str, Any] | None = None
    recommendation: CritiqueRecommendation | None = None
    warning_codes: list[str] = field(default_factory=list)
    duration_ms: int = 0
    mutates_composition: bool = False
    agent_sequence: list[str] = field(default_factory=list)


def build_initial_context(
    source: CompositionV2,
    *,
    workflow_id: str = AGENT_SPINE_WORKFLOW_ID,
) -> AgentWorkflowContext:
    fingerprint = composition_edit_fingerprint(source)
    draft = initial_working_draft(source)
    return AgentWorkflowContext(
        source_composition=source,
        source_fingerprint=fingerprint,
        working_draft_composition=draft,
        workflow_id=workflow_id,
    )


async def _run_agent_node(
    agent_id: str,
    context: AgentWorkflowContext,
    *,
    agent_model_overrides: Mapping[str, str] | None,
    selection: dict[str, Any] | None,
) -> tuple[AgentWorkflowContext, dict[str, Any], list[str]]:
    agent = get_agent(agent_id)
    operation = SPINE_OPERATIONS.get(agent_id, AgentOperation.RUN)
    request = AgentRunRequest(
        agent_id=agent_id,
        operation=operation,
        context=context,
        agent_model_overrides=dict(agent_model_overrides or {}),
        selection=dict(selection or {}),
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
    logger.debug(
        "Workflow slot updates",
        extra={
            "agent_id": agent_id,
            "slots": sorted(result.updated_context_slots.keys()),
            "artifact_count": len(result.artifacts),
            "has_draft_update": result.working_draft_update is not None,
        },
    )
    stage = dict(result.provenance_stage or {})
    if "agent_id" not in stage:
        stage["agent_id"] = agent_id
    return next_ctx, stage, list(result.warning_codes)


async def run_spine_workflow(
    source: CompositionV2,
    *,
    max_revisions: int = 0,
    agent_model_overrides: Mapping[str, str] | None = None,
    selection: dict[str, Any] | None = None,
    workflow_id: str = AGENT_SPINE_WORKFLOW_ID,
    env: Mapping[str, str] | None = None,
) -> WorkflowPreviewResult:
    """Run CreativeDirector → Harmony → Melody → Arrangement → Critic.

    Critic ``approve`` does not persist. ``max_revisions`` default 0 (no loop).
    On revise with remaining budget, re-enter at ``harmony``.
    """
    started = time.perf_counter()
    run_id = str(uuid.uuid4())
    ensure_registry(env)
    context = build_initial_context(source, workflow_id=workflow_id)
    stages: list[dict[str, Any]] = []
    warnings: list[str] = []
    agent_sequence: list[str] = []

    logger.info(
        "Multi-agent workflow start",
        extra={
            "workflow_id": workflow_id,
            "run_id": run_id,
            "max_revisions": max_revisions,
            "source_prefix": edit_fingerprint_log_prefix(context.source_fingerprint),
        },
    )

    # First pass: full spine from creative_director.
    start_index = 0
    while True:
        for agent_id in SPINE_AGENT_IDS[start_index:]:
            context, stage, codes = await _run_agent_node(
                agent_id,
                context,
                agent_model_overrides=agent_model_overrides,
                selection=selection,
            )
            stages.append(stage)
            warnings.extend(codes)
            agent_sequence.append(agent_id)

        recommendation = context.recommendation
        if recommendation != CritiqueRecommendation.REVISE:
            break
        if context.revise_count >= max_revisions:
            logger.warning(
                "Workflow revise exhausted",
                extra={
                    "workflow_id": workflow_id,
                    "revise_count": context.revise_count,
                    "max_revisions": max_revisions,
                },
            )
            raise WorkflowReviseExhaustedError(
                "Critic requested revise but revision budget exhausted",
                workflow_id=workflow_id,
                details={"revise_count": context.revise_count, "max_revisions": max_revisions},
            )
        context = context.with_revise_count(context.revise_count + 1)
        start_index = SPINE_AGENT_IDS.index(SPINE_REVISE_ENTRY_AGENT)
        logger.info(
            "Workflow revise re-enter",
            extra={
                "workflow_id": workflow_id,
                "revise_count": context.revise_count,
                "reenter_agent": SPINE_REVISE_ENTRY_AGENT,
            },
        )

    candidate = context.working_draft_composition
    candidate_fp = composition_edit_fingerprint(candidate)
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
    logger.info(
        "Multi-agent workflow end",
        extra={
            "workflow_id": workflow_id,
            "run_id": run_id,
            "agent_sequence": agent_sequence,
            "artifact_count": len(context.artifact_log),
            "recommendation": context.recommendation.value if context.recommendation else None,
            "duration_ms": duration_ms,
            "candidate_prefix": edit_fingerprint_log_prefix(candidate_fp),
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
    )
