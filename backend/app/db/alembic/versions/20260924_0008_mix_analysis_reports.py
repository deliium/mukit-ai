"""Add mix_analysis_reports table (JSON report bodies on filesystem).

Revision ID: 20260924_0008
Revises: 20260924_0007
Create Date: 2026-09-24

Durable mix.analysis.v1 metadata. Never stores PCM in SQLite and never
touches composition_snapshots / projects.composition_json.
"""

from __future__ import annotations

import logging
from typing import Sequence, Union

from alembic import op

revision: str = "20260924_0008"
down_revision: Union[str, Sequence[str], None] = "20260924_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

_REPORTS_DDL = """
CREATE TABLE IF NOT EXISTS mix_analysis_reports (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    stem_set_id TEXT NOT NULL,
    mix_render_id TEXT,
    dsp_backend TEXT NOT NULL,
    source_stem_set_fingerprint TEXT NOT NULL,
    source_composition_fingerprint TEXT,
    report_relpath TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    sha256_prefix TEXT NOT NULL,
    observation_count INTEGER NOT NULL DEFAULT 0,
    measurement_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    CHECK (dsp_backend IN ('stdlib', 'numpy_scipy', 'fake')),
    CHECK (byte_size >= 0),
    CHECK (observation_count >= 0),
    CHECK (measurement_count >= 0)
)
"""


def upgrade() -> None:
    logger.info("Creating mix_analysis_reports table")
    op.execute(_REPORTS_DDL)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_mix_analysis_reports_project_id "
        "ON mix_analysis_reports(project_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_mix_analysis_reports_stem_set_id "
        "ON mix_analysis_reports(stem_set_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_mix_analysis_reports_created_at "
        "ON mix_analysis_reports(created_at)"
    )
    logger.info("mix_analysis_reports migration complete")


def downgrade() -> None:
    logger.info("Dropping mix_analysis_reports table")
    op.execute("DROP INDEX IF EXISTS ix_mix_analysis_reports_created_at")
    op.execute("DROP INDEX IF EXISTS ix_mix_analysis_reports_stem_set_id")
    op.execute("DROP INDEX IF EXISTS ix_mix_analysis_reports_project_id")
    op.execute("DROP TABLE IF EXISTS mix_analysis_reports")
