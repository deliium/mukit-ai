"""Add performance_plans table for project-scoped performance.plan.v1 documents.

Revision ID: 20261003_0026
Revises: 20261003_0025
Create Date: 2026-10-03

Stores conductor plans only. Realizations are ephemeral. Canonical notes stay
in projects.composition_json.
"""

from __future__ import annotations

import logging
from typing import Sequence, Union

from alembic import op

revision: str = "20261003_0026"
down_revision: Union[str, Sequence[str], None] = "20261003_0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

_TABLE = "performance_plans"

_DDL = """
CREATE TABLE IF NOT EXISTS performance_plans (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    preset_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    body_json TEXT NOT NULL,
    document_revision INTEGER NOT NULL DEFAULT 1 CHECK (document_revision >= 1),
    source_composition_fingerprint TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

_INDEXES = (
    (
        "idx_performance_plans_project_updated",
        "CREATE INDEX IF NOT EXISTS idx_performance_plans_project_updated "
        "ON performance_plans (project_id, updated_at DESC)",
    ),
)


def upgrade() -> None:
    logger.info(
        "Applying performance_plans migration",
        extra={"revision": revision, "table": _TABLE, "stage": "upgrade_start"},
    )
    op.execute(_DDL)
    for _name, ddl in _INDEXES:
        op.execute(ddl)
    logger.info(
        "performance_plans table ready",
        extra={"revision": revision, "table": _TABLE, "stage": "upgrade_end"},
    )


def downgrade() -> None:
    logger.info(
        "Downgrading performance_plans migration",
        extra={"revision": revision, "table": _TABLE, "stage": "downgrade_start"},
    )
    op.execute("DROP INDEX IF EXISTS idx_performance_plans_project_updated")
    op.execute("DROP TABLE IF EXISTS performance_plans")
    logger.info(
        "performance_plans table dropped",
        extra={"revision": revision, "table": _TABLE, "stage": "downgrade_end"},
    )
