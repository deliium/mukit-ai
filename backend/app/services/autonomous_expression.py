"""Arrangement and expression stages. A failed check does not commit."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from app.ai_agents.registry import ensure_registry, get_agent
from app.ai_agents.revision_loop import _run_agent_node
from app.ai_agents.schemas import AgentOperation, AgentRunRequest
from app.ai_agents.workflow import build_initial_context
from app.autonomous_composer_schemas import ProjectPlanV1
from app.composition_schemas import CompositionV2
from app.services.autonomous_composer_store import update_stage_status
from app.services.autonomous_constraints import (
    AutonomousConstraintError,
    assert_stage_completion,
)
from app.services.composition_expression import pitch_timing_fingerprint
from app.services.autonomous_symbolic import commit_autonomous_stage
from app.services.composition_edit_fingerprint import edit_fingerprint_log_prefix

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StageOutcome:
    status: str
    revision_id: str | None
    failure_code: str | None


def _melody_fingerprint(composition: CompositionV2) -> tuple:
    rows = []
    for track in composition.tracks:
        if track.role not in {"melody", "lead"}:
            continue
        for event in track.events:
            rows.append((track.id, event.pitch, event.start_tick, event.duration_ticks))
    return tuple(rows)


async def apply_arrangement_stage(
    *,
    project_id: str,
    branch_id: str,
    run_id: str,
    composition: CompositionV2,
    plan: ProjectPlanV1,
    db_path: Path | str | None = None,
) -> StageOutcome:
    before_melody = _melody_fingerprint(composition)
    context = build_initial_context(composition)
    context, _stage, _codes = await _run_agent_node(
        "arrangement",
        context,
        agent_model_overrides=None,
        selection={"preserve_melody": True},
    )
    realized = context.working_draft_composition
    try:
        if _melody_fingerprint(realized) != before_melody:
            raise AutonomousConstraintError(
                "melody moved",
                completion_code="arrangement_preserved",
            )
        assert_stage_completion(
            "no_forbidden_instruments",
            composition=realized,
            plan=plan,
        )
    except AutonomousConstraintError as exc:
        update_stage_status(
            run_id,
            "arrangement",
            "failed",
            failure_code=exc.code,
            completion_code=exc.completion_code,
            recoverable=0,
            db_path=db_path,
        )
        return StageOutcome("failed", None, exc.code)
    revision_id = commit_autonomous_stage(
        project_id=project_id,
        branch_id=branch_id,
        composition=realized,
        run_id=run_id,
        db_path=db_path,
    )
    update_stage_status(
        run_id,
        "arrangement",
        "completed",
        revision_id=revision_id,
        set_revision_id=True,
        completion_code="arrangement_preserved",
        db_path=db_path,
    )
    return StageOutcome("completed", revision_id, None)


async def apply_expression_stage(
    *,
    project_id: str,
    branch_id: str,
    run_id: str,
    composition: CompositionV2,
    plan: ProjectPlanV1,
    db_path: Path | str | None = None,
) -> StageOutcome:
    before = pitch_timing_fingerprint(composition)
    ensure_registry()
    agent = get_agent("performance_expression")
    context = build_initial_context(composition)
    bands = [
        {"start_bar": section.start_bar, "density": section.density}
        for section in plan.sections
    ]
    request = AgentRunRequest(
        agent_id="performance_expression",
        operation=AgentOperation.ADVISE,
        context=context,
        selection={"apply_expression": True, "section_bands": bands},
    )
    result = await agent.run(request)
    realized = result.working_draft_update or composition
    after = pitch_timing_fingerprint(realized)
    if after != before:
        logger.warning(
            "Expression pitch drift",
            extra={
                "before_prefix": edit_fingerprint_log_prefix(before),
                "after_prefix": edit_fingerprint_log_prefix(after),
            },
        )
        update_stage_status(
            run_id,
            "expression",
            "failed",
            failure_code="autonomous_constraint_failed",
            completion_code="expression_pitch_stable",
            recoverable=0,
            db_path=db_path,
        )
        return StageOutcome("failed", None, "autonomous_constraint_failed")
    revision_id = commit_autonomous_stage(
        project_id=project_id,
        branch_id=branch_id,
        composition=realized,
        run_id=run_id,
        db_path=db_path,
    )
    update_stage_status(
        run_id,
        "expression",
        "completed",
        revision_id=revision_id,
        set_revision_id=True,
        completion_code="expression_pitch_stable",
        db_path=db_path,
    )
    return StageOutcome("completed", revision_id, None)
