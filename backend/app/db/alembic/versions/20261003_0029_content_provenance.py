"""Add content_provenance_records (+ optional credentials meta) for V5 lineage.

Revision ID: 20261003_0029
Revises: 20261003_0028
Create Date: 2026-10-03

Stores non-playable content.provenance.record.v1 nodes. Canonical notes stay
in projects.composition_json / history snapshots. Never stores events or PCM.
"""

from __future__ import annotations

import logging
from typing import Sequence, Union

from alembic import op

revision: str = "20261003_0029"
down_revision: Union[str, Sequence[str], None] = "20261003_0028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

_RECORDS = "content_provenance_records"
_CREDENTIALS = "content_provenance_credentials"

_RECORDS_DDL = """
CREATE TABLE IF NOT EXISTS content_provenance_records (
    record_id TEXT PRIMARY KEY,
    project_id TEXT,
    artifact_kind TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    artifact_fingerprint_prefix TEXT,
    operation TEXT NOT NULL,
    actor_kind TEXT NOT NULL,
    model_id TEXT,
    model_version TEXT,
    runtime TEXT,
    user_action TEXT,
    parent_record_ids_json TEXT NOT NULL,
    parent_artifacts_json TEXT NOT NULL,
    source_generation_provenance_json TEXT,
    trust_class TEXT NOT NULL DEFAULT 'mukit_internal',
    body_json TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""

_CREDENTIALS_DDL = """
CREATE TABLE IF NOT EXISTS content_provenance_credentials (
    project_id TEXT NOT NULL,
    artifact_kind TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    status TEXT NOT NULL,
    fake_mode INTEGER NOT NULL DEFAULT 0,
    attached INTEGER NOT NULL DEFAULT 0,
    reason_code TEXT,
    credential_path_prefix TEXT,
    body_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project_id, artifact_kind, artifact_id)
)
"""

_INDEXES = (
    (
        "idx_content_provenance_project_created",
        "CREATE INDEX IF NOT EXISTS idx_content_provenance_project_created "
        "ON content_provenance_records (project_id, created_at)",
    ),
    (
        "idx_content_provenance_artifact",
        "CREATE INDEX IF NOT EXISTS idx_content_provenance_artifact "
        "ON content_provenance_records (artifact_kind, artifact_id)",
    ),
    (
        "idx_content_provenance_upsert",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_content_provenance_upsert "
        "ON content_provenance_records (artifact_kind, artifact_id, operation)",
    ),
)


def upgrade() -> None:
    logger.info(
        "Applying content_provenance migration",
        extra={"revision": revision, "table": _RECORDS, "stage": "upgrade_start"},
    )
    op.execute(_RECORDS_DDL)
    op.execute(_CREDENTIALS_DDL)
    for _name, ddl in _INDEXES:
        op.execute(ddl)
    logger.info(
        "content_provenance tables ready",
        extra={
            "revision": revision,
            "tables": [_RECORDS, _CREDENTIALS],
            "stage": "upgrade_end",
        },
    )


def downgrade() -> None:
    logger.info(
        "Downgrading content_provenance migration",
        extra={"revision": revision, "stage": "downgrade_start"},
    )
    op.execute("DROP INDEX IF EXISTS idx_content_provenance_upsert")
    op.execute("DROP INDEX IF EXISTS idx_content_provenance_artifact")
    op.execute("DROP INDEX IF EXISTS idx_content_provenance_project_created")
    op.execute(f"DROP TABLE IF EXISTS {_CREDENTIALS}")
    op.execute(f"DROP TABLE IF EXISTS {_RECORDS}")
    logger.info(
        "content_provenance tables dropped",
        extra={"revision": revision, "stage": "downgrade_end"},
    )
