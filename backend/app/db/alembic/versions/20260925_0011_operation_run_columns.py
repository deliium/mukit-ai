"""Add operation_run_id and attempt_count to neural render tables.

Revision ID: 20260925_0011
Revises: 20260925_0010
Create Date: 2026-09-25

"""

from __future__ import annotations

import logging

from alembic import op

revision = "20260925_0011"
down_revision = "20260925_0010"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

_STATEMENTS = (
    "ALTER TABLE neural_audio_renders ADD COLUMN operation_run_id TEXT",
    "ALTER TABLE neural_audio_renders ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE neural_audio_stem_sets ADD COLUMN operation_run_id TEXT",
    "ALTER TABLE neural_audio_stem_sets ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0",
)


def upgrade() -> None:
    logger.info(
        "Applying neural operation_run_id columns",
        extra={"revision": revision},
    )
    for statement in _STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    logger.info(
        "SQLite cannot drop operation_run_id columns in place",
        extra={"revision": revision},
    )
