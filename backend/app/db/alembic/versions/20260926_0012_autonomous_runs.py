"""Add autonomous_runs and autonomous_stages.

Revision ID: 20260926_0012
Revises: 20260925_0011
Create Date: 2026-09-26

Stage status lives on the stage row. The project plan artifact does not
store it. Project delete cascades both tables.
"""

from __future__ import annotations

import logging

from alembic import op

revision = "20260926_0012"
down_revision = "20260925_0011"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

_RUNS = """
CREATE TABLE IF NOT EXISTS autonomous_runs (
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

_STAGES = """
CREATE TABLE IF NOT EXISTS autonomous_stages (
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


def upgrade() -> None:
    logger.info("Creating autonomous run tables", extra={"revision": revision})
    op.execute(_RUNS)
    op.execute(_STAGES)
    logger.info("Autonomous run tables ready", extra={"revision": revision})


def downgrade() -> None:
    logger.info("Dropping autonomous run tables", extra={"revision": revision})
    op.execute("DROP TABLE IF EXISTS autonomous_stages")
    op.execute("DROP TABLE IF EXISTS autonomous_runs")
