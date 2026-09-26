"""Apply a stored critique as a revision stage without rerunning the spine.

Approve skips the stage. A pass that fails preserve or crashes does not commit.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from app.ai_agents.revision_loop import run_targeted_revision_passes
from app.ai_agents.schemas import AgentArtifactV1, CritiqueRecommendation
from app.composition_schemas import CompositionV2
from app.services.autonomous_composer_store import update_stage_status
from app.services.autonomous_symbolic import commit_autonomous_stage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RevisionStageOutcome:
    status: str
    revision_id: str | None
    failure_code: str | None
    stop_reason: str | None


async def apply_revision_stage(
    *,
    project_id: str,
    branch_id: str,
    run_id: str,
    composition: CompositionV2,
    critique: AgentArtifactV1,
    max_revisions: int | None = 1,
    critic_parameters: dict | None = None,
    db_path: Path | str | None = None,
) -> RevisionStageOutcome:
    """Skip, revise, or fail the revision stage. Commits only a changed valid draft."""
    findings = critique.payload.get("findings")
    findings_count = len(findings) if isinstance(findings, list) else 0
    recommendation = str(critique.payload.get("recommendation") or "")
    logger.info(
        "Autonomous revision stage started",
        extra={"run_id": run_id[:16], "findings_count": findings_count},
    )
    if recommendation != CritiqueRecommendation.REVISE.value:
        update_stage_status(
            run_id,
            "revision",
            "skipped",
            completion_code="revision_contained",
            db_path=db_path,
        )
        logger.info(
            "Autonomous revision skipped",
            extra={
                "pass_index": 0,
                "target_agent_ids": [],
                "stop_reason": "critic_approve",
                "budget_code": None,
                "findings_count": findings_count,
            },
        )
        return RevisionStageOutcome(
            status="skipped",
            revision_id=None,
            failure_code=None,
            stop_reason="critic_approve",
        )

    result = await run_targeted_revision_passes(
        composition,
        critique,
        max_revisions=max_revisions,
        critic_parameters=critic_parameters,
    )
    if result.failure_code == "operation_model_crashed":
        update_stage_status(
            run_id,
            "revision",
            "failed",
            failure_code="operation_model_crashed",
            recoverable=0,
            db_path=db_path,
        )
        logger.info(
            "Autonomous revision crashed",
            extra={
                "pass_index": result.pass_count,
                "stop_reason": result.stop_reason.value,
                "budget_code": result.budget_code,
                "failure_code": result.failure_code,
            },
        )
        return RevisionStageOutcome(
            status="failed",
            revision_id=None,
            failure_code="operation_model_crashed",
            stop_reason=result.stop_reason.value,
        )

    revision_id = None
    if result.changed and result.failure_code is None and result.stop_reason.value != "validation_failed_kept_last_valid":
        revision_id = commit_autonomous_stage(
            project_id=project_id,
            branch_id=branch_id,
            composition=result.composition,
            run_id=run_id,
            db_path=db_path,
        )
    update_stage_status(
        run_id,
        "revision",
        "completed",
        revision_id=revision_id,
        set_revision_id=revision_id is not None,
        completion_code="revision_contained",
        failure_code=None,
        db_path=db_path,
    )
    logger.info(
        "Autonomous revision finished",
        extra={
            "pass_index": result.pass_count,
            "stop_reason": result.stop_reason.value,
            "budget_code": result.budget_code,
            "findings_count": findings_count,
        },
    )
    return RevisionStageOutcome(
        status="completed",
        revision_id=revision_id,
        failure_code=None,
        stop_reason=result.stop_reason.value,
    )
