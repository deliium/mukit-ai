"""Add plugin_installations for explicit plugin lifecycle state.

Revision ID: 20260925_0010
Revises: 20260925_0009
Create Date: 2026-09-25

Desired state survives restart. Plugin directories stay read-only.
Config values are not written by this migration.
"""

from __future__ import annotations

import logging
from typing import Sequence, Union

from alembic import op

revision: str = "20260925_0010"
down_revision: Union[str, Sequence[str], None] = "20260925_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

_DDL = """
CREATE TABLE IF NOT EXISTS plugin_installations (
    plugin_id TEXT PRIMARY KEY,
    installed_version TEXT NOT NULL,
    desired_state TEXT NOT NULL CHECK (desired_state IN ('installed', 'enabled', 'disabled')),
    config_json TEXT NOT NULL DEFAULT '{}',
    last_error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""


def upgrade() -> None:
    logger.info("Creating plugin_installations table")
    op.execute(_DDL)
    logger.info("plugin_installations migration complete", extra={"revision": revision})


def downgrade() -> None:
    logger.info("Dropping plugin_installations table")
    op.execute("DROP TABLE IF EXISTS plugin_installations")
