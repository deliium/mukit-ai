"""Anchored comments beside the playable score. Bodies are never logged."""

from __future__ import annotations

import logging
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.collaboration_schemas import CollaborationCommentCreateRequest, CollaborationError
from app.db.connection import get_connection, get_project_db_path
from app.services.persistence_secret_guard import PersistenceSecretError, assert_no_secret_values
from app.services.project_composition import normalize_project_composition
from app.services.project_history import ProjectHistoryNotFoundError, get_revision_detail
from app.services.project_store import get_project

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CommentRecord:
    id: str
    project_id: str
    author_actor_id: str
    target_kind: str
    body: str
    revision_id: str | None
    section_id: str | None
    start_bar: int | None
    end_bar: int | None
    track_id: str | None
    render_id: str | None
    created_at: str


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _anchor_missing(details: dict) -> CollaborationError:
    logger.warning(
        "Comment anchor missing",
        extra={"code": "comment_anchor_missing", **details},
    )
    return CollaborationError(
        "Comment anchor does not match the score",
        code="comment_anchor_missing",
        details=details,
    )


def _score_for_anchor(project_id: str, revision_id: str | None, *, db_path: Path | str):
    if revision_id:
        try:
            detail = get_revision_detail(project_id, revision_id, db_path=db_path)
        except ProjectHistoryNotFoundError as exc:
            raise _anchor_missing({"revision_id": revision_id}) from exc
        composition = detail.composition
        if composition is None:
            raise _anchor_missing({"revision_id": revision_id})
        return composition
    record = get_project(project_id, db_path=db_path)
    if not record.composition_json:
        raise _anchor_missing({"project_id": project_id})
    return normalize_project_composition(
        record.composition_json,
        project_id=project_id,
        persist_canonical=False,
    ).composition


def _validate_anchor(
    project_id: str,
    request: CollaborationCommentCreateRequest,
    *,
    db_path: Path | str,
) -> None:
    kind = request.target_kind
    if kind == "project":
        return
    if kind == "revision":
        if not request.revision_id:
            raise _anchor_missing({"target_kind": kind})
        _score_for_anchor(project_id, request.revision_id, db_path=db_path)
        return
    if kind == "render":
        if not request.render_id:
            raise _anchor_missing({"target_kind": kind})
        with get_connection(db_path) as conn:
            row = conn.execute(
                "SELECT project_id FROM neural_audio_renders WHERE id = ?",
                (request.render_id,),
            ).fetchone()
        if row is None or row["project_id"] != project_id:
            raise _anchor_missing({"render_id": request.render_id})
        logger.debug(
            "Comment render anchor",
            extra={"render_id": request.render_id, "project_id": project_id},
        )
        return

    score = _score_for_anchor(project_id, request.revision_id, db_path=db_path)
    if kind == "section":
        if not request.section_id:
            raise _anchor_missing({"target_kind": kind})
        section_ids = {section.id for section in score.sections}
        if request.section_id not in section_ids:
            raise _anchor_missing({"section_id": request.section_id})
        logger.debug(
            "Comment section anchor",
            extra={"section_id": request.section_id, "project_id": project_id},
        )
        return
    if kind == "track":
        if not request.track_id:
            raise _anchor_missing({"target_kind": kind})
        track_ids = {track.id for track in score.tracks}
        if request.track_id not in track_ids:
            raise _anchor_missing({"track_id": request.track_id})
        logger.debug(
            "Comment track anchor",
            extra={"track_id": request.track_id, "project_id": project_id},
        )
        return
    if kind == "bar_range":
        if request.start_bar is None or request.end_bar is None:
            raise _anchor_missing({"target_kind": kind})
        if request.end_bar < request.start_bar or request.end_bar > score.bar_count:
            raise _anchor_missing(
                {"start_bar": request.start_bar, "end_bar": request.end_bar}
            )
        logger.debug(
            "Comment bar anchor",
            extra={
                "start_bar": request.start_bar,
                "end_bar": request.end_bar,
                "project_id": project_id,
            },
        )
        return
    raise _anchor_missing({"target_kind": kind})


def _row_comment(row: sqlite3.Row) -> CommentRecord:
    return CommentRecord(
        id=row["id"],
        project_id=row["project_id"],
        author_actor_id=row["author_actor_id"],
        target_kind=row["target_kind"],
        body=row["body"],
        revision_id=row["revision_id"],
        section_id=row["section_id"],
        start_bar=row["start_bar"],
        end_bar=row["end_bar"],
        track_id=row["track_id"],
        render_id=row["render_id"],
        created_at=row["created_at"],
    )


def create_comment(
    project_id: str,
    author_actor_id: str,
    request: CollaborationCommentCreateRequest,
    *,
    db_path: Path | str | None = None,
) -> CommentRecord:
    path = Path(db_path) if db_path is not None else get_project_db_path()
    try:
        assert_no_secret_values(request.body, field_name="comment_body")
    except PersistenceSecretError as exc:
        logger.warning(
            "Rejected comment body",
            extra={"code": "persistence_secret_rejected", "project_id": project_id},
        )
        raise CollaborationError(
            "Comment body must not contain credentials",
            code="persistence_secret_rejected",
        ) from exc
    _validate_anchor(project_id, request, db_path=path)
    comment_id = uuid.uuid4().hex
    now = _utc_now_iso()
    with get_connection(path) as conn:
        conn.execute(
            """
            INSERT INTO project_comments (
                id, project_id, author_actor_id, target_kind, revision_id,
                section_id, start_bar, end_bar, track_id, render_id, body, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                comment_id,
                project_id,
                author_actor_id,
                request.target_kind,
                request.revision_id,
                request.section_id,
                request.start_bar,
                request.end_bar,
                request.track_id,
                request.render_id,
                request.body,
                now,
            ),
        )
        from app.services.collaboration_activity import record_comment_activity

        record_comment_activity(
            conn,
            project_id=project_id,
            comment_id=comment_id,
            actor_id=author_actor_id,
            target_kind=request.target_kind,
        )
    logger.info(
        "Comment created",
        extra={
            "comment_id": comment_id,
            "project_id": project_id,
            "actor_id": author_actor_id,
            "target_kind": request.target_kind,
            "body_len": len(request.body),
        },
    )
    return CommentRecord(
        id=comment_id,
        project_id=project_id,
        author_actor_id=author_actor_id,
        target_kind=request.target_kind,
        body=request.body,
        revision_id=request.revision_id,
        section_id=request.section_id,
        start_bar=request.start_bar,
        end_bar=request.end_bar,
        track_id=request.track_id,
        render_id=request.render_id,
        created_at=now,
    )


def list_comments(
    project_id: str,
    *,
    limit: int = 50,
    db_path: Path | str | None = None,
) -> list[CommentRecord]:
    path = Path(db_path) if db_path is not None else get_project_db_path()
    bounded = max(1, min(limit, 100))
    with get_connection(path) as conn:
        rows = conn.execute(
            """
            SELECT id, project_id, author_actor_id, target_kind, body, revision_id,
                   section_id, start_bar, end_bar, track_id, render_id, created_at
            FROM project_comments
            WHERE project_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (project_id, bounded),
        ).fetchall()
    logger.debug(
        "Listed comments",
        extra={"project_id": project_id, "comment_count": len(rows)},
    )
    return [_row_comment(row) for row in rows]
