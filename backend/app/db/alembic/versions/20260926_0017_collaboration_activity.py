"""Append-only collaboration activity rows.

Revision ID: 20260926_0017
Revises: 20260926_0016
Create Date: 2026-09-26
"""

from __future__ import annotations

import logging

from alembic import op

revision = "20260926_0017"
down_revision = "20260926_0016"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

_ACTIVITY = """
CREATE TABLE project_activity (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    actor_id TEXT,
    revision_id TEXT,
    comment_id TEXT,
    review_id TEXT,
    render_id TEXT,
    decision TEXT,
    ai_provider TEXT,
    ai_model TEXT,
    summary TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    CHECK (kind IN ('user_edit', 'ai_edit', 'approval', 'comment', 'render'))
)
"""


def upgrade() -> None:
    op.execute(_ACTIVITY)
    op.execute(
        "CREATE INDEX ix_project_activity_project_created "
        "ON project_activity (project_id, created_at DESC)"
    )
    logger.info("Project activity table ready", extra={"revision": revision})


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_project_activity_project_created")
    op.execute("DROP TABLE IF EXISTS project_activity")
