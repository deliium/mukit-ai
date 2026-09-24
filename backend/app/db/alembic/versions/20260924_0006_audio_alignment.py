"""Widen audio recovery asset kinds for alignment_json; add alignment_asset_id.

Revision ID: 20260924_0006
Revises: 20260923_0005
Create Date: 2026-09-24

Adds ``alignment_json`` to ``audio_recovery_assets.kind`` CHECK and
``audio_recovery_jobs.alignment_asset_id`` for Bind-time
``audio.alignment.v1`` sibling assets. Never stores PCM in SQLite.
"""

from __future__ import annotations

import logging
from typing import Sequence, Union

from alembic import op

revision: str = "20260924_0006"
down_revision: Union[str, Sequence[str], None] = "20260923_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

_ASSETS_NEW_DDL = """
CREATE TABLE audio_recovery_assets_new (
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
    CHECK (kind IN ('source_audio', 'result_json', 'alignment_json')),
    CHECK (byte_size >= 0)
)
"""


def upgrade() -> None:
    logger.info(
        "Applying audio recovery alignment_json migration",
        extra={"revision": revision, "down_revision": down_revision},
    )
    # SQLite cannot ALTER CHECK — recreate assets table with widened kind.
    op.execute(_ASSETS_NEW_DDL)
    op.execute(
        """
        INSERT INTO audio_recovery_assets_new (
            id, project_id, job_id, kind, content_type, byte_size,
            sha256_prefix, relpath, created_at
        )
        SELECT
            id, project_id, job_id, kind, content_type, byte_size,
            sha256_prefix, relpath, created_at
        FROM audio_recovery_assets
        """
    )
    op.execute("DROP TABLE audio_recovery_assets")
    op.execute("ALTER TABLE audio_recovery_assets_new RENAME TO audio_recovery_assets")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_audio_recovery_assets_project_created "
        "ON audio_recovery_assets (project_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_audio_recovery_assets_job "
        "ON audio_recovery_assets (job_id)"
    )
    op.execute(
        "ALTER TABLE audio_recovery_jobs ADD COLUMN alignment_asset_id TEXT"
    )
    logger.info(
        "audio recovery alignment_json kind + alignment_asset_id ready",
        extra={"revision": revision},
    )


def downgrade() -> None:
    logger.info(
        "Downgrading audio recovery alignment_json migration",
        extra={"revision": revision},
    )
    # Drop rows that would violate the old CHECK before narrowing.
    op.execute("DELETE FROM audio_recovery_assets WHERE kind = 'alignment_json'")
    op.execute(
        """
        CREATE TABLE audio_recovery_assets_old (
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
    )
    op.execute(
        """
        INSERT INTO audio_recovery_assets_old (
            id, project_id, job_id, kind, content_type, byte_size,
            sha256_prefix, relpath, created_at
        )
        SELECT
            id, project_id, job_id, kind, content_type, byte_size,
            sha256_prefix, relpath, created_at
        FROM audio_recovery_assets
        """
    )
    op.execute("DROP TABLE audio_recovery_assets")
    op.execute("ALTER TABLE audio_recovery_assets_old RENAME TO audio_recovery_assets")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_audio_recovery_assets_project_created "
        "ON audio_recovery_assets (project_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_audio_recovery_assets_job "
        "ON audio_recovery_assets (job_id)"
    )
    # SQLite cannot DROP COLUMN on older versions — leave alignment_asset_id
    # nullable orphan on downgrade (harmless).
    logger.warning(
        "Downgrade left audio_recovery_jobs.alignment_asset_id in place (SQLite)",
        extra={"revision": revision},
    )
