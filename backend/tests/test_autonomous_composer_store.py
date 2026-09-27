"""Durable autonomous run and stage rows."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from alembic.script import ScriptDirectory

from app.db import initialize_database, reset_database_initialization_cache
from app.db.connection import _alembic_config, get_connection
from app.services import project_store
from app.services.autonomous_composer_store import (
    AUTONOMOUS_RUN_LIMIT,
    PERSISTENCE_SECRET_REJECTED,
    AutonomousStoreError,
    get_run,
    insert_run,
    mark_stage_rejected,
    reconcile_interrupted_stages,
    set_checkpoint,
    set_pause_requested,
    set_stage_instruction,
    update_stage_status,
)
from app.services.autonomous_project_plan import compile_project_plan
from app.autonomous_composer_schemas import CreativeBriefV1, NarrativeBeat


@pytest.fixture
def project_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    reset_database_initialization_cache()
    initialize_database()
    return db_path


def _brief() -> CreativeBriefV1:
    return CreativeBriefV1(
        schema_version="creative.brief.v1",
        duration_seconds=150,
        narrative=[
            NarrativeBeat(intent="sparse_opening", text="cold sparse opening"),
            NarrativeBeat(intent="establish_theme", text="introduce Theme A"),
            NarrativeBeat(intent="build", text="increase tension"),
            NarrativeBeat(intent="climax", text="strong climax"),
            NarrativeBeat(intent="resolve", text="quiet transformed ending"),
        ],
        instrumentation=["piano", "cello", "strings"],
        forbidden_instrument_families=["drums"],
        opening_key="F# minor",
        final_section_key="F# major",
    )


def test_migration_creates_tables(project_db: Path) -> None:
    with get_connection(project_db) as conn:
        revision = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert revision["version_num"] == ScriptDirectory.from_config(_alembic_config(project_db)).get_current_head()
    assert "autonomous_runs" in tables
    assert "autonomous_stages" in tables


def test_insert_nine_pending_stages(project_db: Path) -> None:
    created = project_store.create_project("Piece", db_path=project_db)
    plan = compile_project_plan(_brief())
    run = insert_run(
        project_id=created.id,
        branch_id=created.active_branch_id or "",
        operation_run_id="op-1",
        brief=_brief().model_dump(),
        plan=plan,
        db_path=project_db,
    )
    assert run.status == "pending"
    assert len(run.stages) == 9
    assert all(stage.status == "pending" for stage in run.stages)
    assert run.stages[0].stage_id == "plan"
    with get_connection(project_db) as conn:
        composition = conn.execute(
            "SELECT composition_json FROM projects WHERE id = ?",
            (created.id,),
        ).fetchone()
    assert composition["composition_json"] is None


def test_reconcile_running_uses_revision_id(project_db: Path) -> None:
    created = project_store.create_project("Piece", db_path=project_db)
    run = insert_run(
        project_id=created.id,
        branch_id=created.active_branch_id or "",
        operation_run_id="op-2",
        brief=_brief().model_dump(),
        plan=compile_project_plan(_brief()),
        db_path=project_db,
    )
    update_stage_status(run.id, "symbolic", "running", db_path=project_db)
    update_stage_status(run.id, "plan", "running", db_path=project_db)
    with get_connection(project_db) as conn:
        conn.execute(
            """
            UPDATE autonomous_stages
            SET revision_id = 'rev-1', status = 'running'
            WHERE run_id = ? AND stage_id = 'symbolic'
            """,
            (run.id,),
        )
    changes = dict(reconcile_interrupted_stages(run.id, db_path=project_db))
    assert changes["symbolic"] == "completed"
    assert changes["plan"] == "failed"


def test_secret_brief_is_not_written(project_db: Path) -> None:
    created = project_store.create_project("Piece", db_path=project_db)
    brief = _brief().model_dump()
    brief["api_key"] = "sk-not-a-real-key-value"
    with pytest.raises(AutonomousStoreError) as caught:
        insert_run(
            project_id=created.id,
            branch_id=created.active_branch_id or "",
            operation_run_id="op-secret",
            brief=brief,
            plan=compile_project_plan(_brief()),
            db_path=project_db,
        )
    assert caught.value.code == PERSISTENCE_SECRET_REJECTED
    with get_connection(project_db) as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM autonomous_runs").fetchone()
    assert int(count["n"]) == 0


def test_run_cap_does_not_delete_rows(
    project_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTONOMOUS_MAX_RUNS_PER_PROJECT", "1")
    created = project_store.create_project("Piece", db_path=project_db)
    plan = compile_project_plan(_brief())
    insert_run(
        project_id=created.id,
        branch_id=created.active_branch_id or "",
        operation_run_id="op-a",
        brief=_brief().model_dump(),
        plan=plan,
        db_path=project_db,
    )
    with pytest.raises(AutonomousStoreError) as caught:
        insert_run(
            project_id=created.id,
            branch_id=created.active_branch_id or "",
            operation_run_id="op-b",
            brief=_brief().model_dump(),
            plan=plan,
            db_path=project_db,
        )
    assert caught.value.code == AUTONOMOUS_RUN_LIMIT
    with get_connection(project_db) as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM autonomous_runs").fetchone()
    assert int(count["n"]) == 1


def test_project_delete_cascades(project_db: Path) -> None:
    created = project_store.create_project("Piece", db_path=project_db)
    insert_run(
        project_id=created.id,
        branch_id=created.active_branch_id or "",
        operation_run_id="op-c",
        brief=_brief().model_dump(),
        plan=compile_project_plan(_brief()),
        db_path=project_db,
    )
    project_store.delete_project(created.id, db_path=project_db)
    with get_connection(project_db) as conn:
        runs = conn.execute("SELECT COUNT(*) AS n FROM autonomous_runs").fetchone()
        stages = conn.execute("SELECT COUNT(*) AS n FROM autonomous_stages").fetchone()
    assert int(runs["n"]) == 0
    assert int(stages["n"]) == 0


def test_insert_defaults_to_autonomous(project_db: Path) -> None:
    created = project_store.create_project("Piece", db_path=project_db)
    run = insert_run(
        project_id=created.id,
        branch_id=created.active_branch_id or "",
        operation_run_id="op-mode",
        brief=_brief().model_dump(),
        plan=compile_project_plan(_brief()),
        db_path=project_db,
    )
    assert run.autonomy_mode == "autonomous"
    assert run.checkpoint_id is None
    assert run.pause_requested is False
    assert run.stages[0].instruction is None
    assert run.stages[0].decision is None
    assert run.stages[0].warning_code is None
    assert run.stages[0].rejected_revision_id is None


def test_checkpoint_decision_and_instruction(project_db: Path) -> None:
    created = project_store.create_project("Piece", db_path=project_db)
    run = insert_run(
        project_id=created.id,
        branch_id=created.active_branch_id or "",
        operation_run_id="op-check",
        brief=_brief().model_dump(),
        plan=compile_project_plan(_brief()),
        autonomy_mode="guided",
        db_path=project_db,
    )
    set_checkpoint(
        run.id,
        checkpoint_id="form",
        status="awaiting_approval",
        db_path=project_db,
    )
    set_checkpoint(
        run.id,
        checkpoint_id=None,
        stage_id="plan",
        decision="approved",
        db_path=project_db,
    )
    set_stage_instruction(run.id, "arrangement", "Keep the melody.", db_path=project_db)
    loaded = get_run(run.id, db_path=project_db)
    assert loaded.checkpoint_id is None
    assert loaded.autonomy_mode == "guided"
    plan_stage = next(stage for stage in loaded.stages if stage.stage_id == "plan")
    arrangement = next(stage for stage in loaded.stages if stage.stage_id == "arrangement")
    assert plan_stage.decision == "approved"
    assert arrangement.instruction == "Keep the melody."


def test_instruction_secret_is_not_written(
    project_db: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    created = project_store.create_project("Piece", db_path=project_db)
    run = insert_run(
        project_id=created.id,
        branch_id=created.active_branch_id or "",
        operation_run_id="op-instr-secret",
        brief=_brief().model_dump(),
        plan=compile_project_plan(_brief()),
        db_path=project_db,
    )
    secret = "sk-" + "a" * 20
    caplog.set_level(logging.WARNING)
    with pytest.raises(AutonomousStoreError) as caught:
        set_stage_instruction(run.id, "arrangement", f"use {secret}", db_path=project_db)
    assert caught.value.code == PERSISTENCE_SECRET_REJECTED
    loaded = get_run(run.id, db_path=project_db)
    arrangement = next(stage for stage in loaded.stages if stage.stage_id == "arrangement")
    assert arrangement.instruction is None
    for record in caplog.records:
        assert secret not in record.getMessage()


def test_pause_and_reject_columns(project_db: Path) -> None:
    created = project_store.create_project("Piece", db_path=project_db)
    run = insert_run(
        project_id=created.id,
        branch_id=created.active_branch_id or "",
        operation_run_id="op-pause",
        brief=_brief().model_dump(),
        plan=compile_project_plan(_brief()),
        db_path=project_db,
    )
    update_stage_status(
        run.id,
        "arrangement",
        "completed",
        revision_id="rev-arrange",
        set_revision_id=True,
        db_path=project_db,
    )
    set_pause_requested(run.id, True, status="running", db_path=project_db)
    mark_stage_rejected(
        run.id,
        "arrangement",
        rejected_revision_id="rev-arrange",
        failure_code="autonomous_stage_rejected",
        head_revision_id="rev-child",
        composition_fingerprint="fp-child",
        db_path=project_db,
    )
    loaded = get_run(run.id, db_path=project_db)
    assert loaded.status == "paused"
    assert loaded.checkpoint_id is None
    assert loaded.pause_requested is False
    assert loaded.head_revision_id == "rev-child"
    arrangement = next(stage for stage in loaded.stages if stage.stage_id == "arrangement")
    assert arrangement.status == "failed"
    assert arrangement.recoverable == 1
    assert arrangement.rejected_revision_id == "rev-arrange"
    assert arrangement.revision_id is None
    assert arrangement.decision == "rejected"


def test_legacy_0012_rows_upgrade(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import sqlite3

    from alembic import command

    from app.db.connection import _alembic_config

    db_path = tmp_path / "legacy.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    reset_database_initialization_cache()
    command.upgrade(_alembic_config(db_path), "20260926_0012")
    now = "2026-09-26T00:00:00Z"
    brief = '{"schema_version":"creative.brief.v1"}'
    plan = compile_project_plan(_brief()).model_dump_json()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO projects (
                id, name, composition_json, generation_provider, generation_model,
                generation_prompt_json, created_at, updated_at
            ) VALUES ('proj-legacy', 'Legacy', NULL, NULL, NULL, NULL, ?, ?)
            """,
            (now, now),
        )
        conn.execute(
            """
            INSERT INTO autonomous_runs (
                id, project_id, branch_id, operation_run_id, status,
                brief_json, plan_json, head_revision_id, composition_fingerprint,
                agent_operation_count, revision_pass_count, prompt_tokens,
                completion_tokens, provider_reported_cost_micros, active_runtime_ms,
                failure_code, budget_code, seed, include_rendering, created_at, updated_at
            ) VALUES (
                'run-legacy', 'proj-legacy', 'branch-1', 'op-legacy', 'pending',
                ?, ?, NULL, NULL,
                0, 0, NULL,
                NULL, NULL, 0,
                NULL, NULL, NULL, 0, ?, ?
            )
            """,
            (brief, plan, now, now),
        )
        conn.execute(
            """
            INSERT INTO autonomous_stages (
                run_id, stage_id, position, agent_id, status,
                depends_on_json, target_section_ids_json, completion_json,
                artifact_ids_json, recoverable
            ) VALUES (
                'run-legacy', 'plan', 0, 'creative-director', 'pending',
                '[]', '[]', '["project_plan_valid"]', '[]', 1
            )
            """
        )
    command.upgrade(_alembic_config(db_path), "head")
    loaded = get_run("run-legacy", db_path=db_path)
    assert loaded.autonomy_mode == "autonomous"
    assert loaded.checkpoint_id is None
    assert loaded.pause_requested is False
    assert loaded.stages[0].instruction is None
    assert loaded.stages[0].decision is None
    assert loaded.stages[0].rejected_revision_id is None
    assert loaded.stages[0].warning_code is None


def test_secret_rejection_logs_code_only(
    project_db: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    created = project_store.create_project("Piece", db_path=project_db)
    brief = _brief().model_dump()
    brief["password"] = "hunter2"
    caplog.set_level(logging.WARNING)
    with pytest.raises(AutonomousStoreError):
        insert_run(
            project_id=created.id,
            branch_id=created.active_branch_id or "",
            operation_run_id="op-log",
            brief=brief,
            plan=compile_project_plan(_brief()),
            db_path=project_db,
        )
    for record in caplog.records:
        assert "hunter2" not in record.getMessage()
        assert "cold sparse" not in record.getMessage()
