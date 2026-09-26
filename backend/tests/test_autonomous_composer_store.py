"""Durable autonomous run and stage rows."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from app.db import initialize_database, reset_database_initialization_cache
from app.db.connection import get_connection
from app.services import project_store
from app.services.autonomous_composer_store import (
    AUTONOMOUS_RUN_LIMIT,
    PERSISTENCE_SECRET_REJECTED,
    AutonomousStoreError,
    insert_run,
    reconcile_interrupted_stages,
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
    assert revision["version_num"] == "20260926_0012"
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
