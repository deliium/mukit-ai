"""Open, approve, and reject a review of one immutable revision."""

from __future__ import annotations

import logging
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.collaboration_schemas import CollaborationError
from app.db.connection import get_connection, get_project_db_path
from app.services.collaboration_permissions import (
    review_allowed_for_operation,
    revision_origin,
)
from app.services.persistence_secret_guard import PersistenceSecretError, assert_no_secret_values
from app.services.project_store import ProjectNotFoundError, get_project

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReviewRecord:
    id: str
    project_id: str
    revision_id: str
    status: str
    origin: str
    opened_by_actor_id: str
    decided_by_actor_id: str | None
    created_at: str
    decided_at: str | None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _row(row: sqlite3.Row, origin: str) -> ReviewRecord:
    return ReviewRecord(
        id=row["id"],
        project_id=row["project_id"],
        revision_id=row["revision_id"],
        status=row["status"],
        origin=origin,
        opened_by_actor_id=row["opened_by_actor_id"],
        decided_by_actor_id=row["decided_by_actor_id"],
        created_at=row["created_at"],
        decided_at=row["decided_at"],
    )


def _revision_operation(conn: sqlite3.Connection, project_id: str, revision_id: str) -> str:
    row = conn.execute(
        """
        SELECT operation_type FROM project_revisions
        WHERE project_id = ? AND id = ?
        """,
        (project_id, revision_id),
    ).fetchone()
    if row is None:
        raise CollaborationError(
            "Revision is not on this project",
            code="collaboration_review_not_allowed",
            details={"revision_id": revision_id},
        )
    return str(row["operation_type"])


def _guard_note(note: str | None, *, project_id: str) -> str | None:
    if note is None:
        return None
    cleaned = note.strip()
    if not cleaned:
        return None
    if len(cleaned) > 500:
        raise CollaborationError(
            "Review note is too long",
            code="persistence_secret_rejected",
        )
    try:
        assert_no_secret_values(cleaned, field_name="review_note")
    except PersistenceSecretError as exc:
        logger.warning(
            "Rejected review note",
            extra={"code": "persistence_secret_rejected", "project_id": project_id},
        )
        raise CollaborationError(
            "Review note must not contain credentials",
            code="persistence_secret_rejected",
        ) from exc
    return cleaned


def open_review(
    project_id: str,
    revision_id: str,
    actor_id: str,
    *,
    db_path: Path | str | None = None,
) -> ReviewRecord:
    path = Path(db_path) if db_path is not None else get_project_db_path()
    get_project(project_id, db_path=path)
    now = _utc_now_iso()
    review_id = uuid.uuid4().hex
    with get_connection(path) as conn:
        operation = _revision_operation(conn, project_id, revision_id)
        if not review_allowed_for_operation(operation):
            raise CollaborationError(
                "This revision cannot be reviewed",
                code="collaboration_review_not_allowed",
                details={"revision_id": revision_id},
            )
        origin = revision_origin(operation)
        open_row = conn.execute(
            """
            SELECT id FROM project_revision_reviews
            WHERE project_id = ? AND revision_id = ? AND status = 'open'
            """,
            (project_id, revision_id),
        ).fetchone()
        if open_row is not None:
            logger.warning(
                "Review already open",
                extra={
                    "code": "collaboration_review_open",
                    "project_id": project_id,
                    "revision_id": revision_id,
                    "review_id": open_row["id"],
                },
            )
            raise CollaborationError(
                "An open review already exists",
                code="collaboration_review_open",
                details={"revision_id": revision_id, "review_id": open_row["id"]},
            )
        decided = conn.execute(
            """
            SELECT id FROM project_revision_reviews
            WHERE project_id = ? AND revision_id = ? AND status IN ('approved', 'rejected')
            """,
            (project_id, revision_id),
        ).fetchone()
        if decided is not None:
            logger.warning(
                "Review already decided",
                extra={
                    "code": "collaboration_review_decided",
                    "project_id": project_id,
                    "revision_id": revision_id,
                    "review_id": decided["id"],
                },
            )
            raise CollaborationError(
                "This revision already has a decision",
                code="collaboration_review_decided",
                details={"revision_id": revision_id, "review_id": decided["id"]},
            )
        conn.execute(
            """
            INSERT INTO project_revision_reviews (
                id, project_id, revision_id, status, opened_by_actor_id,
                decided_by_actor_id, note, created_at, decided_at
            ) VALUES (?, ?, ?, 'open', ?, NULL, NULL, ?, NULL)
            """,
            (review_id, project_id, revision_id, actor_id, now),
        )
    logger.info(
        "Review opened",
        extra={
            "review_id": review_id,
            "revision_id": revision_id,
            "project_id": project_id,
            "actor_id": actor_id,
            "origin": origin,
            "decision": "open",
        },
    )
    return ReviewRecord(
        id=review_id,
        project_id=project_id,
        revision_id=revision_id,
        status="open",
        origin=origin or "human",
        opened_by_actor_id=actor_id,
        decided_by_actor_id=None,
        created_at=now,
        decided_at=None,
    )


