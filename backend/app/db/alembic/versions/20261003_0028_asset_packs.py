"""Add asset_packs and asset_pack_slots for soundtrack pack orchestration.

Revision ID: 20261003_0028
Revises: 20261003_0027
Create Date: 2026-10-03

Stores non-playable asset.pack.v1 documents and slot→project status.
Canonical notes stay in projects.composition_json.
"""

from __future__ import annotations

import logging
from typing import Sequence, Union

from alembic import op

revision: str = "20261003_0028"
down_revision: Union[str, Sequence[str], None] = "20261003_0027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

_PACKS = "asset_packs"
_SLOTS = "asset_pack_slots"

_PACKS_DDL = """
CREATE TABLE IF NOT EXISTS asset_packs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    body_json TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    document_revision INTEGER NOT NULL DEFAULT 1 CHECK (document_revision >= 1),
    universe_id TEXT,
    composer_profile_id TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

_SLOTS_DDL = """
CREATE TABLE IF NOT EXISTS asset_pack_slots (
    pack_id TEXT NOT NULL REFERENCES asset_packs(id) ON DELETE CASCADE,
    slot_id TEXT NOT NULL,
    project_id TEXT,
    autonomous_run_id TEXT,
    adaptive_score_id TEXT,
    status TEXT NOT NULL,
    head_revision_id TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (pack_id, slot_id)
)
"""

_INDEXES = (
    (
        "idx_asset_packs_updated",
        "CREATE INDEX IF NOT EXISTS idx_asset_packs_updated "
        "ON asset_packs (updated_at DESC)",
    ),
    (
        "idx_asset_pack_slots_project",
        "CREATE INDEX IF NOT EXISTS idx_asset_pack_slots_project "
        "ON asset_pack_slots (project_id)",
    ),
)


def upgrade() -> None:
    logger.info(
        "Applying asset_packs migration",
        extra={"revision": revision, "table": _PACKS, "stage": "upgrade_start"},
    )
    op.execute(_PACKS_DDL)
    op.execute(_SLOTS_DDL)
    for _name, ddl in _INDEXES:
        op.execute(ddl)
    logger.info(
        "asset_packs tables ready",
        extra={"revision": revision, "tables": [_PACKS, _SLOTS], "stage": "upgrade_end"},
    )


def downgrade() -> None:
    logger.info(
        "Downgrading asset_packs migration",
        extra={"revision": revision, "stage": "downgrade_start"},
    )
    op.execute("DROP INDEX IF EXISTS idx_asset_pack_slots_project")
    op.execute("DROP INDEX IF EXISTS idx_asset_packs_updated")
    op.execute("DROP TABLE IF EXISTS asset_pack_slots")
    op.execute("DROP TABLE IF EXISTS asset_packs")
    logger.info(
        "asset_packs tables dropped",
        extra={"revision": revision, "stage": "downgrade_end"},
    )
