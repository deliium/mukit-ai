"""Baseline schema: projects + composition history + locality triggers.

Revision ID: 20260914_0001
Revises:
Create Date: 2026-09-14

Greenfield final schema (former 001 + 002). Existing DBs must be wiped before apply.
"""

from __future__ import annotations

import logging
from typing import Sequence, Union

from alembic import op

revision: str = "20260914_0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

_PROJECTS_DDL = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    composition_json TEXT,
    generation_provider TEXT,
    generation_model TEXT,
    generation_prompt_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    active_branch_id TEXT,
    current_revision_id TEXT
)
"""

_COMPOSITION_SNAPSHOTS_DDL = """
CREATE TABLE IF NOT EXISTS composition_snapshots (
    fingerprint TEXT PRIMARY KEY,
    encoding_profile TEXT NOT NULL,
    compression_profile TEXT NOT NULL,
    payload_zlib BLOB NOT NULL,
    uncompressed_byte_size INTEGER NOT NULL,
    compressed_byte_size INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    CHECK (uncompressed_byte_size >= 0),
    CHECK (compressed_byte_size >= 0)
)
"""

_PROJECT_REVISIONS_DDL = """
CREATE TABLE IF NOT EXISTS project_revisions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    parent_revision_id TEXT,
    snapshot_fingerprint TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    name TEXT,
    operation_type TEXT NOT NULL,
    ai_provider TEXT,
    ai_model TEXT,
    user_instruction TEXT,
    affected_ranges_json TEXT NOT NULL DEFAULT '[]',
    affected_track_ids_json TEXT NOT NULL DEFAULT '[]',
    summary_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (project_id, parent_revision_id)
        REFERENCES project_revisions(project_id, id),
    FOREIGN KEY (snapshot_fingerprint) REFERENCES composition_snapshots(fingerprint),
    UNIQUE (project_id, id),
    UNIQUE (project_id, sequence)
)
"""

_PROJECT_BRANCHES_DDL = """
CREATE TABLE IF NOT EXISTS project_branches (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    head_revision_id TEXT NOT NULL,
    created_from_revision_id TEXT,
    working_composition_json TEXT,
    working_fingerprint TEXT NOT NULL,
    working_version INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (project_id, head_revision_id)
        REFERENCES project_revisions(project_id, id),
    FOREIGN KEY (project_id, created_from_revision_id)
        REFERENCES project_revisions(project_id, id),
    UNIQUE (project_id, id),
    UNIQUE (project_id, normalized_name),
    CHECK (working_version >= 0)
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_projects_updated_at ON projects (updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_project_revisions_project_sequence "
    "ON project_revisions (project_id, sequence DESC)",
    "CREATE INDEX IF NOT EXISTS idx_project_revisions_parent "
    "ON project_revisions (project_id, parent_revision_id)",
    "CREATE INDEX IF NOT EXISTS idx_project_revisions_snapshot "
    "ON project_revisions (snapshot_fingerprint)",
    "CREATE INDEX IF NOT EXISTS idx_project_branches_project ON project_branches (project_id)",
    "CREATE INDEX IF NOT EXISTS idx_project_branches_head "
    "ON project_branches (project_id, head_revision_id)",
)

_TRIGGERS = (
    """
CREATE TRIGGER IF NOT EXISTS projects_active_branch_insert_check
BEFORE INSERT ON projects
WHEN NEW.active_branch_id IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'active_branch_id must reference a branch of this project')
    WHERE NOT EXISTS (
        SELECT 1 FROM project_branches
        WHERE id = NEW.active_branch_id AND project_id = NEW.id
    );
END
""",
    """
CREATE TRIGGER IF NOT EXISTS projects_active_branch_update_check
BEFORE UPDATE OF active_branch_id ON projects
WHEN NEW.active_branch_id IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'active_branch_id must reference a branch of this project')
    WHERE NOT EXISTS (
        SELECT 1 FROM project_branches
        WHERE id = NEW.active_branch_id AND project_id = NEW.id
    );
END
""",
    """
CREATE TRIGGER IF NOT EXISTS projects_current_revision_insert_check
BEFORE INSERT ON projects
WHEN NEW.current_revision_id IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'current_revision_id must reference a revision of this project')
    WHERE NOT EXISTS (
        SELECT 1 FROM project_revisions
        WHERE id = NEW.current_revision_id AND project_id = NEW.id
    );
END
""",
    """
CREATE TRIGGER IF NOT EXISTS projects_current_revision_update_check
BEFORE UPDATE OF current_revision_id ON projects
WHEN NEW.current_revision_id IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'current_revision_id must reference a revision of this project')
    WHERE NOT EXISTS (
        SELECT 1 FROM project_revisions
        WHERE id = NEW.current_revision_id AND project_id = NEW.id
    );
END
""",
)


def upgrade() -> None:
    stage = "tables"
    try:
        logger.info(
            "Applying Alembic revision",
            extra={"alembic_revision": revision, "migration_stage": stage},
        )
        logger.debug(
            "Creating core tables",
            extra={"alembic_revision": revision, "tables": ["projects", "composition_snapshots", "project_revisions", "project_branches"]},
        )
        op.execute(_PROJECTS_DDL)
        op.execute(_COMPOSITION_SNAPSHOTS_DDL)
        op.execute(_PROJECT_REVISIONS_DDL)
        op.execute(_PROJECT_BRANCHES_DDL)

        stage = "indexes"
        logger.debug(
            "Creating indexes",
            extra={"alembic_revision": revision, "index_count": len(_INDEXES)},
        )
        for ddl in _INDEXES:
            op.execute(ddl)

        stage = "triggers"
        logger.debug(
            "Creating locality triggers",
            extra={"alembic_revision": revision, "trigger_count": len(_TRIGGERS)},
        )
        for ddl in _TRIGGERS:
            op.execute(ddl)

        logger.info(
            "Alembic revision applied",
            extra={"alembic_revision": revision, "migration_stage": "complete"},
        )
    except Exception as exc:
        logger.error(
            "Alembic revision failed",
            extra={
                "alembic_revision": revision,
                "migration_stage": stage,
                "error_type": type(exc).__name__,
                "error_detail": str(exc)[:300],
            },
        )
        raise


def downgrade() -> None:
    # Greenfield baseline: drop in FK-safe order. Not used by app startup.
    op.execute("DROP TRIGGER IF EXISTS projects_current_revision_update_check")
    op.execute("DROP TRIGGER IF EXISTS projects_current_revision_insert_check")
    op.execute("DROP TRIGGER IF EXISTS projects_active_branch_update_check")
    op.execute("DROP TRIGGER IF EXISTS projects_active_branch_insert_check")
    op.execute("DROP TABLE IF EXISTS project_branches")
    op.execute("DROP TABLE IF EXISTS project_revisions")
    op.execute("DROP TABLE IF EXISTS composition_snapshots")
    op.execute("DROP TABLE IF EXISTS projects")
