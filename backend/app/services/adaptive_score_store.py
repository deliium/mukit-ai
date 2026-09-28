"""SQLite CRUD for project-scoped ``adaptive.score.v1`` documents.

Does not load Composition and does not import project_store or project_history.
"""

from __future__ import annotations

import json
import logging
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.adaptive_score_schemas import (
    ADAPTIVE_SCORE_SCHEMA,
    AdaptiveScoreError,
    AdaptiveScoreSummaryV1,
    AdaptiveScoreV1,
    is_server_score_id,
    normalize_adaptive_score_name,
    parse_adaptive_score,
)
from app.adaptive_score_settings import load_adaptive_score_settings
from app.db.connection import get_connection, get_project_db_path
from app.services.adaptive_score_validation import reject_embedded_note_material
from app.services.persistence_secret_guard import (
    PersistenceSecretError,
    assert_no_secret_fields,
    assert_no_secret_values,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AdaptiveScoreRecord:
    id: str
    project_id: str
    name: str
    schema_version: str
    score: AdaptiveScoreV1
    document_revision: int
    is_default: bool
    created_at: str
    updated_at: str

    @property
    def state_count(self) -> int:
        return len(self.score.states)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _new_score_id() -> str:
    return f"ascore_{secrets.token_hex(8)}"


def _counts(score: AdaptiveScoreV1) -> dict[str, int]:
    return {
        "state_count": len(score.states),
        "variant_count": len(score.variants),
        "transition_count": len(score.transitions),
        "layer_count": len(score.layers),
        "stinger_count": len(score.stingers),
    }


def _guard_body(payload: dict[str, Any]) -> str:
    settings = load_adaptive_score_settings()
    try:
        assert_no_secret_fields(payload, context="adaptive_score")
        body_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        assert_no_secret_values(body_json, field_name="body_json")
        if isinstance(payload.get("name"), str):
            assert_no_secret_values(payload["name"], field_name="name")
    except PersistenceSecretError as exc:
        logger.warning(
            "Adaptive score secret guard rejected payload",
            extra={"code": "persistence_secret_rejected"},
        )
        raise AdaptiveScoreError(
            "persistence_secret_rejected",
            "Adaptive score payload contains a secret field or value",
            http_status=422,
            details={"cause": exc.code},
        ) from exc
    encoded = body_json.encode("utf-8")
    if len(encoded) > settings.max_body_bytes:
        logger.warning(
            "Adaptive score body exceeded byte cap",
            extra={"code": "adaptive_score_too_large", "byte_count": len(encoded)},
        )
        raise AdaptiveScoreError(
            "adaptive_score_too_large",
            "Adaptive score body exceeds the configured byte cap",
            http_status=422,
            details={"byte_count": len(encoded), "max_body_bytes": settings.max_body_bytes},
        )
    return body_json


def _prepare_score(score: AdaptiveScoreV1, *, project_id: str, score_id: str) -> tuple[AdaptiveScoreV1, str]:
    payload = score.model_dump(mode="json")
    reject_embedded_note_material(payload)
    payload["id"] = score_id
    payload["project_id"] = project_id
    parsed = parse_adaptive_score(payload)
    body_json = _guard_body(parsed.model_dump(mode="json"))
    return parsed, body_json


def _row_score(row: sqlite3.Row) -> AdaptiveScoreRecord:
    try:
        body = json.loads(row["body_json"])
        score = parse_adaptive_score(body if isinstance(body, dict) else {})
    except (AdaptiveScoreError, json.JSONDecodeError) as exc:
        logger.error(
            "Stored adaptive score body failed validation",
            extra={"score_id": row["id"], "code": "adaptive_score_invalid"},
        )
        raise AdaptiveScoreError(
            "adaptive_score_invalid",
            "Stored adaptive score body is invalid",
            http_status=500,
            details={"score_id": row["id"]},
        ) from exc
    return AdaptiveScoreRecord(
        id=row["id"],
        project_id=row["project_id"],
        name=row["name"],
        schema_version=row["schema_version"],
        score=score.model_copy(update={"id": row["id"], "project_id": row["project_id"], "name": row["name"]}),
        document_revision=int(row["document_revision"]),
        is_default=bool(row["is_default"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _state_count_from_json(body_json: str) -> int:
    try:
        body = json.loads(body_json)
    except json.JSONDecodeError:
        return 0
    states = body.get("states") if isinstance(body, dict) else None
    return len(states) if isinstance(states, list) else 0


def _map_integrity(exc: sqlite3.IntegrityError, *, score_id: str | None) -> AdaptiveScoreError:
    text = str(exc).lower()
    if "foreign key" in text:
        code = "project_not_found"
        status = 404
    elif "normalized_name" in text or "unique" in text:
        code = "adaptive_score_name_conflict"
        status = 409
    else:
        code = "adaptive_score_store_failed"
        status = 500
    logger.error(
        "Adaptive score SQLite constraint failed",
        extra={"code": code, "score_id": score_id},
    )
    return AdaptiveScoreError(
        code,
        "Adaptive score could not be stored",
        http_status=status,
        details={"score_id": score_id} if score_id else {},
    )


def create_score(
    project_id: str,
    score: AdaptiveScoreV1,
    *,
    is_default: bool = False,
    db_path: Path | None = None,
) -> AdaptiveScoreRecord:
    """Insert one score. The server assigns ``ascore_`` plus 16 hex characters."""
    path = db_path or get_project_db_path()
    if score.id:
        raise AdaptiveScoreError(
            "identity_mismatch",
            "Create does not accept a client score id",
            http_status=422,
        )
    if score.project_id not in (None, project_id):
        raise AdaptiveScoreError(
            "identity_mismatch",
            "project_id does not match the route",
            http_status=422,
            details={"project_id": project_id},
        )
    score_id = _new_score_id()
    prepared, body_json = _prepare_score(score, project_id=project_id, score_id=score_id)
    normalized = normalize_adaptive_score_name(prepared.name)
    now = _utc_now_iso()
    try:
        with get_connection(path) as conn:
            if is_default:
                conn.execute(
                    """
                    UPDATE adaptive_scores
                    SET is_default = 0
                    WHERE project_id = ? AND is_default = 1
                    """,
                    (project_id,),
                )
            conn.execute(
                """
                INSERT INTO adaptive_scores (
                    id, project_id, name, normalized_name, schema_version, body_json,
                    document_revision, is_default, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    score_id,
                    project_id,
                    prepared.name,
                    normalized,
                    ADAPTIVE_SCORE_SCHEMA,
                    body_json,
                    1 if is_default else 0,
                    now,
                    now,
                ),
            )
    except sqlite3.IntegrityError as exc:
        raise _map_integrity(exc, score_id=score_id) from exc
    except sqlite3.Error as exc:
        logger.error(
            "Adaptive score create failed",
            extra={"code": "adaptive_score_store_failed", "score_id": score_id},
        )
        raise AdaptiveScoreError(
            "adaptive_score_store_failed",
            "Adaptive score could not be stored",
            http_status=500,
        ) from exc
    logger.info(
        "Adaptive score created",
        extra={
            "project_id": project_id,
            "score_id": score_id,
            "document_revision": 1,
            "is_default": is_default,
            "schema_version": ADAPTIVE_SCORE_SCHEMA,
            **_counts(prepared),
        },
    )
    return AdaptiveScoreRecord(
        id=score_id,
        project_id=project_id,
        name=prepared.name,
        schema_version=ADAPTIVE_SCORE_SCHEMA,
        score=prepared,
        document_revision=1,
        is_default=is_default,
        created_at=now,
        updated_at=now,
    )


def get_score(
    project_id: str,
    score_id: str,
    *,
    db_path: Path | None = None,
) -> AdaptiveScoreRecord:
    path = db_path or get_project_db_path()
    with get_connection(path) as conn:
        row = conn.execute(
            """
            SELECT id, project_id, name, normalized_name, schema_version, body_json,
                   document_revision, is_default, created_at, updated_at
            FROM adaptive_scores
            WHERE project_id = ? AND id = ?
            """,
            (project_id, score_id),
        ).fetchone()
    if row is None:
        raise AdaptiveScoreError(
            "adaptive_score_not_found",
            "Adaptive score id was not found",
            http_status=404,
            details={"score_id": score_id},
        )
    record = _row_score(row)
    logger.info(
        "Adaptive score loaded",
        extra={
            "project_id": project_id,
            "score_id": score_id,
            "document_revision": record.document_revision,
            "is_default": record.is_default,
            **_counts(record.score),
        },
    )
    return record


def list_scores(
    project_id: str,
    *,
    db_path: Path | None = None,
) -> list[AdaptiveScoreSummaryV1]:
    path = db_path or get_project_db_path()
    with get_connection(path) as conn:
        rows = conn.execute(
            """
            SELECT id, name, schema_version, body_json, document_revision, is_default, updated_at
            FROM adaptive_scores
            WHERE project_id = ?
            ORDER BY updated_at DESC, id ASC
            """,
            (project_id,),
        ).fetchall()
    items = [
        AdaptiveScoreSummaryV1(
            id=row["id"],
            name=row["name"],
            schema_version=ADAPTIVE_SCORE_SCHEMA,
            is_default=bool(row["is_default"]),
            document_revision=int(row["document_revision"]),
            state_count=_state_count_from_json(row["body_json"]),
            updated_at=row["updated_at"],
        )
        for row in rows
    ]
    logger.info(
        "Adaptive scores listed",
        extra={"project_id": project_id, "score_count": len(items)},
    )
    return items


def replace_score(
    project_id: str,
    score_id: str,
    score: AdaptiveScoreV1,
    *,
    expected_document_revision: int,
    is_default: bool | None = None,
    db_path: Path | None = None,
) -> AdaptiveScoreRecord:
    """Replace the body when the CAS revision matches, then increment it."""
    path = db_path or get_project_db_path()
    if score.id != score_id or not is_server_score_id(score_id):
        raise AdaptiveScoreError(
            "identity_mismatch",
            "Score id does not match the route",
            http_status=422,
            details={"score_id": score_id},
        )
    if score.project_id != project_id:
        raise AdaptiveScoreError(
            "identity_mismatch",
            "project_id does not match the route",
            http_status=422,
            details={"project_id": project_id},
        )
    prepared, body_json = _prepare_score(score, project_id=project_id, score_id=score_id)
    normalized = normalize_adaptive_score_name(prepared.name)
    now = _utc_now_iso()
    try:
        with get_connection(path) as conn:
            current = conn.execute(
                """
                SELECT document_revision, is_default, normalized_name
                FROM adaptive_scores
                WHERE project_id = ? AND id = ?
                """,
                (project_id, score_id),
            ).fetchone()
            if current is None:
                raise AdaptiveScoreError(
                    "adaptive_score_not_found",
                    "Adaptive score id was not found",
                    http_status=404,
                    details={"score_id": score_id},
                )
            stored_revision = int(current["document_revision"])
            if stored_revision != expected_document_revision:
                logger.debug(
                    "Adaptive score CAS mismatch",
                    extra={
                        "score_id": score_id,
                        "expected_document_revision": expected_document_revision,
                        "stored_document_revision": stored_revision,
                    },
                )
                raise AdaptiveScoreError(
                    "adaptive_score_conflict",
                    "expected_document_revision does not match the stored score",
                    http_status=409,
                    details={
                        "expected_document_revision": expected_document_revision,
                        "document_revision": stored_revision,
                    },
                )
            next_default = bool(current["is_default"]) if is_default is None else is_default
            logger.debug(
                "Adaptive score name change flag",
                extra={
                    "score_id": score_id,
                    "normalized_name_changed": current["normalized_name"] != normalized,
                },
            )
            if next_default:
                conn.execute(
                    """
                    UPDATE adaptive_scores
                    SET is_default = 0
                    WHERE project_id = ? AND is_default = 1 AND id != ?
                    """,
                    (project_id, score_id),
                )
            cursor = conn.execute(
                """
                UPDATE adaptive_scores
                SET name = ?, normalized_name = ?, schema_version = ?, body_json = ?,
                    document_revision = document_revision + 1, is_default = ?, updated_at = ?
                WHERE project_id = ? AND id = ? AND document_revision = ?
                """,
                (
                    prepared.name,
                    normalized,
                    ADAPTIVE_SCORE_SCHEMA,
                    body_json,
                    1 if next_default else 0,
                    now,
                    project_id,
                    score_id,
                    expected_document_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise AdaptiveScoreError(
                    "adaptive_score_conflict",
                    "expected_document_revision does not match the stored score",
                    http_status=409,
                    details={"score_id": score_id},
                )
            created = conn.execute(
                "SELECT created_at FROM adaptive_scores WHERE project_id = ? AND id = ?",
                (project_id, score_id),
            ).fetchone()
    except AdaptiveScoreError:
        raise
    except sqlite3.IntegrityError as exc:
        raise _map_integrity(exc, score_id=score_id) from exc
    except sqlite3.Error as exc:
        logger.error(
            "Adaptive score replace failed",
            extra={"code": "adaptive_score_store_failed", "score_id": score_id},
        )
        raise AdaptiveScoreError(
            "adaptive_score_store_failed",
            "Adaptive score could not be stored",
            http_status=500,
        ) from exc
    revision = expected_document_revision + 1
    logger.info(
        "Adaptive score replaced",
        extra={
            "project_id": project_id,
            "score_id": score_id,
            "document_revision": revision,
            "is_default": next_default,
            "schema_version": ADAPTIVE_SCORE_SCHEMA,
            **_counts(prepared),
        },
    )
    return AdaptiveScoreRecord(
        id=score_id,
        project_id=project_id,
        name=prepared.name,
        schema_version=ADAPTIVE_SCORE_SCHEMA,
        score=prepared,
        document_revision=revision,
        is_default=next_default,
        created_at=created["created_at"] if created else now,
        updated_at=now,
    )


def delete_score(
    project_id: str,
    score_id: str,
    *,
    db_path: Path | None = None,
) -> None:
    path = db_path or get_project_db_path()
    with get_connection(path) as conn:
        cursor = conn.execute(
            "DELETE FROM adaptive_scores WHERE project_id = ? AND id = ?",
            (project_id, score_id),
        )
        if cursor.rowcount != 1:
            raise AdaptiveScoreError(
                "adaptive_score_not_found",
                "Adaptive score id was not found",
                http_status=404,
                details={"score_id": score_id},
            )
    logger.info(
        "Adaptive score deleted",
        extra={"project_id": project_id, "score_id": score_id},
    )
