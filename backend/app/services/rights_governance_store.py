"""SQLite rows for ``rights.registry.entry.v1``.

Does not import FastAPI or ``ai_agents``. A caller-owned connection is not
committed here. Unique key is ``(source_kind, source_id)`` with CAS via
``entry_version``.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.db.connection import get_connection, get_project_db_path
from app.rights_governance_schemas import (
    RIGHTS_GOVERNANCE_ERROR_CODES,
    RightsGovernanceError,
    RightsRegistryEntryV1,
    RightsSourceKind,
    new_rights_entry_id,
    parse_rights_registry_entry,
    reject_embedded_note_material,
)
from app.rights_governance_settings import load_rights_governance_settings
from app.services.persistence_secret_guard import (
    PersistenceSecretError,
    assert_no_secret_fields,
    assert_payload_has_no_secret_values,
)

logger = logging.getLogger(__name__)

_TABLE = "rights_registry_entries"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _refuse(code: str, *, http_status: int = 422, details: dict[str, Any] | None = None) -> RightsGovernanceError:
    logger.warning("Rights registry refused", extra={"code": code})
    return RightsGovernanceError(
        code,
        RIGHTS_GOVERNANCE_ERROR_CODES.get(code, RIGHTS_GOVERNANCE_ERROR_CODES["rights_invalid"]),
        http_status=http_status,
        details=details,
    )


def _validate_secrets(payload: dict[str, Any]) -> None:
    try:
        assert_no_secret_fields(payload, context="rights_registry_entry")
        assert_payload_has_no_secret_values(payload, context="rights_registry_entry")
    except PersistenceSecretError:
        logger.warning(
            "Rights registry refused",
            extra={"code": "forbidden_secret_field"},
        )
        raise


def _row_entry(row: sqlite3.Row) -> RightsRegistryEntryV1:
    body_raw = row["body_json"]
    body = json.loads(str(body_raw))
    if not isinstance(body, dict):
        raise _refuse("rights_invalid")
    return parse_rights_registry_entry(body)


def _project_id_for_source(source_kind: str, source_id: str) -> str | None:
    if source_kind in {"project", "personal_snapshot_project", "composition_revision"}:
        # composition_revision source_id may be "project_id:revision_id"
        if source_kind == "composition_revision" and ":" in source_id:
            return source_id.split(":", 1)[0]
        return source_id
    return None


def get_rights_entry(
    source_kind: RightsSourceKind | str,
    source_id: str,
    *,
    connection: sqlite3.Connection | None = None,
    db_path: Path | str | None = None,
) -> RightsRegistryEntryV1 | None:
    """Return the registry row for ``(source_kind, source_id)`` or None."""

    def _read(conn: sqlite3.Connection) -> RightsRegistryEntryV1 | None:
        row = conn.execute(
            f"""
            SELECT body_json FROM {_TABLE}
            WHERE source_kind = ? AND source_id = ?
            """,
            (source_kind, source_id),
        ).fetchone()
        if row is None:
            return None
        return _row_entry(row)

    if connection is not None:
        return _read(connection)
    path = Path(db_path) if db_path is not None else get_project_db_path()
    with get_connection(path) as conn:
        return _read(conn)


def list_entries_by_source_prefix(
    source_kind: RightsSourceKind | str,
    source_id_prefix: str,
    *,
    connection: sqlite3.Connection | None = None,
    db_path: Path | str | None = None,
    limit: int = 64,
) -> list[RightsRegistryEntryV1]:
    """List entries whose source_id starts with ``source_id_prefix``."""
    capped = max(1, min(int(limit), 256))

    def _list(conn: sqlite3.Connection) -> list[RightsRegistryEntryV1]:
        rows = conn.execute(
            f"""
            SELECT body_json FROM {_TABLE}
            WHERE source_kind = ? AND source_id LIKE ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (source_kind, f"{source_id_prefix}%", capped),
        ).fetchall()
        return [_row_entry(row) for row in rows]

    if connection is not None:
        return _list(connection)
    path = Path(db_path) if db_path is not None else get_project_db_path()
    with get_connection(path) as conn:
        return _list(conn)


