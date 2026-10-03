"""Add model_lab_experiments for V5 Model Lab index.

Revision ID: 20261004_0031
Revises: 20261003_0030
Create Date: 2026-10-04

Stores non-playable model.lab.experiment.v1 index rows. Artifact files live
under MODEL_LAB_ROOT / music_transformer experiment FS. Never stores events
or PCM. No FK into projects. id equals the experiment FS directory name.
"""

from __future__ import annotations

import logging
from typing import Sequence, Union

from alembic import op

revision: str = "20261004_0031"
down_revision: Union[str, Sequence[str], None] = "20261003_0030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

_TABLE = "model_lab_experiments"

_DDL = """
CREATE TABLE IF NOT EXISTS model_lab_experiments (
    id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    display_name TEXT NOT NULL,
    status TEXT NOT NULL,
    dataset_version_id TEXT NOT NULL,
    tokenizer_version TEXT NOT NULL,
    tokenizer_vocab_hash_prefix TEXT NOT NULL,
    architecture_digest_prefix TEXT NOT NULL,
    train_digest_prefix TEXT NOT NULL,
    seed INTEGER NOT NULL,
    evaluation_version TEXT,
    registered_checkpoint_step INTEGER,
    registry_model_id TEXT,
    engine TEXT NOT NULL,
    runtime_json TEXT NOT NULL,
    owner_actor_id TEXT,
    error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

_INDEXES = (
    (
        "idx_model_lab_display_name_live",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_model_lab_display_name_live "
        "ON model_lab_experiments (display_name) "
        "WHERE status != 'deleted'",
    ),
    (
        "idx_model_lab_status",
        "CREATE INDEX IF NOT EXISTS idx_model_lab_status "
        "ON model_lab_experiments (status)",
    ),
    (
        "idx_model_lab_updated",
        "CREATE INDEX IF NOT EXISTS idx_model_lab_updated "
        "ON model_lab_experiments (updated_at)",
    ),
)


def upgrade() -> None:
    logger.info("Creating model_lab_experiments")
    op.execute(_DDL)
    for name, ddl in _INDEXES:
        logger.info("Creating index %s", name)
        op.execute(ddl)


def downgrade() -> None:
    for name, _ddl in reversed(_INDEXES):
        logger.info("Dropping index %s", name)
        op.execute(f"DROP INDEX IF EXISTS {name}")
    logger.info("Dropping model_lab_experiments")
    op.execute(f"DROP TABLE IF EXISTS {_TABLE}")
