"""Add musical_dependency_edges.

Revision ID: 20261002_0021
Revises: 20261001_0020
Create Date: 2026-10-02

Edges name derived assets. They do not store note events. There is no
foreign key to projects or universes, so an upstream delete can leave a
row visible as missing.
"""

from __future__ import annotations

import logging
from typing import Sequence, Union

from alembic import op

revision: str = "20261002_0021"
down_revision: Union[str, Sequence[str], None] = "20261001_0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

_TABLE = "musical_dependency_edges"

_DDL = """
CREATE TABLE IF NOT EXISTS musical_dependency_edges (
    id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    dependency_type TEXT NOT NULL,
    upstream_kind TEXT NOT NULL,
    downstream_kind TEXT NOT NULL,
    universe_id TEXT,
    upstream_project_id TEXT,
    downstream_project_id TEXT,
    upstream_theme_id TEXT,
    variant_id TEXT,
    motif_id TEXT,
    occurrence_id TEXT,
    source_motif_id TEXT,
    source_occurrence_id TEXT,
    upstream_revision_id TEXT,
    downstream_revision_id TEXT,
    downstream_asset_id TEXT,
    upstream_fingerprint TEXT NOT NULL,
    downstream_fingerprint TEXT,
    created_at TEXT NOT NULL,
    upstream_node_key TEXT NOT NULL,
    downstream_node_key TEXT NOT NULL,
    UNIQUE (dependency_type, upstream_node_key, downstream_node_key)
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_musical_dependency_edges_universe_id ON musical_dependency_edges (universe_id)",
    "CREATE INDEX IF NOT EXISTS ix_musical_dependency_edges_upstream_theme_id ON musical_dependency_edges (upstream_theme_id)",
    "CREATE INDEX IF NOT EXISTS ix_musical_dependency_edges_upstream_project_id ON musical_dependency_edges (upstream_project_id)",
    "CREATE INDEX IF NOT EXISTS ix_musical_dependency_edges_downstream_project_id ON musical_dependency_edges (downstream_project_id)",
    "CREATE INDEX IF NOT EXISTS ix_musical_dependency_edges_downstream_node_key ON musical_dependency_edges (downstream_node_key)",
)


def upgrade() -> None:
    logger.info(
        "Applying musical dependency migration",
        extra={"revision": revision, "stage": "upgrade_start"},
    )
    op.execute(_DDL)
    for statement in _INDEXES:
        op.execute(statement)
    logger.info(
        "musical dependency table ready",
        extra={"revision": revision, "table": _TABLE, "stage": "upgrade_end"},
    )


def downgrade() -> None:
    logger.info(
        "Downgrading musical dependency migration",
        extra={"revision": revision, "stage": "downgrade_start"},
    )
    op.execute("DROP INDEX IF EXISTS ix_musical_dependency_edges_downstream_node_key")
    op.execute("DROP INDEX IF EXISTS ix_musical_dependency_edges_downstream_project_id")
    op.execute("DROP INDEX IF EXISTS ix_musical_dependency_edges_upstream_project_id")
    op.execute("DROP INDEX IF EXISTS ix_musical_dependency_edges_upstream_theme_id")
    op.execute("DROP INDEX IF EXISTS ix_musical_dependency_edges_universe_id")
    op.execute("DROP TABLE IF EXISTS musical_dependency_edges")
    logger.info(
        "musical dependency table dropped",
        extra={"revision": revision, "stage": "downgrade_end"},
    )