def upsert_rights_entry(
    entry: RightsRegistryEntryV1 | dict[str, Any],
    *,
    expected_version: int | None = None,
    connection: sqlite3.Connection | None = None,
    db_path: Path | str | None = None,
) -> RightsRegistryEntryV1:
    """Upsert by ``(source_kind, source_id)``. CAS when ``expected_version`` set."""
    if isinstance(entry, dict):
        parsed = parse_rights_registry_entry(entry)
    else:
        parsed = entry

    body = parsed.model_dump(mode="json")
    reject_embedded_note_material(body, model="RightsRegistryEntryV1")
    _validate_secrets(body)

    settings = load_rights_governance_settings()
    if parsed.attribution and len(parsed.attribution) > settings.max_attribution_chars:
        raise _refuse(
            "rights_invalid",
            details={"field": "attribution", "max": settings.max_attribution_chars},
        )

    def _upsert(conn: sqlite3.Connection) -> RightsRegistryEntryV1:
        existing = conn.execute(
            f"""
            SELECT entry_id, entry_version, created_at, body_json FROM {_TABLE}
            WHERE source_kind = ? AND source_id = ?
            """,
            (parsed.source_kind, parsed.source_id),
        ).fetchone()

        now = _utc_now_iso()
        if existing is None:
            # Cap check for project-scoped sources.
            project_id = _project_id_for_source(parsed.source_kind, parsed.source_id)
            if project_id:
                count_row = conn.execute(
                    f"""
                    SELECT COUNT(*) AS n FROM {_TABLE}
                    WHERE source_kind IN ('project', 'personal_snapshot_project', 'composition_revision')
                      AND (
                        source_id = ?
                        OR source_id LIKE ?
                      )
                    """,
                    (project_id, f"{project_id}:%"),
                ).fetchone()
                if count_row is not None and int(count_row["n"]) >= settings.max_entries_per_project:
                    raise _refuse("rights_invalid", details={"code": "rights_project_cap"})

            entry_id = parsed.entry_id if parsed.entry_id.startswith("rights_") else new_rights_entry_id()
            stored = parsed.model_copy(
                update={
                    "entry_id": entry_id,
                    "entry_version": 1,
                    "created_at": now,
                    "updated_at": now,
                }
            )
            stored_body = stored.model_dump(mode="json")
            reject_embedded_note_material(stored_body)
            _validate_secrets(stored_body)
            # Recompute digest after id/timestamps may have changed identity fields only
            # (digest excludes entry_id/timestamps — still refresh via parse).
            stored = parse_rights_registry_entry(stored_body)
            stored_body = stored.model_dump(mode="json")
            conn.execute(
                f"""
                INSERT INTO {_TABLE} (
                    entry_id, source_kind, source_id, ownership_class, use_policy,
                    verification_status, rights_digest, entry_version,
                    created_at, updated_at, body_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stored.entry_id,
                    stored.source_kind,
                    stored.source_id,
                    stored.ownership_class,
                    stored.use_policy,
                    stored.verification_status,
                    stored.rights_digest,
                    stored.entry_version,
                    stored.created_at,
                    stored.updated_at,
                    json.dumps(stored_body, separators=(",", ":"), ensure_ascii=False),
                ),
            )
            logger.info(
                "Rights registry entry upserted",
                extra={
                    "entry_id": stored.entry_id,
                    "source_kind": stored.source_kind,
                    "ownership_class": stored.ownership_class,
                    "use_policy": stored.use_policy,
                    "op": "insert",
                },
            )
            return stored

        current_version = int(existing["entry_version"])
        if expected_version is not None and expected_version != current_version:
            raise _refuse(
                "rights_cas_conflict",
                http_status=409,
                details={
                    "expected_version": expected_version,
                    "current_version": current_version,
                },
            )

        stored = parsed.model_copy(
            update={
                "entry_id": str(existing["entry_id"]),
                "entry_version": current_version + 1,
                "created_at": str(existing["created_at"]),
                "updated_at": now,
            }
        )
        stored_body = stored.model_dump(mode="json")
        reject_embedded_note_material(stored_body)
        _validate_secrets(stored_body)
        stored = parse_rights_registry_entry(stored_body)
        stored_body = stored.model_dump(mode="json")
        conn.execute(
            f"""
            UPDATE {_TABLE}
            SET ownership_class = ?,
                use_policy = ?,
                verification_status = ?,
                rights_digest = ?,
                entry_version = ?,
                updated_at = ?,
                body_json = ?
            WHERE source_kind = ? AND source_id = ?
            """,
            (
                stored.ownership_class,
                stored.use_policy,
                stored.verification_status,
                stored.rights_digest,
                stored.entry_version,
                stored.updated_at,
                json.dumps(stored_body, separators=(",", ":"), ensure_ascii=False),
                stored.source_kind,
                stored.source_id,
            ),
        )
        logger.info(
            "Rights registry entry upserted",
            extra={
                "entry_id": stored.entry_id,
                "source_kind": stored.source_kind,
                "ownership_class": stored.ownership_class,
                "use_policy": stored.use_policy,
                "op": "update",
                "entry_version": stored.entry_version,
            },
        )
        return stored

    if connection is not None:
        return _upsert(connection)
    path = Path(db_path) if db_path is not None else get_project_db_path()
    with get_connection(path) as conn:
        return _upsert(conn)


def delete_entries_for_project(conn: sqlite3.Connection, project_id: str) -> int:
    """Delete registry rows for a project. Does not commit."""
    deleted = conn.execute(
        f"""
        DELETE FROM {_TABLE}
        WHERE (
            source_kind IN ('project', 'personal_snapshot_project')
            AND source_id = ?
        )
        OR (
            source_kind = 'composition_revision'
            AND (source_id = ? OR source_id LIKE ?)
        )
        """,
        (project_id, project_id, f"{project_id}:%"),
    )
    count = int(deleted.rowcount or 0)
    logger.info(
        "Rights registry entries deleted for project",
        extra={"project_id": project_id, "deleted_count": count},
    )
    return count
