"""Add scheduling_policy singleton for AI job scheduling.

Revision ID: 20261003_0025
Revises: 20261003_0024
Create Date: 2026-10-03

CAS policy document only. Seeds nothing. First GET materializes env defaults
without requiring a row.
"""

from __future__ import annotations

import logging
from typing import Sequence, Union

from alembic import op

revision: str = "20261003_0025"
down_revision: Union[str, Sequence[str], None] = "20261003_0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

_DDL = """
CREATE TABLE IF NOT EXISTS scheduling_policy (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    body_json TEXT NOT NULL,
    document_revision INTEGER NOT NULL CHECK (document_revision >= 1),
    updated_at TEXT NOT NULL
)
"""


def upgrade() -> None:
    logger.info("Creating scheduling_policy table")
    op.execute(_DDL)
    logger.info("scheduling_policy migration complete", extra={"revision": revision})


def downgrade() -> None:
    logger.info("Dropping scheduling_policy table")
    op.execute("DROP TABLE IF EXISTS scheduling_policy")
