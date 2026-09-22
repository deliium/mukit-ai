"""Add immutable agent_artifacts + revision_artifact_links tables.

Revision ID: 20260922_0003
Revises: 20260921_0002
Create Date: 2026-09-22

content_digest = sha256(canonical_json(payload)) with sort_keys=True,
separators=(',', ':'), UTF-8. Soft FK to project_revisions is enforced in
application code; CASCADE on project_id removes rows when a project is deleted.
"""

from __future__ import annotations

import logging
from typing import Sequence, Union

from alembic import op

revision: str = "20260922_0003"
down_revision: Union[str, Sequence[str], None] = "20260921_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

_AGENT_ARTIFACTS_DDL = """
CREATE TABLE IF NOT EXISTS agent_artifacts (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    content_type TEXT NOT NULL,
    kind TEXT NOT NULL,
    producer_agent_id TEXT NOT NULL,
    source_revision_id TEXT,
    source_fingerprint TEXT,
    payload_json TEXT NOT NULL,
    parent_ids_json TEXT NOT NULL DEFAULT '[]',
    depends_on_json TEXT NOT NULL DEFAULT '[]',
    provenance_json TEXT,
    supersedes_artifact_id TEXT,
    retention_class TEXT NOT NULL,
    expires_at TEXT,
    content_digest TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    CHECK (retention_class IN ('temporary', 'durable')),
    CHECK (
        (retention_class = 'temporary' AND expires_at IS NOT NULL)
        OR (retention_class = 'durable' AND expires_at IS NULL)
    )
)
"""

_REVISION_ARTIFACT_LINKS_DDL = """
CREATE TABLE IF NOT EXISTS revision_artifact_links (
    revision_id TEXT NOT NULL,
    role TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    PRIMARY KEY (revision_id, role),
    FOREIGN KEY (artifact_id) REFERENCES agent_artifacts(id) ON DELETE CASCADE,
    CHECK (role IN (
        'brief',
        'harmony_plan',
        'motif_plan',
        'arrangement_plan',
        'critique',
        'revision_plan'
    ))
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_agent_artifacts_project_created "
    "ON agent_artifacts (project_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_agent_artifacts_expires "
    "ON agent_artifacts (expires_at)",
    "CREATE INDEX IF NOT EXISTS idx_agent_artifacts_revision "
    "ON agent_artifacts (project_id, source_revision_id)",
    "CREATE INDEX IF NOT EXISTS idx_revision_artifact_links_artifact "
    "ON revision_artifact_links (artifact_id)",
)


def upgrade() -> None:
    logger.info(
        "Applying agent_artifacts migration",
        extra={"revision": revision, "down_revision": down_revision},
    )
    op.execute(_AGENT_ARTIFACTS_DDL)
    op.execute(_REVISION_ARTIFACT_LINKS_DDL)
    for ddl in _INDEXES:
        op.execute(ddl)
    logger.info("agent_artifacts tables ready", extra={"revision": revision})


def downgrade() -> None:
    logger.info("Downgrading agent_artifacts migration", extra={"revision": revision})
    op.execute("DROP INDEX IF EXISTS idx_revision_artifact_links_artifact")
    op.execute("DROP INDEX IF EXISTS idx_agent_artifacts_revision")
    op.execute("DROP INDEX IF EXISTS idx_agent_artifacts_expires")
    op.execute("DROP INDEX IF EXISTS idx_agent_artifacts_project_created")
    op.execute("DROP TABLE IF EXISTS revision_artifact_links")
    op.execute("DROP TABLE IF EXISTS agent_artifacts")
