"""Add musical_universes and musical_universe_members.

Revision ID: 20261001_0020
Revises: 20260930_0019
Create Date: 2026-10-01

The universe document is not a score. Membership rows point at projects.
Deleting a project removes that membership and leaves the universe row.
"""

from __future__ import annotations

import logging
from typing import Sequence, Union

from alembic import op

revision: str = "20261001_0020"
down_revision: Union[str, Sequence[str], None] = "20260930_0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

_UNIVERSES = "musical_universes"
_MEMBERS = "musical_universe_members"

_UNIVERSES_DDL = """
CREATE TABLE IF NOT EXISTS musical_universes (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL UNIQUE,
    schema_version TEXT NOT NULL,
    body_json TEXT NOT NULL,
    document_revision INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

_MEMBERS_DDL = """
CREATE TABLE IF NOT EXISTS musical_universe_members (
    universe_id TEXT NOT NULL REFERENCES musical_universes(id) ON DELETE CASCADE,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (project_id)
)
"""

_MEMBERS_INDEX = """
CREATE INDEX IF NOT EXISTS ix_musical_universe_members_universe_id
ON musical_universe_members (universe_id)
"""


def upgrade() -> None:
    logger.info(
        "Applying musical universe migration",
        extra={"revision": revision, "stage": "upgrade_start"},
    )
    op.execute(_UNIVERSES_DDL)
    op.execute(_MEMBERS_DDL)
    op.execute(_MEMBERS_INDEX)
    logger.info(
        "musical universe tables ready",
        extra={
            "revision": revision,
            "universes": _UNIVERSES,
            "members": _MEMBERS,
            "stage": "upgrade_end",
        },
    )


def downgrade() -> None:
    logger.info(
        "Downgrading musical universe migration",
        extra={"revision": revision, "stage": "downgrade_start"},
    )
    op.execute("DROP INDEX IF EXISTS ix_musical_universe_members_universe_id")
    op.execute("DROP TABLE IF EXISTS musical_universe_members")
    op.execute("DROP TABLE IF EXISTS musical_universes")
    logger.info(
        "musical universe tables dropped",
        extra={"revision": revision, "stage": "downgrade_end"},
    )
