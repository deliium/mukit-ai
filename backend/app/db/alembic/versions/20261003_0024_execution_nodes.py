"""Add execution_nodes for trusted LAN peer registration.

Revision ID: 20261003_0024
Revises: 20261002_0023
Create Date: 2026-10-03

Durable registration JSON and CAS revision. Live availability stays in
process memory. This migration inserts nothing.
"""

from __future__ import annotations

import logging
from typing import Sequence, Union

from alembic import op

revision: str = "20261003_0024"
down_revision: Union[str, Sequence[str], None] = "20261002_0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

_DDL = """
CREATE TABLE IF NOT EXISTS execution_nodes (
    node_id TEXT PRIMARY KEY,
    body_json TEXT NOT NULL,
    document_revision INTEGER NOT NULL CHECK (document_revision >= 1),
    updated_at TEXT NOT NULL
)
"""


def upgrade() -> None:
    logger.info("Creating execution_nodes table")
    op.execute(_DDL)
    logger.info("execution_nodes migration complete", extra={"revision": revision})


def downgrade() -> None:
    logger.info("Dropping execution_nodes table")
    op.execute("DROP TABLE IF EXISTS execution_nodes")
