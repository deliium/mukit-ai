"""Add rights_registry_entries for V5 rights-governance registry.

Revision ID: 20261003_0030
Revises: 20261003_0029
Create Date: 2026-10-03

Stores non-playable rights.registry.entry.v1 rows. Canonical notes stay in
projects.composition_json / history snapshots. Never stores events or PCM.
"""

from __future__ import annotations

import logging
from typing import Sequence, Union

from alembic import op

revision: str = "20261003_0030"
down_revision: Union[str, Sequence[str], None] = "20261003_0029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

_TABLE = "rights_registry_entries"

_DDL = """
CREATE TABLE IF NOT EXISTS rights_registry_entries (
    entry_id TEXT PRIMARY KEY,
    source_kind TEXT NOT NULL,
    source_id TEXT NOT NULL,
    ownership_class TEXT NOT NULL,
    use_policy TEXT NOT NULL,
    verification_status TEXT NOT NULL,
    rights_digest TEXT NOT NULL,
    entry_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    body_json TEXT NOT NULL
)
"""

_INDEXES = (
    (
        "idx_rights_registry_source_unique",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_rights_registry_source_unique "
        "ON rights_registry_entries (source_kind, source_id)",
    ),
    (
        "idx_rights_registry_updated",
        "CREATE INDEX IF NOT EXISTS idx_rights_registry_updated "
        "ON rights_registry_entries (updated_at)",
    ),
)


def upgrade() -> None:
    logger.info("Creating rights_registry_entries")
    op.execute(_DDL)
    for name, ddl in _INDEXES:
        logger.info("Creating index %s", name)
        op.execute(ddl)


def downgrade() -> None:
    for name, _ddl in reversed(_INDEXES):
        logger.info("Dropping index %s", name)
        op.execute(f"DROP INDEX IF EXISTS {name}")
    logger.info("Dropping rights_registry_entries")
    op.execute(f"DROP TABLE IF EXISTS {_TABLE}")
