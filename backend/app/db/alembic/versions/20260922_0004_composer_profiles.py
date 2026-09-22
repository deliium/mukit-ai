"""Add composer_profiles table for durable preference documents.

Revision ID: 20260922_0004
Revises: 20260922_0003
Create Date: 2026-09-22

Stores ``composer.profile.v1`` bodies in PROJECT_DB_PATH. Never links to
DATASET_ROOT. normalized_name is the uniqueness key (NFKC + casefold).
"""

from __future__ import annotations

import logging
from typing import Sequence, Union

from alembic import op

revision: str = "20260922_0004"
down_revision: Union[str, Sequence[str], None] = "20260922_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

_COMPOSER_PROFILES_DDL = """
CREATE TABLE IF NOT EXISTS composer_profiles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL UNIQUE,
    schema_version TEXT NOT NULL,
    body_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_composer_profiles_updated "
    "ON composer_profiles (updated_at DESC)",
)


def upgrade() -> None:
    logger.info(
        "Applying composer_profiles migration",
        extra={"revision": revision, "down_revision": down_revision},
    )
    op.execute(_COMPOSER_PROFILES_DDL)
    for ddl in _INDEXES:
        op.execute(ddl)
    logger.info("composer_profiles table ready", extra={"revision": revision})


def downgrade() -> None:
    logger.info("Downgrading composer_profiles migration", extra={"revision": revision})
    op.execute("DROP INDEX IF EXISTS idx_composer_profiles_updated")
    op.execute("DROP TABLE IF EXISTS composer_profiles")
