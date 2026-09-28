"""Add adaptive_scores table for project-scoped adaptive.score.v1 documents.

Revision ID: 20260928_0018
Revises: 20260926_0017
Create Date: 2026-09-28

Stores graph documents only. Canonical notes stay in projects.composition_json.
"""

from __future__ import annotations

import logging
from typing import Sequence, Union

from alembic import op

revision: str = "20260928_0018"
down_revision: Union[str, Sequence[str], None] = "20260926_0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

_TABLE = "adaptive_scores"

_DDL = """
CREATE TABLE IF NOT EXISTS adaptive_scores (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    body_json TEXT NOT NULL,
    document_revision INTEGER NOT NULL DEFAULT 1,
    is_default INTEGER NOT NULL DEFAULT 0 CHECK (is_default IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (project_id, normalized_name)
)
"""

_INDEXES = (
    (
        "idx_adaptive_scores_project_updated",
        "CREATE INDEX IF NOT EXISTS idx_adaptive_scores_project_updated "
        "ON adaptive_scores (project_id, updated_at DESC)",
    ),
    (
        "idx_adaptive_scores_one_default",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_adaptive_scores_one_default "
        "ON adaptive_scores (project_id) WHERE is_default = 1",
    ),
)


def upgrade() -> None:
    logger.info(
        "Applying adaptive_scores migration",
        extra={"revision": revision, "table": _TABLE, "stage": "upgrade_start"},
    )
    op.execute(_DDL)
    for _name, ddl in _INDEXES:
        op.execute(ddl)
    logger.info(
        "adaptive_scores table ready",
        extra={"revision": revision, "table": _TABLE, "stage": "upgrade_end"},
    )


def downgrade() -> None:
    logger.info(
        "Downgrading adaptive_scores migration",
        extra={"revision": revision, "table": _TABLE, "stage": "downgrade_start"},
    )
    op.execute("DROP INDEX IF EXISTS idx_adaptive_scores_one_default")
    op.execute("DROP INDEX IF EXISTS idx_adaptive_scores_project_updated")
    op.execute("DROP TABLE IF EXISTS adaptive_scores")
    logger.info(
        "adaptive_scores table dropped",
        extra={"revision": revision, "table": _TABLE, "stage": "downgrade_end"},
    )
