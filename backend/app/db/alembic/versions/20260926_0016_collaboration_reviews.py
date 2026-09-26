"""Revision reviews beside immutable history rows.

Revision ID: 20260926_0016
Revises: 20260926_0015
Create Date: 2026-09-26
"""

from __future__ import annotations

import logging

from alembic import op

revision = "20260926_0016"
down_revision = "20260926_0015"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

_REVIEWS = """
CREATE TABLE project_revision_reviews (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    status TEXT NOT NULL,
    opened_by_actor_id TEXT NOT NULL,
    decided_by_actor_id TEXT,
    note TEXT,
    created_at TEXT NOT NULL,
    decided_at TEXT,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (revision_id) REFERENCES project_revisions(id),
    FOREIGN KEY (opened_by_actor_id) REFERENCES collaboration_actors(id),
    CHECK (status IN ('open', 'approved', 'rejected'))
)
"""


def upgrade() -> None:
    op.execute(_REVIEWS)
    op.execute(
        "CREATE INDEX ix_project_revision_reviews_project_created "
        "ON project_revision_reviews (project_id, created_at DESC)"
    )
    logger.info("Project revision reviews table ready", extra={"revision": revision})


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_project_revision_reviews_project_created")
    op.execute("DROP TABLE IF EXISTS project_revision_reviews")
