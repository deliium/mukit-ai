"""Add local collaboration actors and project memberships.

Revision ID: 20260926_0014
Revises: 20260926_0013
Create Date: 2026-09-26

Additive only. Seeds actor ``local`` and one owner membership per existing
project. Comments, reviews, and activity stay in later revisions.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from alembic import op

revision = "20260926_0014"
down_revision = "20260926_0013"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

_ACTORS = """
CREATE TABLE collaboration_actors (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""

_MEMBERSHIPS = """
CREATE TABLE project_memberships (
    project_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    role TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (project_id, actor_id),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (actor_id) REFERENCES collaboration_actors(id),
    CHECK (role IN ('owner', 'editor', 'commenter', 'viewer'))
)
"""

_ONE_OWNER = """
CREATE UNIQUE INDEX ux_project_memberships_one_owner
ON project_memberships (project_id)
WHERE role = 'owner'
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def upgrade() -> None:
    now = _utc_now()
    op.execute(_ACTORS)
    op.execute(_MEMBERSHIPS)
    op.execute(_ONE_OWNER)
    op.execute("ALTER TABLE projects ADD COLUMN accepted_revision_id TEXT")
    op.execute("ALTER TABLE project_revisions ADD COLUMN actor_id TEXT")
    bind = op.get_bind()
    bind.exec_driver_sql(
        """
        INSERT INTO collaboration_actors (id, display_name, created_at)
        VALUES ('local', 'Local', ?)
        """,
        (now,),
    )
    bind.exec_driver_sql(
        """
        INSERT INTO project_memberships (project_id, actor_id, role, created_at)
        SELECT id, 'local', 'owner', ? FROM projects
        """,
        (now,),
    )
    count = bind.exec_driver_sql("SELECT COUNT(*) FROM project_memberships").scalar()
    logger.info(
        "Collaboration memberships ready",
        extra={"revision": revision, "membership_count": int(count or 0)},
    )


def downgrade() -> None:
    logger.info(
        "Downgrading collaboration memberships",
        extra={"revision": revision},
    )
    op.execute("DROP INDEX IF EXISTS ux_project_memberships_one_owner")
    op.execute("DROP TABLE IF EXISTS project_memberships")
    op.execute("DELETE FROM collaboration_actors WHERE id = 'local'")
    op.execute("DROP TABLE IF EXISTS collaboration_actors")
    logger.info(
        "SQLite cannot drop accepted_revision_id or actor_id columns in place",
        extra={"revision": revision},
    )
