"""Add autonomy mode, pause, checkpoint, and stage decision columns.

Revision ID: 20260926_0013
Revises: 20260926_0012
Create Date: 2026-09-26

SQLite cannot alter CHECK constraints in place. Copy into new tables, drop
the stage table first, then the run table, then rename. Do not toggle
foreign keys inside this migration: Alembic already holds a transaction.
"""

from __future__ import annotations

import logging

from alembic import op

revision = "20260926_0013"
down_revision = "20260926_0012"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

_RUNS_NEW = """
CREATE TABLE autonomous_runs_new (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    operation_run_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    brief_json TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    head_revision_id TEXT,
    composition_fingerprint TEXT,
    agent_operation_count INTEGER NOT NULL DEFAULT 0,
    revision_pass_count INTEGER NOT NULL DEFAULT 0,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    provider_reported_cost_micros INTEGER,
    active_runtime_ms INTEGER NOT NULL DEFAULT 0,
    failure_code TEXT,
    budget_code TEXT,
    seed INTEGER,
    include_rendering INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    autonomy_mode TEXT NOT NULL DEFAULT 'autonomous',
    pause_requested INTEGER NOT NULL DEFAULT 0,
    checkpoint_id TEXT,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    CHECK (status IN (
        'pending', 'running', 'completed', 'failed',
        'awaiting_approval', 'cancelled', 'paused'
    )),
    CHECK (autonomy_mode IN ('guided', 'balanced', 'autonomous'))
)
"""

_STAGES_NEW = """
CREATE TABLE autonomous_stages_new (
    run_id TEXT NOT NULL,
    stage_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    agent_id TEXT,
    status TEXT NOT NULL,
    depends_on_json TEXT NOT NULL,
    target_section_ids_json TEXT NOT NULL,
    completion_json TEXT NOT NULL,
    artifact_ids_json TEXT NOT NULL DEFAULT '[]',
    revision_id TEXT,
    failure_code TEXT,
    completion_code TEXT,
    recoverable INTEGER NOT NULL DEFAULT 1,
    started_at TEXT,
    finished_at TEXT,
    instruction TEXT,
    decision TEXT,
    rejected_revision_id TEXT,
    warning_code TEXT,
    PRIMARY KEY (run_id, stage_id),
    FOREIGN KEY (run_id) REFERENCES autonomous_runs(id) ON DELETE CASCADE,
    CHECK (status IN (
        'pending', 'running', 'completed', 'failed',
        'awaiting_approval', 'skipped'
    )),
    CHECK (decision IS NULL OR decision IN ('approved', 'rejected'))
)
"""

_RUN_COLUMNS = """
    id, project_id, branch_id, operation_run_id, status,
    brief_json, plan_json, head_revision_id, composition_fingerprint,
    agent_operation_count, revision_pass_count, prompt_tokens,
    completion_tokens, provider_reported_cost_micros, active_runtime_ms,
    failure_code, budget_code, seed, include_rendering, created_at, updated_at
"""

_STAGE_COLUMNS = """
    run_id, stage_id, position, agent_id, status,
    depends_on_json, target_section_ids_json, completion_json,
    artifact_ids_json, revision_id, failure_code, completion_code,
    recoverable, started_at, finished_at
"""


def upgrade() -> None:
    logger.info("Migrating autonomous control columns", extra={"revision": revision})
    op.execute(_RUNS_NEW)
    op.execute(_STAGES_NEW)
    op.execute(
        f"INSERT INTO autonomous_runs_new ({_RUN_COLUMNS}) "
        f"SELECT {_RUN_COLUMNS} FROM autonomous_runs"
    )
    op.execute(
        f"INSERT INTO autonomous_stages_new ({_STAGE_COLUMNS}) "
        f"SELECT {_STAGE_COLUMNS} FROM autonomous_stages"
    )
    op.execute("DROP TABLE autonomous_stages")
    op.execute("DROP TABLE autonomous_runs")
    op.execute("ALTER TABLE autonomous_runs_new RENAME TO autonomous_runs")
    op.execute("ALTER TABLE autonomous_stages_new RENAME TO autonomous_stages")
    logger.info("Autonomous control columns ready", extra={"revision": revision})


def downgrade() -> None:
    logger.info("Reverting autonomous control columns", extra={"revision": revision})
    op.execute(
        """
        CREATE TABLE autonomous_runs_old (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            branch_id TEXT NOT NULL,
            operation_run_id TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL,
            brief_json TEXT NOT NULL,
            plan_json TEXT NOT NULL,
            head_revision_id TEXT,
            composition_fingerprint TEXT,
            agent_operation_count INTEGER NOT NULL DEFAULT 0,
            revision_pass_count INTEGER NOT NULL DEFAULT 0,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            provider_reported_cost_micros INTEGER,
            active_runtime_ms INTEGER NOT NULL DEFAULT 0,
            failure_code TEXT,
            budget_code TEXT,
            seed INTEGER,
            include_rendering INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
            CHECK (status IN (
                'pending', 'running', 'completed', 'failed',
                'awaiting_approval', 'cancelled'
            ))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE autonomous_stages_old (
            run_id TEXT NOT NULL,
            stage_id TEXT NOT NULL,
            position INTEGER NOT NULL,
            agent_id TEXT,
            status TEXT NOT NULL,
            depends_on_json TEXT NOT NULL,
            target_section_ids_json TEXT NOT NULL,
            completion_json TEXT NOT NULL,
            artifact_ids_json TEXT NOT NULL DEFAULT '[]',
            revision_id TEXT,
            failure_code TEXT,
            completion_code TEXT,
            recoverable INTEGER NOT NULL DEFAULT 1,
            started_at TEXT,
            finished_at TEXT,
            PRIMARY KEY (run_id, stage_id),
            FOREIGN KEY (run_id) REFERENCES autonomous_runs(id) ON DELETE CASCADE,
            CHECK (status IN (
                'pending', 'running', 'completed', 'failed',
                'awaiting_approval', 'skipped'
            ))
        )
        """
    )
    op.execute(
        f"INSERT INTO autonomous_runs_old ({_RUN_COLUMNS}) "
        f"SELECT {_RUN_COLUMNS} FROM autonomous_runs"
    )
    op.execute(
        f"INSERT INTO autonomous_stages_old ({_STAGE_COLUMNS}) "
        f"SELECT {_STAGE_COLUMNS} FROM autonomous_stages"
    )
    op.execute("DROP TABLE autonomous_stages")
    op.execute("DROP TABLE autonomous_runs")
    op.execute("ALTER TABLE autonomous_runs_old RENAME TO autonomous_runs")
    op.execute("ALTER TABLE autonomous_stages_old RENAME TO autonomous_stages")
