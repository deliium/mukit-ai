"""SQLite rows for one autonomous composition run and its stages.

This module owns persistence. ``ai_agents`` must not import it. It never
writes ``composition_json``.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.autonomous_composer_schemas import ProjectPlanV1
from app.autonomous_composer_settings import load_autonomous_composer_settings
from app.db.connection import get_connection, get_project_db_path
from app.services.persistence_secret_guard import (
    PersistenceSecretError,
    assert_payload_has_no_secret_values,
)

logger = logging.getLogger(__name__)

BRIEF_JSON_MAX_BYTES = 16_384
PLAN_JSON_MAX_BYTES = 65_536

AUTONOMOUS_RUN_LIMIT = "autonomous_run_limit"
PERSISTENCE_SECRET_REJECTED = "persistence_secret_rejected"
AUTONOMOUS_RUN_NOT_FOUND = "autonomous_run_not_found"
STAGE_INTERRUPTED = "autonomous_stage_interrupted"

RUN_STATUSES = frozenset(
    {
        "pending",
        "running",
        "completed",
        "failed",
        "awaiting_approval",
        "cancelled",
    }
)
STAGE_STATUSES = frozenset(
    {
        "pending",
        "running",
        "completed",
        "failed",
        "awaiting_approval",
        "skipped",
    }
)


class AutonomousStoreError(ValueError):
    """Store failure with a public code and no document body."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class AutonomousStageRecord:
    run_id: str
    stage_id: str
    position: int
    agent_id: str | None
    status: str
    depends_on: tuple[str, ...]
    target_section_ids: tuple[str, ...]
    completion_codes: tuple[str, ...]
    artifact_ids: tuple[str, ...]
    revision_id: str | None
    failure_code: str | None
    completion_code: str | None
    recoverable: int
    started_at: str | None
    finished_at: str | None


@dataclass(frozen=True)
class AutonomousRunRecord:
    id: str
    project_id: str
    branch_id: str
    operation_run_id: str
    status: str
    head_revision_id: str | None
    composition_fingerprint: str | None
    agent_operation_count: int
    revision_pass_count: int
    prompt_tokens: int | None
    completion_tokens: int | None
    provider_reported_cost_micros: int | None
    active_runtime_ms: int
    failure_code: str | None
    budget_code: str | None
    seed: int | None
    include_rendering: bool
    created_at: str
    updated_at: str
    stages: tuple[AutonomousStageRecord, ...]


