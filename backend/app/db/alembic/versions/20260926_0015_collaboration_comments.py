"""Anchored project comments.

Revision ID: 20260926_0015
Revises: 20260926_0014
Create Date: 2026-09-26
"""

from __future__ import annotations

import logging

from alembic import op

revision = "20260926_0015"
down_revision = "20260926_0014"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

_COMMENTS = """
CREATE TABLE project_comments (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    author_actor_id TEXT NOT NULL,
    target_kind TEXT NOT NULL,
    revision_id TEXT,
    section_id TEXT,
    start_bar INTEGER,
    end_bar INTEGER,
    track_id TEXT,
    render_id TEXT,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (author_actor_id) REFERENCES collaboration_actors(id),
    CHECK (target_kind IN ('project', 'revision', 'section', 'bar_range', 'track', 'render'))
)
"""


def upgrade() -> None:
    op.execute(_COMMENTS)
    op.execute(
        "CREATE INDEX ix_project_comments_project_created "
        "ON project_comments (project_id, created_at DESC)"
    )
    logger.info("Project comments table ready", extra={"revision": revision})


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_project_comments_project_created")
    op.execute("DROP TABLE IF EXISTS project_comments")
