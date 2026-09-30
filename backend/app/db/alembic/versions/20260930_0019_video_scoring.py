"""Add video_assets and video_scoring tables.

Revision ID: 20260930_0019
Revises: 20260928_0018
Create Date: 2026-09-30

Picture bytes stay on disk. The composition row is not a media column.
"""

from __future__ import annotations

import logging
from typing import Sequence, Union

from alembic import op

revision: str = "20260930_0019"
down_revision: Union[str, Sequence[str], None] = "20260928_0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

_ASSETS = "video_assets"
_SCORING = "video_scoring"

_ASSETS_DDL = """
CREATE TABLE IF NOT EXISTS video_assets (
    asset_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL UNIQUE REFERENCES projects(id) ON DELETE CASCADE,
    container TEXT NOT NULL,
    content_type TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    sha256_prefix TEXT NOT NULL,
    duration_seconds REAL NOT NULL,
    frame_rate_numerator INTEGER NOT NULL,
    frame_rate_denominator INTEGER NOT NULL,
    has_audio INTEGER NOT NULL CHECK (has_audio IN (0, 1)),
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    relpath TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""

_SCORING_DDL = """
CREATE TABLE IF NOT EXISTS video_scoring (
    project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    body_json TEXT NOT NULL,
    document_revision INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""


def upgrade() -> None:
    logger.info(
        "Applying video scoring migration",
        extra={"revision": revision, "stage": "upgrade_start"},
    )
    op.execute(_ASSETS_DDL)
    op.execute(_SCORING_DDL)
    logger.info(
        "video scoring tables ready",
        extra={"revision": revision, "assets": _ASSETS, "scoring": _SCORING, "stage": "upgrade_end"},
    )


def downgrade() -> None:
    logger.info(
        "Downgrading video scoring migration",
        extra={"revision": revision, "stage": "downgrade_start"},
    )
    op.execute("DROP TABLE IF EXISTS video_scoring")
    op.execute("DROP TABLE IF EXISTS video_assets")
    logger.info(
        "video scoring tables dropped",
        extra={"revision": revision, "stage": "downgrade_end"},
    )