def insert_run(
    *,
    project_id: str,
    branch_id: str,
    operation_run_id: str,
    brief: dict[str, Any],
    plan: ProjectPlanV1 | dict[str, Any],
    seed: int | None = None,
    include_rendering: bool = False,
    run_id: str | None = None,
    db_path: Path | str | None = None,
) -> AutonomousRunRecord:
    """Insert a pending run and one pending stage per plan row."""
    path = Path(db_path) if db_path is not None else get_project_db_path()
    plan_model = plan if isinstance(plan, ProjectPlanV1) else ProjectPlanV1.model_validate(plan)
    brief_text = _bounded_json(brief, limit=BRIEF_JSON_MAX_BYTES, field_name="brief_json")
    plan_text = _bounded_json(
        plan_model.model_dump(),
        limit=PLAN_JSON_MAX_BYTES,
        field_name="plan_json",
    )
    _reject_secrets(brief, plan_model.model_dump())
    new_id = run_id or str(uuid.uuid4())
    now = _utc_now_iso()
    settings = load_autonomous_composer_settings()
    with get_connection(path) as conn:
        existing = conn.execute(
            "SELECT COUNT(*) AS n FROM autonomous_runs WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        if int(existing["n"]) >= settings.max_runs_per_project:
            logger.warning(
                "Autonomous run cap reached",
                extra={"code": AUTONOMOUS_RUN_LIMIT, "project_id": project_id},
            )
            raise AutonomousStoreError("run limit reached", code=AUTONOMOUS_RUN_LIMIT)
        conn.execute(
            """
            INSERT INTO autonomous_runs (
                id, project_id, branch_id, operation_run_id, status,
                brief_json, plan_json, head_revision_id, composition_fingerprint,
                agent_operation_count, revision_pass_count, prompt_tokens,
                completion_tokens, provider_reported_cost_micros, active_runtime_ms,
                failure_code, budget_code, seed, include_rendering, created_at, updated_at
            ) VALUES (
                ?, ?, ?, ?, 'pending',
                ?, ?, NULL, NULL,
                0, 0, NULL,
                NULL, NULL, 0,
                NULL, NULL, ?, ?, ?, ?
            )
            """,
            (
                new_id,
                project_id,
                branch_id,
                operation_run_id,
                brief_text,
                plan_text,
                seed,
                1 if include_rendering else 0,
                now,
                now,
            ),
        )
        for position, stage in enumerate(plan_model.stages):
            conn.execute(
                """
                INSERT INTO autonomous_stages (
                    run_id, stage_id, position, agent_id, status,
                    depends_on_json, target_section_ids_json, completion_json,
                    artifact_ids_json, revision_id, failure_code, completion_code,
                    recoverable, started_at, finished_at
                ) VALUES (?, ?, ?, ?, 'pending', ?, '[]', ?, '[]', NULL, NULL, NULL, 1, NULL, NULL)
                """,
                (
                    new_id,
                    stage.stage_id,
                    position,
                    stage.agent_id,
                    json.dumps(list(stage.depends_on)),
                    json.dumps(list(stage.completion_codes)),
                ),
            )
    logger.info(
        "Inserted autonomous run",
        extra={
            "run_id": new_id[:16],
            "project_id": project_id,
            "stage_count": len(plan_model.stages),
        },
    )
    return get_run(new_id, db_path=path)


def get_run(run_id: str, *, db_path: Path | str | None = None) -> AutonomousRunRecord:
    path = Path(db_path) if db_path is not None else get_project_db_path()
    with get_connection(path) as conn:
        row = conn.execute(
            "SELECT * FROM autonomous_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise AutonomousStoreError("run not found", code=AUTONOMOUS_RUN_NOT_FOUND)
        stages = conn.execute(
            """
            SELECT * FROM autonomous_stages
            WHERE run_id = ?
            ORDER BY position ASC
            """,
            (run_id,),
        ).fetchall()
    return _run_from_rows(row, stages)


def get_run_by_operation(
    operation_run_id: str,
    *,
    db_path: Path | str | None = None,
) -> AutonomousRunRecord | None:
    path = Path(db_path) if db_path is not None else get_project_db_path()
    with get_connection(path) as conn:
        row = conn.execute(
            "SELECT id FROM autonomous_runs WHERE operation_run_id = ?",
            (operation_run_id,),
        ).fetchone()
    if row is None:
        return None
    return get_run(str(row["id"]), db_path=path)


def reconcile_interrupted_stages(
    run_id: str,
    *,
    db_path: Path | str | None = None,
) -> tuple[tuple[str, str], ...]:
    """Finish stages left ``running`` when the process stopped."""
    path = Path(db_path) if db_path is not None else get_project_db_path()
    changes: list[tuple[str, str]] = []
    now = _utc_now_iso()
    with get_connection(path) as conn:
        rows = conn.execute(
            """
            SELECT stage_id, revision_id
            FROM autonomous_stages
            WHERE run_id = ? AND status = 'running'
            ORDER BY position ASC
            """,
            (run_id,),
        ).fetchall()
        for row in rows:
            stage_id = str(row["stage_id"])
            if row["revision_id"]:
                conn.execute(
                    """
                    UPDATE autonomous_stages
                    SET status = 'completed', finished_at = ?
                    WHERE run_id = ? AND stage_id = ?
                    """,
                    (now, run_id, stage_id),
                )
                changes.append((stage_id, "completed"))
            else:
                conn.execute(
                    """
                    UPDATE autonomous_stages
                    SET status = 'failed',
                        failure_code = ?,
                        recoverable = 1,
                        finished_at = ?
                    WHERE run_id = ? AND stage_id = ?
                    """,
                    (STAGE_INTERRUPTED, now, run_id, stage_id),
                )
                changes.append((stage_id, "failed"))
        if changes:
            conn.execute(
                "UPDATE autonomous_runs SET updated_at = ? WHERE id = ?",
                (now, run_id),
            )
    for stage_id, status in changes:
        logger.info(
            "Reconciled interrupted autonomous stage",
            extra={"run_id": run_id[:16], "stage_id": stage_id, "status": status},
        )
    return tuple(changes)


def update_stage_status(
    run_id: str,
    stage_id: str,
    status: str,
    *,
    revision_id: str | None = None,
    failure_code: str | None = None,
    completion_code: str | None = None,
    recoverable: int | None = None,
    artifact_ids: list[str] | None = None,
    set_revision_id: bool = False,
    db_path: Path | str | None = None,
) -> None:
    if status not in STAGE_STATUSES:
        raise AutonomousStoreError("invalid stage status", code="autonomous_stage_invalid")
    path = Path(db_path) if db_path is not None else get_project_db_path()
    now = _utc_now_iso()
    with get_connection(path) as conn:
        current = conn.execute(
            "SELECT started_at FROM autonomous_stages WHERE run_id = ? AND stage_id = ?",
            (run_id, stage_id),
        ).fetchone()
        if current is None:
            raise AutonomousStoreError("stage not found", code="autonomous_stage_not_found")
        started_at = current["started_at"] or (now if status == "running" else None)
        finished_at = now if status in {"completed", "failed", "skipped", "awaiting_approval"} else None
        assignments = [
            "status = ?",
            "started_at = ?",
            "finished_at = ?",
            "failure_code = ?",
            "completion_code = ?",
        ]
        params: list[Any] = [status, started_at, finished_at, failure_code, completion_code]
        if recoverable is not None:
            assignments.append("recoverable = ?")
            params.append(int(recoverable))
        if set_revision_id:
            assignments.append("revision_id = ?")
            params.append(revision_id)
        if artifact_ids is not None:
            assignments.append("artifact_ids_json = ?")
            params.append(json.dumps(artifact_ids))
        params.extend([run_id, stage_id])
        conn.execute(
            f"UPDATE autonomous_stages SET {', '.join(assignments)} WHERE run_id = ? AND stage_id = ?",
            params,
        )
        conn.execute(
            "UPDATE autonomous_runs SET updated_at = ? WHERE id = ?",
            (now, run_id),
        )
    logger.info(
        "Autonomous stage transition",
        extra={
            "run_id": run_id[:16],
            "stage_id": stage_id,
            "status": status,
            "failure_code": failure_code,
            "budget_code": None,
        },
    )


def update_run_fields(
    run_id: str,
    *,
    status: str | None = None,
    head_revision_id: str | None = None,
    composition_fingerprint: str | None = None,
    agent_operation_count: int | None = None,
    revision_pass_count: int | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    provider_reported_cost_micros: int | None = None,
    active_runtime_ms: int | None = None,
    failure_code: str | None = None,
    budget_code: str | None = None,
    set_head_revision_id: bool = False,
    set_fingerprint: bool = False,
    db_path: Path | str | None = None,
) -> None:
    if status is not None and status not in RUN_STATUSES:
        raise AutonomousStoreError("invalid run status", code="autonomous_run_invalid")
    path = Path(db_path) if db_path is not None else get_project_db_path()
    now = _utc_now_iso()
    assignments = ["updated_at = ?"]
    params: list[Any] = [now]
    if status is not None:
        assignments.append("status = ?")
        params.append(status)
    if set_head_revision_id:
        assignments.append("head_revision_id = ?")
        params.append(head_revision_id)
    if set_fingerprint:
        assignments.append("composition_fingerprint = ?")
        params.append(composition_fingerprint)
    for column, value in (
        ("agent_operation_count", agent_operation_count),
        ("revision_pass_count", revision_pass_count),
        ("prompt_tokens", prompt_tokens),
        ("completion_tokens", completion_tokens),
        ("provider_reported_cost_micros", provider_reported_cost_micros),
        ("active_runtime_ms", active_runtime_ms),
        ("failure_code", failure_code),
        ("budget_code", budget_code),
    ):
        if value is not None:
            assignments.append(f"{column} = ?")
            params.append(value)
    params.append(run_id)
    with get_connection(path) as conn:
        conn.execute(
            f"UPDATE autonomous_runs SET {', '.join(assignments)} WHERE id = ?",
            params,
        )
    logger.info(
        "Autonomous run fields updated",
        extra={
            "run_id": run_id[:16],
            "status": status,
            "failure_code": failure_code,
            "budget_code": budget_code,
        },
    )


def _reject_secrets(brief: dict[str, Any], plan: dict[str, Any]) -> None:
    try:
        assert_payload_has_no_secret_values(brief, context="autonomous_brief")
        assert_payload_has_no_secret_values(plan, context="autonomous_plan")
    except PersistenceSecretError as exc:
        logger.warning(
            "Autonomous payload rejected",
            extra={"code": PERSISTENCE_SECRET_REJECTED},
        )
        raise AutonomousStoreError(
            "secret rejected",
            code=PERSISTENCE_SECRET_REJECTED,
        ) from exc


def _bounded_json(payload: dict[str, Any], *, limit: int, field_name: str) -> str:
    text = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    if len(text.encode("utf-8")) > limit:
        raise AutonomousStoreError(
            f"{field_name} exceeds limit",
            code="autonomous_payload_too_large",
        )
    return text


def _run_from_rows(row: Any, stage_rows: list[Any]) -> AutonomousRunRecord:
    return AutonomousRunRecord(
        id=str(row["id"]),
        project_id=str(row["project_id"]),
        branch_id=str(row["branch_id"]),
        operation_run_id=str(row["operation_run_id"]),
        status=str(row["status"]),
        head_revision_id=row["head_revision_id"],
        composition_fingerprint=row["composition_fingerprint"],
        agent_operation_count=int(row["agent_operation_count"]),
        revision_pass_count=int(row["revision_pass_count"]),
        prompt_tokens=row["prompt_tokens"],
        completion_tokens=row["completion_tokens"],
        provider_reported_cost_micros=row["provider_reported_cost_micros"],
        active_runtime_ms=int(row["active_runtime_ms"]),
        failure_code=row["failure_code"],
        budget_code=row["budget_code"],
        seed=row["seed"],
        include_rendering=bool(row["include_rendering"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        stages=tuple(_stage_from_row(item) for item in stage_rows),
    )


def _stage_from_row(row: Any) -> AutonomousStageRecord:
    return AutonomousStageRecord(
        run_id=str(row["run_id"]),
        stage_id=str(row["stage_id"]),
        position=int(row["position"]),
        agent_id=row["agent_id"],
        status=str(row["status"]),
        depends_on=tuple(json.loads(row["depends_on_json"])),
        target_section_ids=tuple(json.loads(row["target_section_ids_json"])),
        completion_codes=tuple(json.loads(row["completion_json"])),
        artifact_ids=tuple(json.loads(row["artifact_ids_json"])),
        revision_id=row["revision_id"],
        failure_code=row["failure_code"],
        completion_code=row["completion_code"],
        recoverable=int(row["recoverable"]),
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
