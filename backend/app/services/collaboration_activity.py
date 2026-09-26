"""Append-only collaboration activity. Comment bodies are never stored here."""

from __future__ import annotations

import logging
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from app.collaboration_settings import collaboration_enabled
from app.services.collaboration_permissions import revision_origin

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ActivityRecord:
    id: str
    project_id: str
    kind: str
    actor_id: str | None
    revision_id: str | None
    comment_id: str | None
    review_id: str | None
    render_id: str | None
    decision: str | None
    ai_provider: str | None
    ai_model: str | None
    summary: str | None
    created_at: str


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _skip(kind: str, project_id: str | None) -> bool:
    if collaboration_enabled():
        return False
    logger.debug(
        "Skipped activity insert",
        extra={"kind": kind, "skipped": True, "project_id": project_id},
    )
    return True


def _insert(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    kind: str,
    actor_id: str | None = None,
    revision_id: str | None = None,
    comment_id: str | None = None,
    review_id: str | None = None,
    render_id: str | None = None,
    decision: str | None = None,
    ai_provider: str | None = None,
    ai_model: str | None = None,
    summary: str | None = None,
) -> str:
    activity_id = uuid.uuid4().hex
    now = _utc_now_iso()
    conn.execute(
        """
        INSERT INTO project_activity (
            id, project_id, kind, actor_id, revision_id, comment_id, review_id,
            render_id, decision, ai_provider, ai_model, summary, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            activity_id,
            project_id,
            kind,
            actor_id,
            revision_id,
            comment_id,
            review_id,
            render_id,
            decision,
            ai_provider,
            ai_model,
            summary,
            now,
        ),
    )
    logger.info(
        "Activity recorded",
        extra={
            "kind": kind,
            "project_id": project_id,
            "activity_id": activity_id,
            "revision_id": revision_id,
            "comment_id": comment_id,
            "review_id": review_id,
            "render_id": render_id,
        },
    )
    return activity_id


def record_revision_activity(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    revision_id: str | None,
    operation_type: str,
    actor_id: str | None,
    ai_provider: str | None,
    ai_model: str | None,
    revision_created: bool,
) -> None:
    origin = revision_origin(operation_type)
    kind = "ai_edit" if origin == "ai" else "user_edit" if origin == "human" else None
    if kind is None or not revision_created or not revision_id:
        return
    if _skip(kind, project_id):
        return
    _insert(
        conn,
        project_id=project_id,
        kind=kind,
        actor_id=actor_id,
        revision_id=revision_id,
        ai_provider=ai_provider if kind == "ai_edit" else None,
        ai_model=ai_model if kind == "ai_edit" else None,
    )


def record_comment_activity(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    comment_id: str,
    actor_id: str,
    target_kind: str,
) -> None:
    if _skip("comment", project_id):
        return
    _insert(
        conn,
        project_id=project_id,
        kind="comment",
        actor_id=actor_id,
        comment_id=comment_id,
        summary=f"comment on {target_kind}",
    )


def record_review_activity(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    review_id: str,
    revision_id: str,
    actor_id: str,
    decision: str,
) -> None:
    if _skip("approval", project_id):
        return
    _insert(
        conn,
        project_id=project_id,
        kind="approval",
        actor_id=actor_id,
        review_id=review_id,
        revision_id=revision_id,
        decision=decision,
    )


def record_render_activity(
    conn: sqlite3.Connection,
    *,
    project_id: str | None,
    render_id: str,
    previous_status: str,
    status: str,
    model_id: str | None,
) -> None:
    if status != "complete" or previous_status == "complete" or not project_id:
        return
    if _skip("render", project_id):
        return
    _insert(
        conn,
        project_id=project_id,
        kind="render",
        render_id=render_id,
        ai_model=model_id or None,
    )


def list_activity(
    project_id: str,
    *,
    limit: int = 50,
    db_path=None,
) -> list[ActivityRecord]:
    from pathlib import Path

    from app.db.connection import get_connection, get_project_db_path

    path = Path(db_path) if db_path is not None else get_project_db_path()
    bounded = max(1, min(limit, 100))
    with get_connection(path) as conn:
        rows = conn.execute(
            """
            SELECT id, project_id, kind, actor_id, revision_id, comment_id, review_id,
                   render_id, decision, ai_provider, ai_model, summary, created_at
            FROM project_activity
            WHERE project_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (project_id, bounded),
        ).fetchall()
    return [
        ActivityRecord(
            id=row["id"],
            project_id=row["project_id"],
            kind=row["kind"],
            actor_id=row["actor_id"],
            revision_id=row["revision_id"],
            comment_id=row["comment_id"],
            review_id=row["review_id"],
            render_id=row["render_id"],
            decision=row["decision"],
            ai_provider=row["ai_provider"],
            ai_model=row["ai_model"],
            summary=row["summary"],
            created_at=row["created_at"],
        )
        for row in rows
    ]
