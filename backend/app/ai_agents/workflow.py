"""Sequential multi-agent workflow orchestrator (acceptance spine).

Uses immutable ``AgentWorkflowContext`` updates. Prefer sequential orchestration
over LangGraph for v1 so immutability tests stay simple; node order matches the
acceptance spine. Never writes projects / SQLite.

Revision loops delegate to ``revision_loop.run_revision_loop``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Mapping

from app.ai_agents.progressive_realize import initial_working_draft
from app.ai_agents.schemas import (
    AGENT_SPINE_WORKFLOW_ID,
    AgentWorkflowContext,
    CritiqueRecommendation,
)
from app.composition_schemas import CompositionV2
from app.operation_trace import current_run_id
from app.services.composition_edit_fingerprint import composition_edit_fingerprint

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
    # Controlled revision loop (optional; default-empty preserves legacy callers).
    revision_history: tuple[Any, ...] = ()
    # Session-only playable snapshots per pass (audition/compare); not in pass records.
    pass_candidates: tuple[Any, ...] = ()
    stop_reason: Any | None = None
    last_valid_fingerprint: str | None = None
    usage: Any | None = None
    revision_mode: Any | None = None
    max_passes: int = 0
    budget_code: str | None = None


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


async def run_spine_workflow(
    source: CompositionV2,
    *,
    max_revisions: int = 0,
    revision_mode: str | None = None,
    budgets: Any | None = None,
    cancel_check: Any | None = None,
    agent_model_overrides: Mapping[str, str] | None = None,
    selection: dict[str, Any] | None = None,
    workflow_id: str = AGENT_SPINE_WORKFLOW_ID,
    env: Mapping[str, str] | None = None,
    critic_parameters: dict[str, Any] | None = None,
) -> WorkflowPreviewResult:
    """Run CreativeDirector → Harmony → Melody → Arrangement → Critic.

    Critic ``approve`` does not persist. Default ``max_revisions=0`` / mode off
    (no loop). When revision modes or max_revisions > 0 are set, delegates to
    the bounded ``revision_loop`` controller (targeted revise; last_valid safe).
    """
    from app.ai_agents.revision_loop import run_revision_loop
    from app.ai_agents.revision_loop_schemas import RevisionMode

    mode: RevisionMode | str | None
    if revision_mode is not None:
        mode = revision_mode
    elif max_revisions and max_revisions > 0:
        # Legacy escape hatch: raw max_revisions without a named mode.
        mode = None
    else:
        mode = RevisionMode.OFF

    logger.info(
        "Multi-agent workflow start",
        extra={
            "workflow_id": workflow_id,
            "max_revisions": max_revisions,
            "run_id": current_run_id(),
            "revision_mode": (
                mode.value if isinstance(mode, RevisionMode) else mode
            ),
        },
    )
    return await run_revision_loop(
        source,
        revision_mode=mode,
        max_revisions=max_revisions,
        budgets=budgets,
        cancel_check=cancel_check,
        agent_model_overrides=agent_model_overrides,
        selection=selection,
        workflow_id=workflow_id,
        env=env,
        critic_parameters=critic_parameters,
        raise_on_revise_exhausted=True,
    )