def _decide(
    project_id: str,
    review_id: str,
    actor_id: str,
    decision: str,
    note: str | None,
    *,
    db_path: Path | str,
) -> ReviewRecord:
    cleaned = _guard_note(note, project_id=project_id)
    now = _utc_now_iso()
    with get_connection(db_path) as conn:
        row = conn.execute(
            """
            SELECT * FROM project_revision_reviews
            WHERE project_id = ? AND id = ?
            """,
            (project_id, review_id),
        ).fetchone()
        if row is None:
            raise CollaborationError(
                "Review not found",
                code="collaboration_review_decided",
                details={"review_id": review_id},
            )
        if row["status"] != "open":
            logger.warning(
                "Review already decided",
                extra={
                    "code": "collaboration_review_decided",
                    "project_id": project_id,
                    "revision_id": row["revision_id"],
                    "review_id": review_id,
                },
            )
            raise CollaborationError(
                "This review is already decided",
                code="collaboration_review_decided",
                details={"review_id": review_id, "revision_id": row["revision_id"]},
            )
        operation = _revision_operation(conn, project_id, row["revision_id"])
        origin = revision_origin(operation) or "human"
        conn.execute(
            """
            UPDATE project_revision_reviews
            SET status = ?, decided_by_actor_id = ?, note = ?, decided_at = ?
            WHERE id = ? AND status = 'open'
            """,
            (decision, actor_id, cleaned, now, review_id),
        )
        if decision == "approved":
            conn.execute(
                "UPDATE projects SET accepted_revision_id = ? WHERE id = ?",
                (row["revision_id"], project_id),
            )
        from app.services.collaboration_activity import record_review_activity

        record_review_activity(
            conn,
            project_id=project_id,
            review_id=review_id,
            revision_id=row["revision_id"],
            actor_id=actor_id,
            decision=decision,
        )
    logger.info(
        "Review decided",
        extra={
            "review_id": review_id,
            "revision_id": row["revision_id"],
            "project_id": project_id,
            "actor_id": actor_id,
            "origin": origin,
            "decision": decision,
        },
    )
    return ReviewRecord(
        id=review_id,
        project_id=project_id,
        revision_id=row["revision_id"],
        status=decision,
        origin=origin,
        opened_by_actor_id=row["opened_by_actor_id"],
        decided_by_actor_id=actor_id,
        created_at=row["created_at"],
        decided_at=now,
    )


def approve_review(
    project_id: str,
    review_id: str,
    actor_id: str,
    *,
    note: str | None = None,
    db_path: Path | str | None = None,
) -> ReviewRecord:
    path = Path(db_path) if db_path is not None else get_project_db_path()
    return _decide(project_id, review_id, actor_id, "approved", note, db_path=path)


def reject_review(
    project_id: str,
    review_id: str,
    actor_id: str,
    *,
    note: str | None = None,
    db_path: Path | str | None = None,
) -> ReviewRecord:
    path = Path(db_path) if db_path is not None else get_project_db_path()
    return _decide(project_id, review_id, actor_id, "rejected", note, db_path=path)


def list_reviews(
    project_id: str,
    *,
    db_path: Path | str | None = None,
) -> list[ReviewRecord]:
    path = Path(db_path) if db_path is not None else get_project_db_path()
    try:
        get_project(project_id, db_path=path)
    except ProjectNotFoundError as exc:
        raise CollaborationError(
            "Project not found",
            code="collaboration_not_member",
        ) from exc
    with get_connection(path) as conn:
        rows = conn.execute(
            """
            SELECT r.*, v.operation_type
            FROM project_revision_reviews AS r
            JOIN project_revisions AS v ON v.id = r.revision_id
            WHERE r.project_id = ?
            ORDER BY r.created_at DESC, r.id DESC
            """,
            (project_id,),
        ).fetchall()
    return [
        _row(row, revision_origin(str(row["operation_type"])) or "human")
        for row in rows
    ]
