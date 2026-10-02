"""Add preference learning tables.

Revision ID: 20261002_0023
Revises: 20261002_0022
Create Date: 2026-10-02

Rows store settings, one pending ballot per surface, choice documents, and
one linear ranker. They do not store note events and they have no foreign
keys. The migration inserts nothing.
"""

from __future__ import annotations

import logging
from typing import Sequence, Union

from alembic import op

revision: str = "20261002_0023"
down_revision: Union[str, Sequence[str], None] = "20261002_0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

_TABLES = (
    "preference_settings",
    "preference_pending",
    "preference_choices",
    "preference_ranker",
)

_DDL = (
    """
    CREATE TABLE IF NOT EXISTS preference_settings (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        body_json TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS preference_pending (
        surface TEXT PRIMARY KEY,
        body_json TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS preference_choices (
        id TEXT PRIMARY KEY,
        surface TEXT NOT NULL,
        created_at TEXT NOT NULL,
        body_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS preference_ranker (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        body_json TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
)


def upgrade() -> None:
    logger.info(
        "Applying preference learning migration",
        extra={"revision": revision, "stage": "upgrade_start"},
    )
    for statement in _DDL:
        op.execute(statement)
    logger.info(
        "preference learning tables ready",
        extra={"revision": revision, "tables": list(_TABLES), "stage": "upgrade_end"},
    )


def downgrade() -> None:
    logger.info(
        "Reverting preference learning migration",
        extra={"revision": revision, "stage": "downgrade_start"},
    )
    for table in reversed(_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")
    logger.info(
        "preference learning tables removed",
        extra={"revision": revision, "stage": "downgrade_end"},
    )
