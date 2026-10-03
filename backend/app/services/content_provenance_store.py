"""SQLite rows for ``content.provenance.record.v1``.

Does not import ``project_store``, FastAPI, or ``ai_agents``. A caller-owned
connection is not committed here. Cycle/cap refusals raise; soft-fail capture
lives in ``content_provenance_capture``.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from app.content_provenance_schemas import (
    PROVENANCE_RECORD_SCHEMA,
    CONTENT_PROVENANCE_ERROR_CODES,
    ContentProvenanceError,
    ContentProvenanceRecordV1,
    parse_content_provenance_record,
    reject_embedded_note_material,
)
from app.content_provenance_settings import load_content_provenance_settings
from app.db.connection import get_connection, get_project_db_path
from app.services.persistence_secret_guard import (
    PersistenceSecretError,
    assert_no_secret_fields,
    assert_payload_has_no_secret_values,
)

logger = logging.getLogger(__name__)

_TABLE = "content_provenance_records"
_COLUMNS = (
    "record_id",
    "project_id",
    "artifact_kind",
    "artifact_id",
    "artifact_fingerprint_prefix",
    "operation",
    "actor_kind",
    "model_id",
    "model_version",
    "runtime",
    "user_action",
    "parent_record_ids_json",
    "parent_artifacts_json",
    "source_generation_provenance_json",
    "trust_class",
    "body_json",
    "created_at",
)


def _refuse(code: str, *, http_status: int = 422, details: dict[str, Any] | None = None) -> ContentProvenanceError:
    logger.warning("Provenance record refused", extra={"code": code})
    return ContentProvenanceError(
        code,
        CONTENT_PROVENANCE_ERROR_CODES.get(code, CONTENT_PROVENANCE_ERROR_CODES["provenance_invalid"]),
        http_status=http_status,
        details=details,
    )


def _fingerprint_log_prefix(value: str | None) -> str | None:
    if not value:
        return None
    return value[:12]


def _row_record(row: sqlite3.Row) -> ContentProvenanceRecordV1:
    body_raw = row["body_json"]
    body = json.loads(str(body_raw))
    if not isinstance(body, dict):
        raise _refuse("provenance_invalid")
    return parse_content_provenance_record(body)


def _reject_cycle(conn: sqlite3.Connection, record_id: str, parent_ids: list[str]) -> None:
    """Raise when any parent already reaches ``record_id`` (or is self)."""
    if record_id in parent_ids:
        raise _refuse("provenance_cycle")
    if not parent_ids:
        return
    seen: set[str] = set()
    stack = list(parent_ids)
    while stack:
        current = stack.pop()
        if current == record_id:
            raise _refuse("provenance_cycle")
        if current in seen:
            continue
        seen.add(current)
        row = conn.execute(
            f"SELECT parent_record_ids_json FROM {_TABLE} WHERE record_id = ?",
            (current,),
        ).fetchone()
        if row is None:
            continue
        try:
            parents = json.loads(str(row["parent_record_ids_json"]))
        except json.JSONDecodeError:
            continue
        if not isinstance(parents, list):
            continue
        for parent in parents:
            if isinstance(parent, str) and parent:
                stack.append(parent)


def _reject_cap(conn: sqlite3.Connection, project_id: str | None) -> None:
    if not project_id:
        return
    settings = load_content_provenance_settings()
    row = conn.execute(
        f"SELECT COUNT(*) AS n FROM {_TABLE} WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    if row is not None and int(row["n"]) >= settings.max_records_per_project:
        raise _refuse("provenance_project_cap")


def _existing_upsert(
    conn: sqlite3.Connection,
    artifact_kind: str,
    artifact_id: str,
    operation: str,
) -> str | None:
    row = conn.execute(
        f"""
        SELECT record_id FROM {_TABLE}
        WHERE artifact_kind = ? AND artifact_id = ? AND operation = ?
        """,
        (artifact_kind, artifact_id, operation),
    ).fetchone()
    if row is None:
        return None
    return str(row["record_id"])


def _validate_secrets(payload: dict[str, Any]) -> None:
    try:
        assert_no_secret_fields(payload, context="content_provenance_record")
        assert_payload_has_no_secret_values(payload, context="content_provenance_record")
    except PersistenceSecretError:
        logger.warning(
            "Provenance record refused",
            extra={"code": "forbidden_secret_field"},
        )
        raise


def _insert(conn: sqlite3.Connection, record: ContentProvenanceRecordV1) -> ContentProvenanceRecordV1:
    body = record.model_dump(mode="json")
    reject_embedded_note_material(body)
    _validate_secrets(body)
    _reject_cycle(conn, record.record_id, list(record.parent_record_ids))
    existing = _existing_upsert(conn, record.artifact_kind, record.artifact_id, record.operation)
    if existing is not None:
        # Idempotent upsert: refresh body fields but keep original record_id.
        updated = record.model_copy(update={"record_id": existing})
        body = updated.model_dump(mode="json")
        reject_embedded_note_material(body)
        _validate_secrets(body)
        _reject_cycle(conn, existing, list(updated.parent_record_ids))
        conn.execute(
            f"""
            UPDATE {_TABLE}
            SET project_id = ?,
                artifact_fingerprint_prefix = ?,
                actor_kind = ?,
                model_id = ?,
                model_version = ?,
                runtime = ?,
                user_action = ?,
                parent_record_ids_json = ?,
                parent_artifacts_json = ?,
                source_generation_provenance_json = ?,
                trust_class = ?,
                body_json = ?,
                created_at = ?
            WHERE record_id = ?
            """,
            (
                updated.project_id,
                updated.artifact_fingerprint_prefix,
                updated.actor_kind,
                updated.model_id,
                updated.model_version,
                updated.runtime,
                updated.user_action,
                json.dumps(list(updated.parent_record_ids), separators=(",", ":")),
                json.dumps(
                    [item.model_dump(mode="json") for item in updated.parent_artifacts],
                    separators=(",", ":"),
                ),
                (
                    json.dumps(updated.source_generation_provenance, separators=(",", ":"))
                    if updated.source_generation_provenance is not None
                    else None
                ),
                updated.trust_class,
                json.dumps(body, separators=(",", ":"), ensure_ascii=False),
                updated.created_at,
                existing,
            ),
        )
        logger.info(
            "Provenance record upserted",
            extra={
                "record_id": existing,
                "operation": updated.operation,
                "artifact_kind": updated.artifact_kind,
                "artifact_fingerprint_prefix": _fingerprint_log_prefix(
                    updated.artifact_fingerprint_prefix
                ),
            },
        )
        return get_provenance_record(existing, connection=conn)

    _reject_cap(conn, record.project_id)
    conn.execute(
        f"""
        INSERT INTO {_TABLE} ({", ".join(_COLUMNS)})
        VALUES ({", ".join("?" for _ in _COLUMNS)})
        """,
        (
            record.record_id,
            record.project_id,
            record.artifact_kind,
            record.artifact_id,
            record.artifact_fingerprint_prefix,
            record.operation,
            record.actor_kind,
            record.model_id,
            record.model_version,
            record.runtime,
            record.user_action,
            json.dumps(list(record.parent_record_ids), separators=(",", ":")),
            json.dumps(
                [item.model_dump(mode="json") for item in record.parent_artifacts],
                separators=(",", ":"),
            ),
            (
                json.dumps(record.source_generation_provenance, separators=(",", ":"))
                if record.source_generation_provenance is not None
                else None
            ),
            record.trust_class,
            json.dumps(body, separators=(",", ":"), ensure_ascii=False),
            record.created_at,
        ),
    )
    logger.info(
        "Provenance record inserted",
        extra={
            "record_id": record.record_id,
            "operation": record.operation,
            "artifact_kind": record.artifact_kind,
            "artifact_fingerprint_prefix": _fingerprint_log_prefix(
                record.artifact_fingerprint_prefix
            ),
        },
    )
    return record


def insert_provenance_record(
    connection: sqlite3.Connection | None,
    record: ContentProvenanceRecordV1 | dict[str, Any],
    *,
    db_path: Path | str | None = None,
) -> ContentProvenanceRecordV1:
    """Insert or upsert by ``(artifact_kind, artifact_id, operation)``.

    Does not commit a caller-owned connection.
    """
    logger.debug(
        "Inserting provenance record",
        extra={"uses_caller_connection": connection is not None},
    )
    parsed = (
        record
        if isinstance(record, ContentProvenanceRecordV1)
        else parse_content_provenance_record(record)
    )
    if parsed.schema_version != PROVENANCE_RECORD_SCHEMA:
        raise _refuse("provenance_invalid")
    if connection is not None:
        return _insert(connection, parsed)
    path = Path(db_path) if db_path is not None else get_project_db_path()
    with get_connection(path) as conn:
        return _insert(conn, parsed)


def get_provenance_record(
    record_id: str,
    *,
    connection: sqlite3.Connection | None = None,
    db_path: Path | str | None = None,
) -> ContentProvenanceRecordV1:
    """Load one record or raise ``provenance_not_found``."""

    def _load(conn: sqlite3.Connection) -> ContentProvenanceRecordV1:
        row = conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM {_TABLE} WHERE record_id = ?",
            (record_id,),
        ).fetchone()
        if row is None:
            raise _refuse(
                "provenance_not_found",
                http_status=404,
                details={"record_id": record_id},
            )
        return _row_record(row)

    if connection is not None:
        return _load(connection)
    path = Path(db_path) if db_path is not None else get_project_db_path()
    with get_connection(path) as conn:
        return _load(conn)


def list_records_for_artifact(
    conn: sqlite3.Connection,
    *,
    artifact_kind: str,
    artifact_id: str,
    project_id: str | None = None,
) -> list[ContentProvenanceRecordV1]:
    """Records for one leaf artifact, oldest first."""
    if project_id:
        rows = conn.execute(
            f"""
            SELECT {", ".join(_COLUMNS)} FROM {_TABLE}
            WHERE artifact_kind = ? AND artifact_id = ? AND project_id = ?
            ORDER BY created_at ASC, record_id ASC
            """,
            (artifact_kind, artifact_id, project_id),
        ).fetchall()
    else:
        rows = conn.execute(
            f"""
            SELECT {", ".join(_COLUMNS)} FROM {_TABLE}
            WHERE artifact_kind = ? AND artifact_id = ?
            ORDER BY created_at ASC, record_id ASC
            """,
            (artifact_kind, artifact_id),
        ).fetchall()
    return [_row_record(row) for row in rows]


def walk_parent_records(
    conn: sqlite3.Connection,
    leaf_records: list[ContentProvenanceRecordV1],
    *,
    max_depth: int | None = None,
) -> list[ContentProvenanceRecordV1]:
    """BFS toward roots. Returns unique records leaf-first then parents."""
    settings = load_content_provenance_settings()
    depth_cap = max_depth if max_depth is not None else settings.chain_max_depth
    ordered: list[ContentProvenanceRecordV1] = []
    seen: set[str] = set()
    queue: list[tuple[ContentProvenanceRecordV1, int]] = [(record, 0) for record in leaf_records]
    while queue:
        current, depth = queue.pop(0)
        if current.record_id in seen:
            continue
        seen.add(current.record_id)
        ordered.append(current)
        if depth >= depth_cap:
            continue
        for parent_id in current.parent_record_ids:
            if parent_id in seen:
                continue
            try:
                parent = get_provenance_record(parent_id, connection=conn)
            except ContentProvenanceError:
                continue
            queue.append((parent, depth + 1))
    return ordered


def count_provenance_records(
    conn: sqlite3.Connection,
    *,
    project_id: str | None = None,
) -> int:
    if project_id:
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM {_TABLE} WHERE project_id = ?",
            (project_id,),
        ).fetchone()
    else:
        row = conn.execute(f"SELECT COUNT(*) AS n FROM {_TABLE}").fetchone()
    return int(row["n"]) if row is not None else 0


def delete_records_for_project(conn: sqlite3.Connection, project_id: str) -> int:
    """Delete provenance rows for a project. Does not commit."""
    deleted = conn.execute(
        f"DELETE FROM {_TABLE} WHERE project_id = ?",
        (project_id,),
    )
    conn.execute(
        "DELETE FROM content_provenance_credentials WHERE project_id = ?",
        (project_id,),
    )
    count = int(deleted.rowcount or 0)
    logger.info(
        "Provenance records deleted for project",
        extra={"project_id": project_id, "deleted_count": count},
    )
    return count


def set_record_trust_class(
    conn: sqlite3.Connection,
    record_id: str,
    trust_class: str,
) -> ContentProvenanceRecordV1:
    """Upgrade/downgrade trust class on an existing record. Does not commit."""
    record = get_provenance_record(record_id, connection=conn)
    updated = record.model_copy(update={"trust_class": trust_class})
    body = updated.model_dump(mode="json")
    reject_embedded_note_material(body)
    _validate_secrets(body)
    conn.execute(
        f"""
        UPDATE {_TABLE}
        SET trust_class = ?, body_json = ?
        WHERE record_id = ?
        """,
        (trust_class, json.dumps(body, separators=(",", ":"), ensure_ascii=False), record_id),
    )
    logger.info(
        "Provenance trust class updated",
        extra={"record_id": record_id, "trust_class": trust_class},
    )
    return get_provenance_record(record_id, connection=conn)
