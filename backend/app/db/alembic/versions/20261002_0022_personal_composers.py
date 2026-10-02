"""Add personal_composer_adapters.

Revision ID: 20261002_0022
Revises: 20261002_0021
Create Date: 2026-10-02

Rows name a user-trained adapter. They do not store note events and they
do not reference projects. Deleting a source project leaves the snapshot.
"""

from __future__ import annotations

import logging
from typing import Sequence, Union

from alembic import op

revision: str = "20261002_0022"
down_revision: Union[str, Sequence[str], None] = "20261002_0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

_TABLE = "personal_composer_adapters"

_DDL = """
CREATE TABLE IF NOT EXISTS personal_composer_adapters (
    id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    display_name TEXT NOT NULL,
    status TEXT NOT NULL,
    registry_model_id TEXT NOT NULL,
    engine TEXT NOT NULL,
    base_model_id TEXT NOT NULL,
    snapshot_version TEXT NOT NULL,
    step INTEGER NOT NULL,
    max_steps INTEGER NOT NULL,
    manifest_json TEXT NOT NULL,
    eval_json TEXT,
    owner_actor_id TEXT,
    error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

_INDEXES = (
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_personal_composer_display_name
    ON personal_composer_adapters (display_name)
    WHERE status != 'deleted'
    """,
    "CREATE INDEX IF NOT EXISTS ix_personal_composer_status ON personal_composer_adapters (status)",
    "CREATE INDEX IF NOT EXISTS ix_personal_composer_registry ON personal_composer_adapters (registry_model_id)",
)


def upgrade() -> None:
    logger.info(
        "Applying personal composer migration",
        extra={"revision": revision, "stage": "upgrade_start"},
    )
    op.execute(_DDL)
    for statement in _INDEXES:
        op.execute(statement)
    logger.info(
        "personal composer table ready",
        extra={"revision": revision, "table": _TABLE, "stage": "upgrade_end"},
    )


def downgrade() -> None:
    logger.info(
        "Reverting personal composer migration",
        extra={"revision": revision, "stage": "downgrade_start"},
    )
    op.execute("DROP INDEX IF EXISTS ix_personal_composer_registry")
    op.execute("DROP INDEX IF EXISTS ix_personal_composer_status")
    op.execute("DROP INDEX IF EXISTS uq_personal_composer_display_name")
    op.execute(f"DROP TABLE IF EXISTS {_TABLE}")
    logger.info(
        "personal composer table removed",
        extra={"revision": revision, "table": _TABLE, "stage": "downgrade_end"},
    )
