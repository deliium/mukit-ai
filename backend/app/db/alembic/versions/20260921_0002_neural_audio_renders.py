"""Add neural_audio_renders metadata table (audio files stay on filesystem).

Revision ID: 20260921_0002
Revises: 20260914_0001
Create Date: 2026-09-21

Neural audio is egress only: never stores PCM in SQLite and never touches
composition_snapshots.
"""

from __future__ import annotations

import logging
from typing import Sequence, Union

from alembic import op

revision: str = "20260921_0002"
down_revision: Union[str, Sequence[str], None] = "20260914_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

# Soft FK to project_revisions is enforced in application code; CASCADE on
# project_id removes rows when a project is deleted.
_NEURAL_AUDIO_RENDERS_DDL = """
CREATE TABLE IF NOT EXISTS neural_audio_renders (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    source_revision_id TEXT,
    source_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL,
    model_id TEXT NOT NULL,
    model_version TEXT,
    adapter_kind TEXT NOT NULL,
    fidelity_class TEXT NOT NULL,
    instructions TEXT NOT NULL DEFAULT '',
    genre TEXT,
    mood TEXT,
    instrumentation_summary TEXT,
    tempo_bpm REAL,
    seed INTEGER,
    error_code TEXT,
    error_message TEXT,
    audio_relpath TEXT,
    content_type TEXT,
    byte_size INTEGER,
    sha256_prefix TEXT,
    adapter_warnings_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    CHECK (status IN ('queued', 'running', 'failed', 'complete')),
    CHECK (adapter_kind IN ('midi_projection', 'melody_conditioning', 'text_prompt')),
    CHECK (fidelity_class IN ('deterministic', 'neural_instrument', 'generative')),
    CHECK (byte_size IS NULL OR byte_size >= 0)
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_neural_audio_renders_project_created "
    "ON neural_audio_renders (project_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_neural_audio_renders_status "
    "ON neural_audio_renders (status)",
    "CREATE INDEX IF NOT EXISTS idx_neural_audio_renders_revision "
    "ON neural_audio_renders (project_id, source_revision_id)",
)


def upgrade() -> None:
    logger.info(
        "Applying neural_audio_renders migration",
        extra={"revision": revision, "down_revision": down_revision},
    )
    op.execute(_NEURAL_AUDIO_RENDERS_DDL)
    for ddl in _INDEXES:
        op.execute(ddl)
    logger.info("neural_audio_renders table ready", extra={"revision": revision})


def downgrade() -> None:
    logger.info("Downgrading neural_audio_renders migration", extra={"revision": revision})
    op.execute("DROP INDEX IF EXISTS idx_neural_audio_renders_revision")
    op.execute("DROP INDEX IF EXISTS idx_neural_audio_renders_status")
    op.execute("DROP INDEX IF EXISTS idx_neural_audio_renders_project_created")
    op.execute("DROP TABLE IF EXISTS neural_audio_renders")
