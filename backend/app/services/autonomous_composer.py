"""Schedule specialized agents for one creative brief and commit the score.

The spine preview is not used. This module owns SQLite writes.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from app.ai_agents.registry import ensure_registry
from app.ai_agents.schemas import AgentArtifactKind, AgentArtifactV1
from app.ai_agents.workflow import build_initial_context
from app.autonomous_composer_schemas import (
    AutonomousRunViewV1,
    AutonomousStageViewV1,
    CreativeBriefV1,
    ProjectPlanV1,
)
from app.autonomous_composer_settings import load_autonomous_composer_settings
from app.composition_schemas import CompositionV2
from app.services.agent_artifact_workspace import insert_durable
from app.services.autonomous_composer_store import (
    AutonomousStoreError,
    get_run,
    get_run_by_operation,
    insert_run,
    update_run_fields,
    update_stage_status,
)
from app.services.autonomous_expression import apply_arrangement_stage, apply_expression_stage
from app.services.autonomous_project_plan import compile_project_plan
from app.services.autonomous_revision import apply_revision_stage
from app.services.autonomous_symbolic import commit_autonomous_stage, realize_symbolic_stage
from app.services.autonomous_constraints import AutonomousConstraintError, assert_stage_completion
from app.services.project_history_store import _load_branch_command_state
from app.services.project_store import create_project, get_project
from app.db.connection import get_connection

logger = logging.getLogger(__name__)

CancelCheck = Callable[[], bool | Awaitable[bool]]


def run_view(run_id: str, *, db_path: Path | str | None = None, summary: dict | None = None) -> AutonomousRunViewV1:
    run = get_run(run_id, db_path=db_path)
    return AutonomousRunViewV1(
        run_id=run.id,
        project_id=run.project_id,
        status=run.status,
        head_revision_id=run.head_revision_id,
        composition_fingerprint=run.composition_fingerprint,
        budget_code=run.budget_code,
        failure_code=run.failure_code,
        stages=[
            AutonomousStageViewV1(
                stage_id=stage.stage_id,
                agent_id=stage.agent_id,
                status=stage.status,
                revision_id=stage.revision_id,
                failure_code=stage.failure_code,
                artifact_ids=list(stage.artifact_ids),
            )
            for stage in run.stages
        ],
        operation_summary=summary,
    )


def _scaffold() -> CompositionV2:
    """In-memory context only. Never written as a revision."""
    return CompositionV2.model_validate(
        {
            "schema_version": "composition.v2",
            "tempo": 72,
            "key": "C major",
            "time_signature": "4/4",
            "ticks_per_quarter": 480,
            "duration_ticks": 1920,
            "bar_count": 1,
            "sections": [
                {
                    "id": "section-1",
                    "type": "intro",
                    "label": "Intro",
                    "start_bar": 1,
                    "bar_count": 1,
                    "start_tick": 0,
                    "duration_ticks": 1920,
                }
            ],
            "tracks": [
                {
                    "id": "piano-1",
                    "name": "Piano",
                    "instrument": "piano",
                    "role": "melody",
                    "midi_program": 0,
                    "channel": 1,
                    "events": [
                        {
                            "pitch": "C4",
                            "start_tick": 0,
                            "duration_ticks": 480,
                            "velocity": 80,
                        }
                    ],
                }
            ],
        }
    )


def _load_working(project_id: str, db_path: Path | str | None) -> CompositionV2:
    record = get_project(project_id, db_path=db_path)
    return CompositionV2.model_validate_json(record.composition_json or "{}")


async def _cancelled(cancel_check: CancelCheck | None) -> bool:
    if cancel_check is None:
        return False
    result = cancel_check()
    if hasattr(result, "__await__"):
        return bool(await result)
    return bool(result)


def _effective_agent_cap(request_cap: int | None) -> int:
    """A request may only lower the env ceiling. Zero on the request does not disable it."""
    cap = load_autonomous_composer_settings().max_agent_operations
    if request_cap is None or request_cap <= 0:
        return cap
    if cap <= 0:
        return cap
    return min(cap, int(request_cap))


def _bump_agent_count(
    run_id: str,
    db_path: Path | str | None,
    *,
    cap: int,
) -> str | None:
    run = get_run(run_id, db_path=db_path)
    if cap > 0 and run.agent_operation_count >= cap:
        logger.warning(
            "Autonomous agent budget refused the next stage",
            extra={"budget_code": "autonomous_agent_operation_budget", "run_id": run_id[:16]},
        )
        return "autonomous_agent_operation_budget"
    update_run_fields(
        run_id,
        agent_operation_count=run.agent_operation_count + 1,
        db_path=db_path,
    )
    return None


def _restore_stage_inputs(
    run: Any,
    *,
    db_path: Path | str | None,
) -> dict[str, Any]:
    """Reload durable plans so a resumed run does not regenerate from an empty scaffold."""
    from app.services.agent_artifact_workspace import get_artifact

    harmony = None
    motif = None
    critique = None
    working = None
    for stage in run.stages:
        if stage.status != "completed" or not stage.artifact_ids:
            continue
        meta = get_artifact(
            run.project_id,
            stage.artifact_ids[-1],
            include_payload=True,
            db_path=db_path,
        )
        payload = meta.get("payload")
        if not isinstance(payload, dict):
            continue
        if stage.stage_id == "harmony_plan":
            harmony = payload
        elif stage.stage_id == "motif_plan":
            motif = payload
        elif stage.stage_id == "critique":
            critique = AgentArtifactV1(
                artifact_id=str(meta["artifact_id"]),
                kind=AgentArtifactKind(str(meta["kind"])),
                producer_agent_id=str(meta["producer_agent_id"]),
                content_type=str(meta["content_type"]),
                payload=payload,
            )
    if run.head_revision_id:
        working = _load_working(run.project_id, db_path)
    return {"harmony": harmony, "motif": motif, "critique": critique, "working": working}


def dispatch_render(run_id: str, *, db_path: Path | str | None = None) -> str:
    """Enqueue one neural render. Does not change the composition fingerprint."""
    from app.neural_audio_schemas import NeuralAudioEnqueueRequest
    from app.services.neural_audio_render import enqueue_neural_audio_render

    run = get_run(run_id, db_path=db_path)
    before = run.composition_fingerprint
    job = enqueue_neural_audio_render(
        NeuralAudioEnqueueRequest(
            project_id=run.project_id,
            source_revision_id=run.head_revision_id,
            operation_run_id=run.operation_run_id,
            seed=run.seed or 0,
        ),
        db_path=db_path,
    )
    after = get_run(run_id, db_path=db_path).composition_fingerprint
    if before != after:
        raise AutonomousStoreError(
            "render changed the composition fingerprint",
            code="autonomous_constraint_failed",
        )
    logger.info(
        "Autonomous render enqueued",
        extra={"run_id": run_id[:16], "job_prefix": str(job.id)[:16]},
    )
    return str(job.id)


def approve_render_stage(run_id: str, *, db_path: Path | str | None = None) -> None:
    run = get_run(run_id, db_path=db_path)
    stage = next((row for row in run.stages if row.stage_id == "render"), None)
    if stage is None or stage.status != "awaiting_approval":
        raise AutonomousStoreError(
            "render stage is not awaiting approval",
            code="autonomous_run_not_resumable",
        )
    job_id = dispatch_render(run_id, db_path=db_path)
    update_stage_status(
        run_id,
        "render",
        "completed",
        completion_code="render_dispatched",
        artifact_ids=[job_id],
        db_path=db_path,
    )
    _finish_run_status(run_id, db_path)


def skip_render_stage(run_id: str, *, db_path: Path | str | None = None) -> None:
    run = get_run(run_id, db_path=db_path)
    stage = next((row for row in run.stages if row.stage_id == "render"), None)
    if stage is None or stage.status != "awaiting_approval":
        raise AutonomousStoreError(
            "render stage is not awaiting approval",
            code="autonomous_run_not_resumable",
        )
    update_stage_status(
        run_id,
        "render",
        "skipped",
        completion_code="render_dispatched",
        db_path=db_path,
    )
    _finish_run_status(run_id, db_path)


def _finish_run_status(run_id: str, db_path: Path | str | None) -> None:
    fresh = get_run(run_id, db_path=db_path)
    statuses = {row.status for row in fresh.stages}
    if statuses <= {"completed", "skipped"}:
        update_run_fields(run_id, status="completed", db_path=db_path)
    elif "awaiting_approval" in statuses:
        update_run_fields(run_id, status="awaiting_approval", db_path=db_path)


async def execute_autonomous_run(
    run_id: str,
    *,
    db_path: Path | str | None = None,
    cancel_check: CancelCheck | None = None,
    render_approval: str = "required",
    max_agent_operations: int | None = None,
) -> None:
    """Walk pending stages in graph order. Completed stages are left alone."""
    started = time.perf_counter()
    ensure_registry()
    run = get_run(run_id, db_path=db_path)
    path = Path(db_path) if db_path is not None else None
    with get_connection(path) as conn:
        row = conn.execute("SELECT plan_json FROM autonomous_runs WHERE id = ?", (run_id,)).fetchone()
    plan = ProjectPlanV1.model_validate_json(row["plan_json"])
    selection = {"compiled_project_plan": plan.model_dump(mode="json")}
    scaffold = _scaffold()
    context = build_initial_context(scaffold)
    working: CompositionV2 | None = None
    critique: AgentArtifactV1 | None = None
    harmony_payload: dict[str, Any] | None = None
    motif_payload: dict[str, Any] | None = None
    agent_cap = _effective_agent_cap(max_agent_operations)
    restored = _restore_stage_inputs(run, db_path=db_path)
    harmony_payload = restored["harmony"]
    motif_payload = restored["motif"]
    critique = restored["critique"]
    working = restored["working"]
    if working is not None:
        context = build_initial_context(working)
    update_run_fields(run_id, status="running", db_path=db_path)
    logger.info(
        "Autonomous run started",
        extra={
            "run_id": run_id[:16],
            "project_id": run.project_id,
            "include_rendering": run.include_rendering,
            "stage_count": len(run.stages),
        },
    )

    for stage in run.stages:
        if stage.status in {"completed", "skipped"}:
            continue
        if stage.status == "failed" and not stage.recoverable:
            update_run_fields(run_id, status="failed", failure_code=stage.failure_code, db_path=db_path)
            return
        if await _cancelled(cancel_check):
            update_stage_status(
                run_id,
                stage.stage_id,
                "failed",
                failure_code="operation_cancelled",
                recoverable=0,
                db_path=db_path,
            )
            update_run_fields(run_id, status="cancelled", failure_code="operation_cancelled", db_path=db_path)
            return
        update_stage_status(run_id, stage.stage_id, "running", db_path=db_path)
        try:
            if stage.stage_id in {"plan", "harmony_plan", "motif_plan", "critique"}:
                from app.ai_agents.registry import get_agent
                from app.ai_agents.schemas import AgentOperation, AgentRunRequest

                agent_id = {
                    "plan": "creative_director",
                    "harmony_plan": "harmony",
                    "motif_plan": "melody_motif",
                    "critique": "critic",
                }[stage.stage_id]
                budget = _bump_agent_count(run_id, db_path, cap=agent_cap)
                if budget:
                    update_stage_status(run_id, stage.stage_id, "pending", db_path=db_path)
                    update_run_fields(run_id, status="failed", budget_code=budget, db_path=db_path)
                    return
                operation = (
                    AgentOperation.CRITIQUE
                    if stage.stage_id == "critique"
                    else AgentOperation.PLAN
                )
                request = AgentRunRequest(
                    agent_id=agent_id,
                    operation=operation,
                    context=context,
                    selection=selection,
                )
                result = await get_agent(agent_id).run(request)
                artifact = result.artifacts[-1] if result.artifacts else None
                artifact_id = None
                if artifact is not None:
                    artifact_id = insert_durable(run.project_id, artifact, db_path=db_path)
                    if stage.stage_id == "harmony_plan":
                        harmony_payload = dict(artifact.payload)
                    if stage.stage_id == "motif_plan":
                        motif_payload = dict(artifact.payload)
                    if stage.stage_id == "critique":
                        critique = artifact
                if result.working_draft_update is not None:
                    context = context.with_working_draft(result.working_draft_update)
                completion_code = {
                    "plan": "project_plan_valid",
                    "harmony_plan": "harmony_plan_key",
                    "motif_plan": "motif_plan_present",
                    "critique": "critique_stored",
                }[stage.stage_id]
                assert_stage_completion(
                    completion_code,
                    plan=plan,
                    merged_plan=artifact.payload if artifact is not None and stage.stage_id == "plan" else None,
                    artifact_payload=dict(artifact.payload) if artifact is not None else None,
                    artifact_content_type=artifact.content_type if artifact is not None else None,
                    artifact_id=artifact_id,
                )
                if stage.stage_id == "plan" and artifact is not None:
                    plan = ProjectPlanV1.model_validate(artifact.payload)
                    selection = {
                        **selection,
                        "compiled_project_plan": plan.model_dump(mode="json"),
                    }
                update_stage_status(
                    run_id,
                    stage.stage_id,
                    "completed",
                    artifact_ids=[artifact_id] if artifact_id else [],
                    completion_code=completion_code,
                    db_path=db_path,
                )
            elif stage.stage_id == "symbolic":
                if harmony_payload is None or motif_payload is None:
                    raise AutonomousConstraintError(
                        "plans missing",
                        completion_code="composition_v2_ok",
                    )
                result = realize_symbolic_stage(
                    plan,
                    harmony_payload,
                    motif_payload,
                    seed=run.seed or 0,
                )
                revision_id = commit_autonomous_stage(
                    project_id=run.project_id,
                    branch_id=run.branch_id,
                    composition=result.composition,
                    run_id=run_id,
                    db_path=db_path,
                )
                working = result.composition
                context = build_initial_context(working)
                update_stage_status(
                    run_id,
                    "symbolic",
                    "completed",
                    revision_id=revision_id,
                    set_revision_id=True,
                    completion_code="composition_v2_ok",
                    db_path=db_path,
                )
            elif stage.stage_id == "revision":
                if critique is None or working is None:
                    raise AutonomousConstraintError("critique missing", completion_code="revision_contained")
                await apply_revision_stage(
                    project_id=run.project_id,
                    branch_id=run.branch_id,
                    run_id=run_id,
                    composition=working,
                    critique=critique,
                    db_path=db_path,
                )
                latest = get_run(run_id, db_path=db_path)
                if latest.head_revision_id:
                    working = _load_working(run.project_id, db_path)
            elif stage.stage_id == "arrangement":
                if working is None:
                    working = _load_working(run.project_id, db_path)
                budget = _bump_agent_count(run_id, db_path, cap=agent_cap)
                if budget:
                    update_stage_status(run_id, "arrangement", "pending", db_path=db_path)
                    update_run_fields(run_id, status="failed", budget_code=budget, db_path=db_path)
                    return
                outcome = await apply_arrangement_stage(
                    project_id=run.project_id,
                    branch_id=run.branch_id,
                    run_id=run_id,
                    composition=working,
                    plan=plan,
                    db_path=db_path,
                )
                if outcome.status == "failed":
                    update_run_fields(
                        run_id,
                        status="failed",
                        failure_code=outcome.failure_code,
                        db_path=db_path,
                    )
                    return
                working = _load_working(run.project_id, db_path)
            elif stage.stage_id == "expression":
                if working is None:
                    working = _load_working(run.project_id, db_path)
                budget = _bump_agent_count(run_id, db_path, cap=agent_cap)
                if budget:
                    update_stage_status(run_id, "expression", "pending", db_path=db_path)
                    update_run_fields(run_id, status="failed", budget_code=budget, db_path=db_path)
                    return
                outcome = await apply_expression_stage(
                    project_id=run.project_id,
                    branch_id=run.branch_id,
                    run_id=run_id,
                    composition=working,
                    plan=plan,
                    db_path=db_path,
                )
                if outcome.status == "failed":
                    update_run_fields(
                        run_id,
                        status="failed",
                        failure_code=outcome.failure_code,
                        db_path=db_path,
                    )
                    return
                working = _load_working(run.project_id, db_path)
            elif stage.stage_id == "render":
                if not run.include_rendering:
                    update_stage_status(
                        run_id,
                        "render",
                        "skipped",
                        completion_code="render_dispatched",
                        db_path=db_path,
                    )
                elif render_approval == "auto":
                    job_id = dispatch_render(run_id, db_path=db_path)
                    update_stage_status(
                        run_id,
                        "render",
                        "completed",
                        completion_code="render_dispatched",
                        artifact_ids=[job_id],
                        db_path=db_path,
                    )
                else:
                    update_stage_status(
                        run_id,
                        "render",
                        "awaiting_approval",
                        completion_code="render_dispatched",
                        db_path=db_path,
                    )
                    update_run_fields(run_id, status="awaiting_approval", db_path=db_path)
                    return
        except AutonomousConstraintError as exc:
            update_stage_status(
                run_id,
                stage.stage_id,
                "failed",
                failure_code=exc.code,
                completion_code=exc.completion_code,
                recoverable=exc.recoverable,
                db_path=db_path,
            )
            update_run_fields(run_id, status="failed", failure_code=exc.code, db_path=db_path)
            return
        except AutonomousStoreError as exc:
            update_stage_status(
                run_id,
                stage.stage_id,
                "failed",
                failure_code=exc.code,
                recoverable=0,
                db_path=db_path,
            )
            update_run_fields(run_id, status="failed", failure_code=exc.code, db_path=db_path)
            return

    latest = get_run(run_id, db_path=db_path)
    if any(stage.status == "failed" for stage in latest.stages):
        status = "failed"
    elif any(stage.status == "awaiting_approval" for stage in latest.stages):
        status = "awaiting_approval"
    elif all(stage.status in {"completed", "skipped"} for stage in latest.stages):
        status = "completed"
    else:
        status = latest.status
    elapsed = int((time.perf_counter() - started) * 1000)
    update_run_fields(run_id, status=status, active_runtime_ms=elapsed, db_path=db_path)
    finished = get_run(run_id, db_path=db_path)
    logger.info(
        "Autonomous run finished",
        extra={
            "run_id": run_id[:16],
            "status": finished.status,
            "failure_code": finished.failure_code,
            "budget_code": finished.budget_code,
            "active_runtime_ms": elapsed,
            "agent_operation_count": finished.agent_operation_count,
        },
    )


def prepare_run(
    brief: CreativeBriefV1,
    *,
    project_id: str | None,
    operation_run_id: str,
    include_rendering: bool,
    seed: int,
    expected_working_version: int | None,
    expected_head_revision_id: str | None,
    expected_source_fingerprint: str | None,
    db_path: Path | str | None,
):
    existing = get_run_by_operation(operation_run_id, db_path=db_path)
    if existing is not None and existing.status not in {"failed", "cancelled"}:
        return existing
    plan = compile_project_plan(brief)
    if project_id is None:
        created = create_project(brief.title or "Autonomous piece", db_path=db_path)
        project_id = created.id
        branch_id = created.active_branch_id or ""
    else:
        created = get_project(project_id, db_path=db_path)
        branch_id = created.active_branch_id or ""
        if expected_source_fingerprint is not None:
            path = Path(db_path) if db_path is not None else None
            with get_connection(path) as conn:
                state = _load_branch_command_state(conn, project_id, branch_id)
            if (
                state.working_fingerprint != expected_source_fingerprint
                or (
                    expected_working_version is not None
                    and state.working_version != expected_working_version
                )
                or (
                    expected_head_revision_id is not None
                    and state.head_revision_id != expected_head_revision_id
                )
            ):
                raise AutonomousStoreError("revision conflict", code="project_revision_conflict")
    return insert_run(
        project_id=project_id,
        branch_id=branch_id,
        operation_run_id=operation_run_id,
        brief=brief.model_dump(mode="json"),
        plan=plan,
        seed=seed,
        include_rendering=include_rendering,
        db_path=db_path,
    )
