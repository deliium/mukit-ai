"""Add spatial_scenes table for project-scoped spatial.scene.v1 documents.

Revision ID: 20261003_0027
Revises: 20261003_0026
Create Date: 2026-10-03

Stores SpatialMix layouts only. Previews are ephemeral. Canonical notes stay
in projects.composition_json; stem WAV bytes stay in neural asset storage.
"""

from __future__ import annotations

import logging
from typing import Sequence, Union

from alembic import op

revision: str = "20261003_0027"
down_revision: Union[str, Sequence[str], None] = "20261003_0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

_TABLE = "spatial_scenes"

_DDL = """
CREATE TABLE IF NOT EXISTS spatial_scenes (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    body_json TEXT NOT NULL,
    document_revision INTEGER NOT NULL DEFAULT 1 CHECK (document_revision >= 1),
    source_composition_fingerprint TEXT NOT NULL,
    source_stem_set_id TEXT,
    source_stem_set_fingerprint TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

_INDEXES = (
    (
        "idx_spatial_scenes_project_updated",
        "CREATE INDEX IF NOT EXISTS idx_spatial_scenes_project_updated "
        "ON spatial_scenes (project_id, updated_at DESC)",
    ),
)


def upgrade() -> None:
    logger.info(
        "Applying spatial_scenes migration",
        extra={"revision": revision, "table": _TABLE, "stage": "upgrade_start"},
    )
    op.execute(_DDL)
    for _name, ddl in _INDEXES:
        op.execute(ddl)
    logger.info(
        "spatial_scenes table ready",
        extra={"revision": revision, "table": _TABLE, "stage": "upgrade_end"},
    )


def downgrade() -> None:
    logger.info(
        "Downgrading spatial_scenes migration",
        extra={"revision": revision, "table": _TABLE, "stage": "downgrade_start"},
    )
    op.execute("DROP INDEX IF EXISTS idx_spatial_scenes_project_updated")
    op.execute("DROP TABLE IF EXISTS spatial_scenes")
    logger.info(
        "spatial_scenes table dropped",
        extra={"revision": revision, "table": _TABLE, "stage": "downgrade_end"},
    )
