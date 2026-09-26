"""SQLite store for collaboration actors and project memberships.

Does not import ``ai_agents``. Membership rows are written even when the
collaboration flag is off; route enforcement stays flag-gated.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.collaboration_schemas import CollaborationError
from app.db.connection import get_connection
from app.services.collaboration_permissions import GRANTABLE_ROLES
from app.services.persistence_secret_guard import (
    PersistenceSecretError,
    assert_no_secret_values,
)

logger = logging.getLogger(__name__)

LOCAL_ACTOR_ID = "local"
LOCAL_ACTOR_DISPLAY_NAME = "Local"
_DISPLAY_NAME_MAX = 80


@dataclass(frozen=True)
class ActorRecord:
    id: str
    display_name: str
    created_at: str


@dataclass(frozen=True)
class MembershipRecord:
    project_id: str
    actor_id: str
    role: str
    created_at: str


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _row_actor(row: sqlite3.Row) -> ActorRecord:
    return ActorRecord(
        id=row["id"],
        display_name=row["display_name"],
        created_at=row["created_at"],
    )


def _row_membership(row: sqlite3.Row) -> MembershipRecord:
    return MembershipRecord(
        project_id=row["project_id"],
        actor_id=row["actor_id"],
        role=row["role"],
        created_at=row["created_at"],
    )


def _run(conn: sqlite3.Connection | None, db_path: Path | str | None, fn):
    if conn is not None:
        return fn(conn)
    with get_connection(db_path) as connection:
        return fn(connection)


def get_actor(
    actor_id: str,
    *,
    conn: sqlite3.Connection | None = None,
    db_path: Path | str | None = None,
) -> ActorRecord | None:
    """Return one actor, or ``None`` when the id is unknown."""

    def _query(connection: sqlite3.Connection) -> ActorRecord | None:
        row = connection.execute(
            "SELECT id, display_name, created_at FROM collaboration_actors WHERE id = ?",
            (actor_id,),
        ).fetchone()
        if row is None:
            logger.debug("Collaboration actor missing", extra={"actor_id": actor_id})
            return None
        return _row_actor(row)

    return _run(conn, db_path, _query)


def list_actors(
    *,
    conn: sqlite3.Connection | None = None,
    db_path: Path | str | None = None,
) -> list[ActorRecord]:
    """Return actors ordered by creation time, then id."""

    def _query(connection: sqlite3.Connection) -> list[ActorRecord]:
        rows = connection.execute(
            """
            SELECT id, display_name, created_at
            FROM collaboration_actors
            ORDER BY created_at ASC, id ASC
            """
        ).fetchall()
        logger.debug("Listed collaboration actors", extra={"actor_count": len(rows)})
        return [_row_actor(row) for row in rows]

    return _run(conn, db_path, _query)


def create_actor(
    display_name: str,
    *,
    actor_id: str | None = None,
    conn: sqlite3.Connection | None = None,
    db_path: Path | str | None = None,
) -> ActorRecord:
    """Insert an actor. Display names are secret-guarded and never logged."""
    cleaned = display_name.strip()
    if not cleaned or len(cleaned) > _DISPLAY_NAME_MAX:
        raise CollaborationError(
            "display_name must be 1..80 characters",
            code="persistence_secret_rejected",
        )
    try:
        assert_no_secret_values(cleaned, field_name="display_name")
    except PersistenceSecretError as exc:
        logger.warning(
            "Rejected collaboration display name",
            extra={"code": "persistence_secret_rejected"},
        )
        raise CollaborationError(
            "display_name must not contain credentials",
            code="persistence_secret_rejected",
        ) from exc

    new_id = actor_id or uuid.uuid4().hex
    now = _utc_now_iso()

    def _insert(connection: sqlite3.Connection) -> ActorRecord:
        connection.execute(
            """
            INSERT INTO collaboration_actors (id, display_name, created_at)
            VALUES (?, ?, ?)
            """,
            (new_id, cleaned, now),
        )
        logger.info(
            "Collaboration actor created",
            extra={"actor_id": new_id, "display_name_len": len(cleaned)},
        )
        return ActorRecord(id=new_id, display_name=cleaned, created_at=now)

    return _run(conn, db_path, _insert)


def get_membership(
    project_id: str,
    actor_id: str,
    *,
    conn: sqlite3.Connection | None = None,
    db_path: Path | str | None = None,
) -> MembershipRecord | None:
    """Return the membership row, or ``None`` when the actor is not a member."""

    def _query(connection: sqlite3.Connection) -> MembershipRecord | None:
        row = connection.execute(
            """
            SELECT project_id, actor_id, role, created_at
            FROM project_memberships
            WHERE project_id = ? AND actor_id = ?
            """,
            (project_id, actor_id),
        ).fetchone()
        return None if row is None else _row_membership(row)

    return _run(conn, db_path, _query)


def list_members(
    project_id: str,
    *,
    conn: sqlite3.Connection | None = None,
    db_path: Path | str | None = None,
) -> list[MembershipRecord]:
    """Return memberships for one project, owner first, then creation time."""

    def _query(connection: sqlite3.Connection) -> list[MembershipRecord]:
        rows = connection.execute(
            """
            SELECT project_id, actor_id, role, created_at
            FROM project_memberships
            WHERE project_id = ?
            ORDER BY CASE role WHEN 'owner' THEN 0 ELSE 1 END, created_at ASC, actor_id ASC
            """,
            (project_id,),
        ).fetchall()
        logger.debug(
            "Listed project memberships",
            extra={"project_id": project_id, "member_count": len(rows)},
        )
        return [_row_membership(row) for row in rows]

    return _run(conn, db_path, _query)


def list_project_ids_for_actor(
    actor_id: str,
    *,
    conn: sqlite3.Connection | None = None,
    db_path: Path | str | None = None,
) -> list[str]:
    """Project ids the actor belongs to, oldest project first."""

    def _query(connection: sqlite3.Connection) -> list[str]:
        rows = connection.execute(
            """
            SELECT m.project_id
            FROM project_memberships AS m
            JOIN projects AS p ON p.id = m.project_id
            WHERE m.actor_id = ?
            ORDER BY p.created_at ASC, m.project_id ASC
            """,
            (actor_id,),
        ).fetchall()
        return [row["project_id"] for row in rows]

    return _run(conn, db_path, _query)


def ensure_owner(
    project_id: str,
    actor_id: str = LOCAL_ACTOR_ID,
    *,
    conn: sqlite3.Connection | None = None,
    db_path: Path | str | None = None,
) -> MembershipRecord:
    """Insert the single owner membership for a new project.

    A project that already has this actor as owner is unchanged. A different
    owner raises ``collaboration_owner_required``.
    """

    def _ensure(connection: sqlite3.Connection) -> MembershipRecord:
        actor = connection.execute(
            "SELECT id FROM collaboration_actors WHERE id = ?",
            (actor_id,),
        ).fetchone()
        if actor is None:
            raise CollaborationError(
                "Unknown collaboration actor",
                code="collaboration_actor_unknown",
                details={"actor_id": actor_id},
            )
        existing_owner = connection.execute(
            """
            SELECT project_id, actor_id, role, created_at
            FROM project_memberships
            WHERE project_id = ? AND role = 'owner'
            """,
            (project_id,),
        ).fetchone()
        if existing_owner is not None:
            if existing_owner["actor_id"] == actor_id:
                logger.debug(
                    "Project owner already present",
                    extra={"project_id": project_id, "actor_id": actor_id, "role": "owner"},
                )
                return _row_membership(existing_owner)
            raise CollaborationError(
                "Project already has an owner",
                code="collaboration_owner_required",
                details={"project_id": project_id},
            )
        now = _utc_now_iso()
        connection.execute(
            """
            INSERT INTO project_memberships (project_id, actor_id, role, created_at)
            VALUES (?, ?, 'owner', ?)
            """,
            (project_id, actor_id, now),
        )
        logger.info(
            "Project owner membership created",
            extra={"project_id": project_id, "actor_id": actor_id, "role": "owner"},
        )
        return MembershipRecord(
            project_id=project_id,
            actor_id=actor_id,
            role="owner",
            created_at=now,
        )

    return _run(conn, db_path, _ensure)


def grant_member(
    project_id: str,
    actor_id: str,
    role: str,
    *,
    conn: sqlite3.Connection | None = None,
    db_path: Path | str | None = None,
) -> MembershipRecord:
    """Grant a non-owner role. A second grant is ``collaboration_member_exists``."""
    if role not in GRANTABLE_ROLES:
        raise CollaborationError(
            "Sharing cannot grant owner",
            code="collaboration_owner_required",
            details={"role": role},
        )

    def _grant(connection: sqlite3.Connection) -> MembershipRecord:
        actor = connection.execute(
            "SELECT id FROM collaboration_actors WHERE id = ?",
            (actor_id,),
        ).fetchone()
        if actor is None:
            raise CollaborationError(
                "Unknown collaboration actor",
                code="collaboration_actor_unknown",
                details={"actor_id": actor_id},
            )
        existing = connection.execute(
            """
            SELECT role FROM project_memberships
            WHERE project_id = ? AND actor_id = ?
            """,
            (project_id, actor_id),
        ).fetchone()
        if existing is not None:
            raise CollaborationError(
                "Actor is already a member",
                code="collaboration_member_exists",
                details={"actor_id": actor_id, "role": existing["role"]},
            )
        now = _utc_now_iso()
        connection.execute(
            """
            INSERT INTO project_memberships (project_id, actor_id, role, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (project_id, actor_id, role, now),
        )
        logger.info(
            "Project membership granted",
            extra={"project_id": project_id, "actor_id": actor_id, "role": role},
        )
        return MembershipRecord(
            project_id=project_id,
            actor_id=actor_id,
            role=role,
            created_at=now,
        )

    return _run(conn, db_path, _grant)


