"""Add mix_plan_revisions table (plan JSON and mix WAV on filesystem).

Revision ID: 20260925_0009
Revises: 20260924_0008
Create Date: 2026-09-25

Durable applied mix revisions only. Previews stay on the filesystem.
Never stores PCM in SQLite and never touches composition snapshots.
"""

from __future__ import annotations

import logging
from typing import Sequence, Union

from alembic import op

revision: str = "20260925_0009"
down_revision: Union[str, Sequence[str], None] = "20260924_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

_DDL = """
CREATE TABLE IF NOT EXISTS mix_plan_revisions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    stem_set_id TEXT NOT NULL,
    parent_revision_id TEXT,
    status TEXT NOT NULL DEFAULT 'applied',
    master_target TEXT NOT NULL,
    plan_relpath TEXT NOT NULL,
    mix_relpath TEXT NOT NULL,
    source_stem_set_fingerprint TEXT NOT NULL,
    stem_sha256_pins_json TEXT NOT NULL,
    dsp_backend TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    sha256_prefix TEXT NOT NULL,
    created_at TEXT NOT NULL,
    is_head INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (parent_revision_id) REFERENCES mix_plan_revisions(id),
    CHECK (status = 'applied'),
    CHECK (master_target IN ('dynamic', 'streaming', 'cinematic', 'demo')),
    CHECK (dsp_backend IN ('stdlib', 'numpy_scipy', 'fake')),
    CHECK (byte_size >= 0),
    CHECK (is_head IN (0, 1))
)
"""


def upgrade() -> None:
    logger.info("Creating mix_plan_revisions table")
    op.execute(_DDL)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_mix_plan_revisions_project_stem_created "
        "ON mix_plan_revisions(project_id, stem_set_id, created_at)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_mix_plan_revisions_head "
        "ON mix_plan_revisions(project_id, stem_set_id) WHERE is_head = 1"
    )
    logger.info("mix_plan_revisions migration complete", extra={"revision": revision})


def downgrade() -> None:
    logger.info("Dropping mix_plan_revisions table")
    op.execute("DROP INDEX IF EXISTS ux_mix_plan_revisions_head")
    op.execute("DROP INDEX IF EXISTS ix_mix_plan_revisions_project_stem_created")
    op.execute("DROP TABLE IF EXISTS mix_plan_revisions")
