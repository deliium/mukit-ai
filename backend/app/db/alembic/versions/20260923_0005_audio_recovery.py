"""Add audio_recovery_jobs and audio_recovery_assets metadata tables.

Revision ID: 20260923_0005
Revises: 20260922_0004
Create Date: 2026-09-23

Filesystem holds WAV + result JSON under AUDIO_RECOVERY_ASSET_ROOT.
SQLite holds metadata only — never PCM, never composition_snapshots.
"""

from __future__ import annotations

import logging
from typing import Sequence, Union

from alembic import op

revision: str = "20260923_0005"
down_revision: Union[str, Sequence[str], None] = "20260922_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

_AUDIO_RECOVERY_JOBS_DDL = """
CREATE TABLE IF NOT EXISTS audio_recovery_jobs (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    status TEXT NOT NULL,
    engine_id TEXT NOT NULL,
    separation_engine_id TEXT,
    fake INTEGER NOT NULL DEFAULT 0,
    preview_fingerprint TEXT,
    preview_json TEXT,
    error_code TEXT,
    error_message TEXT,
    work_relpath TEXT,
    source_relpath TEXT,
    source_sha256_prefix TEXT,
    source_byte_size INTEGER,
    bound INTEGER NOT NULL DEFAULT 0,
    source_audio_asset_id TEXT,
    result_asset_id TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    CHECK (status IN ('queued', 'running', 'failed', 'complete')),
    CHECK (fake IN (0, 1)),
    CHECK (bound IN (0, 1)),
    CHECK (source_byte_size IS NULL OR source_byte_size >= 0)
)
"""

_AUDIO_RECOVERY_ASSETS_DDL = """
CREATE TABLE IF NOT EXISTS audio_recovery_assets (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    content_type TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    sha256_prefix TEXT NOT NULL,
    relpath TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (job_id) REFERENCES audio_recovery_jobs(id) ON DELETE CASCADE,
    CHECK (kind IN ('source_audio', 'result_json')),
    CHECK (byte_size >= 0)
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_audio_recovery_jobs_project_created "
    "ON audio_recovery_jobs (project_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_audio_recovery_jobs_status "
    "ON audio_recovery_jobs (status)",
    "CREATE INDEX IF NOT EXISTS idx_audio_recovery_assets_project_created "
    "ON audio_recovery_assets (project_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_audio_recovery_assets_job "
    "ON audio_recovery_assets (job_id)",
)


def upgrade() -> None:
    logger.info(
        "Applying audio_recovery tables migration",
        extra={"revision": revision, "down_revision": down_revision},
    )
    op.execute(_AUDIO_RECOVERY_JOBS_DDL)
    op.execute(_AUDIO_RECOVERY_ASSETS_DDL)
    for ddl in _INDEXES:
        op.execute(ddl)
    logger.info("audio_recovery tables ready", extra={"revision": revision})


def downgrade() -> None:
    logger.info("Downgrading audio_recovery tables migration", extra={"revision": revision})
    op.execute("DROP INDEX IF EXISTS idx_audio_recovery_assets_job")
    op.execute("DROP INDEX IF EXISTS idx_audio_recovery_assets_project_created")
    op.execute("DROP INDEX IF EXISTS idx_audio_recovery_jobs_status")
    op.execute("DROP INDEX IF EXISTS idx_audio_recovery_jobs_project_created")
    op.execute("DROP TABLE IF EXISTS audio_recovery_assets")
    op.execute("DROP TABLE IF EXISTS audio_recovery_jobs")