def change_role(
    project_id: str,
    actor_id: str,
    role: str,
    *,
    conn: sqlite3.Connection | None = None,
    db_path: Path | str | None = None,
) -> MembershipRecord:
    """Change a non-owner membership among editor, commenter, and viewer."""
    if role not in GRANTABLE_ROLES:
        raise CollaborationError(
            "Owner role cannot be assigned",
            code="collaboration_owner_required",
            details={"role": role},
        )

    def _change(connection: sqlite3.Connection) -> MembershipRecord:
        row = connection.execute(
            """
            SELECT project_id, actor_id, role, created_at
            FROM project_memberships
            WHERE project_id = ? AND actor_id = ?
            """,
            (project_id, actor_id),
        ).fetchone()
        if row is None:
            raise CollaborationError(
                "Actor is not a member",
                code="collaboration_not_member",
                details={"actor_id": actor_id},
            )
        if row["role"] == "owner":
            raise CollaborationError(
                "Owner membership cannot be changed",
                code="collaboration_owner_required",
                details={"actor_id": actor_id},
            )
        connection.execute(
            """
            UPDATE project_memberships
            SET role = ?
            WHERE project_id = ? AND actor_id = ?
            """,
            (role, project_id, actor_id),
        )
        logger.info(
            "Project membership role changed",
            extra={"project_id": project_id, "actor_id": actor_id, "role": role},
        )
        return MembershipRecord(
            project_id=project_id,
            actor_id=actor_id,
            role=role,
            created_at=row["created_at"],
        )

    return _run(conn, db_path, _change)


def revoke_member(
    project_id: str,
    actor_id: str,
    *,
    conn: sqlite3.Connection | None = None,
    db_path: Path | str | None = None,
) -> None:
    """Remove a non-owner membership. The owner row cannot be deleted."""

    def _revoke(connection: sqlite3.Connection) -> None:
        row = connection.execute(
            """
            SELECT role FROM project_memberships
            WHERE project_id = ? AND actor_id = ?
            """,
            (project_id, actor_id),
        ).fetchone()
        if row is None:
            raise CollaborationError(
                "Actor is not a member",
                code="collaboration_not_member",
                details={"actor_id": actor_id},
            )
        if row["role"] == "owner":
            raise CollaborationError(
                "Owner membership cannot be revoked",
                code="collaboration_owner_required",
                details={"actor_id": actor_id},
            )
        connection.execute(
            """
            DELETE FROM project_memberships
            WHERE project_id = ? AND actor_id = ?
            """,
            (project_id, actor_id),
        )
        logger.info(
            "Project membership revoked",
            extra={"project_id": project_id, "actor_id": actor_id, "role": row["role"]},
        )

    _run(conn, db_path, _revoke)
