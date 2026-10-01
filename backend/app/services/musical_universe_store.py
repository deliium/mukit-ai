"""SQLite CRUD for ``musical.universe.v1``.

Does not load Composition and does not import project_store or composition_schemas.
Membership rows are stored beside the document, not inside body_json.
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

from app.db.connection import get_connection, get_project_db_path
from app.musical_universe_schemas import (
    MUSICAL_UNIVERSE_SCHEMA,
    MusicalUniverseError,
    MusicalUniverseSummaryV1,
    MusicalUniverseV1,
    normalize_universe_name,
    parse_musical_universe,
    reject_embedded_note_material,
)
from app.services.persistence_secret_guard import (
    PersistenceSecretError,
    assert_no_secret_fields,
    assert_no_secret_values,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MusicalUniverseRecord:
    id: str
    name: str
    schema_version: str
    universe: MusicalUniverseV1
    document_revision: int
    member_project_ids: list[str]
    created_at: str
    updated_at: str

    @property
    def entity_count(self) -> int:
        return len(self.universe.entities)

    @property
    def theme_count(self) -> int:
        return len(self.universe.themes)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _new_universe_id() -> str:
    return f"muniv_{secrets.token_hex(8)}"


def _guard_body(payload: dict[str, Any]) -> str:
    reject_embedded_note_material(payload)
    try:
        assert_no_secret_fields(payload, context="musical_universe")
        body_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        assert_no_secret_values(body_json, field_name="body_json")
        if isinstance(payload.get("name"), str):
            assert_no_secret_values(payload["name"], field_name="name")
    except PersistenceSecretError as exc:
        logger.warning(
            "Musical universe secret guard rejected payload",
            extra={"code": "persistence_secret_rejected"},
        )
        raise MusicalUniverseError(
            "persistence_secret_rejected",
            "Musical universe payload contains a secret field or value",
            http_status=422,
            details={"cause": exc.code},
        ) from exc
    logger.debug(
        "Musical universe body guarded",
        extra={"byte_count": len(body_json.encode("utf-8")), "schema_version": MUSICAL_UNIVERSE_SCHEMA},
    )
    return body_json


def _prepare_document(universe: MusicalUniverseV1) -> tuple[MusicalUniverseV1, str]:
    payload = universe.model_dump(mode="json")
    parsed = parse_musical_universe(payload)
    body_json = _guard_body(parsed.model_dump(mode="json"))
    return parsed, body_json


def _members(conn: sqlite3.Connection, universe_id: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT project_id
        FROM musical_universe_members
        WHERE universe_id = ?
        ORDER BY created_at ASC, project_id ASC
        """,
        (universe_id,),
    ).fetchall()
    return [str(row["project_id"]) for row in rows]


def _row_record(conn: sqlite3.Connection, row: sqlite3.Row) -> MusicalUniverseRecord:
    try:
        body = json.loads(row["body_json"])
        universe = parse_musical_universe(body if isinstance(body, dict) else {})
    except (MusicalUniverseError, json.JSONDecodeError) as exc:
        logger.error(
            "Stored musical universe body failed validation",
            extra={"universe_id": row["id"], "code": "musical_universe_invalid"},
        )
        raise MusicalUniverseError(
            "musical_universe_invalid",
            "Stored musical universe body is invalid",
            http_status=500,
            details={"universe_id": row["id"]},
        ) from exc
    members = _members(conn, str(row["id"]))
    return MusicalUniverseRecord(
        id=str(row["id"]),
        name=str(row["name"]),
        schema_version=str(row["schema_version"]),
        universe=universe.model_copy(update={"id": row["id"], "name": row["name"]}),
        document_revision=int(row["document_revision"]),
        member_project_ids=members,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _load(conn: sqlite3.Connection, universe_id: str) -> MusicalUniverseRecord:
    row = conn.execute(
        """
        SELECT id, name, normalized_name, schema_version, body_json,
               document_revision, created_at, updated_at
        FROM musical_universes
        WHERE id = ?
        """,
        (universe_id,),
    ).fetchone()
    if row is None:
        logger.debug(
            "Musical universe missing",
            extra={"universe_id": universe_id, "code": "musical_universe_not_found"},
        )
        raise MusicalUniverseError(
            "musical_universe_not_found",
            "Musical universe id was not found",
            http_status=404,
            details={"universe_id": universe_id},
        )
    return _row_record(conn, row)


def _map_integrity(exc: sqlite3.IntegrityError) -> MusicalUniverseError:
    text = str(exc).lower()
    if "musical_universe_members.project_id" in text or "primary key" in text:
        code = "universe_project_busy"
        status = 409
    elif "foreign key" in text and "projects" in text:
        code = "project_not_found"
        status = 404
    elif "foreign key" in text:
        code = "musical_universe_not_found"
        status = 404
    elif "normalized_name" in text or "unique" in text:
        code = "musical_universe_name_conflict"
        status = 409
    else:
        code = "musical_universe_store_failed"
        status = 500
    logger.error(
        "Musical universe SQLite constraint failed",
        extra={"code": code},
    )
    return MusicalUniverseError(
        code,
        "Musical universe could not be stored",
        http_status=status,
    )


def _log_write(action: str, record: MusicalUniverseRecord) -> None:
    logger.info(
        action,
        extra={
            "universe_id": record.id,
            "document_revision": record.document_revision,
            "entity_count": record.entity_count,
            "theme_count": record.theme_count,
        },
    )


def create_universe(
    name: str,
    project_id: str,
    *,
    db_path: Path | str | None = None,
) -> MusicalUniverseRecord:
    """Insert one universe and its first member. The server assigns the id."""
    path = Path(db_path) if db_path is not None else get_project_db_path()
    cleaned = " ".join(name.strip().split())
    universe_id = _new_universe_id()
    logger.debug(
        "Creating musical universe",
        extra={"universe_id": universe_id, "project_id": project_id, "name_length": len(cleaned)},
    )
    draft = parse_musical_universe(
        {
            "schema_version": MUSICAL_UNIVERSE_SCHEMA,
            "id": universe_id,
            "name": cleaned,
            "entities": [],
            "themes": [],
        }
    )
    _prepared, body_json = _prepare_document(draft)
    normalized = normalize_universe_name(draft.name)
    now = _utc_now_iso()
    try:
        with get_connection(path) as conn:
            conn.execute(
                """
                INSERT INTO musical_universes (
                    id, name, normalized_name, schema_version, body_json,
                    document_revision, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    universe_id,
                    draft.name,
                    normalized,
                    MUSICAL_UNIVERSE_SCHEMA,
                    body_json,
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO musical_universe_members (universe_id, project_id, created_at)
                VALUES (?, ?, ?)
                """,
                (universe_id, project_id, now),
            )
            record = _load(conn, universe_id)
    except sqlite3.IntegrityError as exc:
        raise _map_integrity(exc) from exc
    except sqlite3.Error as exc:
        logger.error(
            "Musical universe create failed",
            extra={"code": "musical_universe_store_failed", "universe_id": universe_id},
        )
        raise MusicalUniverseError(
            "musical_universe_store_failed",
            "Musical universe could not be stored",
            http_status=500,
        ) from exc
    _log_write("Musical universe inserted", record)
    return record


def get_universe(
    universe_id: str,
    *,
    db_path: Path | str | None = None,
) -> MusicalUniverseRecord:
    path = Path(db_path) if db_path is not None else get_project_db_path()
    with get_connection(path) as conn:
        return _load(conn, universe_id)


def list_universes(*, db_path: Path | str | None = None) -> list[MusicalUniverseSummaryV1]:
    """Summaries only. ``body_json`` stays in the table."""
    path = Path(db_path) if db_path is not None else get_project_db_path()
    with get_connection(path) as conn:
        rows = conn.execute(
            """
            SELECT u.id, u.name, u.document_revision, COUNT(m.project_id) AS member_count
            FROM musical_universes AS u
            LEFT JOIN musical_universe_members AS m ON m.universe_id = u.id
            GROUP BY u.id
            ORDER BY u.updated_at DESC, u.id ASC
            """
        ).fetchall()
    summaries = [
        MusicalUniverseSummaryV1(
            id=str(row["id"]),
            name=str(row["name"]),
            member_count=int(row["member_count"]),
            document_revision=int(row["document_revision"]),
        )
        for row in rows
    ]
    logger.debug("Listed musical universes", extra={"count": len(summaries)})
    return summaries


def get_universe_for_project(
    project_id: str,
    *,
    db_path: Path | str | None = None,
) -> MusicalUniverseRecord | None:
    path = Path(db_path) if db_path is not None else get_project_db_path()
    with get_connection(path) as conn:
        row = conn.execute(
            """
            SELECT universe_id
            FROM musical_universe_members
            WHERE project_id = ?
            """,
            (project_id,),
        ).fetchone()
        if row is None:
            logger.debug(
                "Project has no musical universe",
                extra={"project_id": project_id, "code": "universe_not_linked"},
            )
            return None
        return _load(conn, str(row["universe_id"]))


def add_member(
    universe_id: str,
    project_id: str,
    *,
    db_path: Path | str | None = None,
) -> MusicalUniverseRecord:
    path = Path(db_path) if db_path is not None else get_project_db_path()
    now = _utc_now_iso()
    logger.debug(
        "Adding musical universe member",
        extra={"universe_id": universe_id, "project_id": project_id},
    )
    try:
        with get_connection(path) as conn:
            existing = conn.execute(
                """
                SELECT universe_id
                FROM musical_universe_members
                WHERE project_id = ?
                """,
                (project_id,),
            ).fetchone()
            if existing is not None:
                if str(existing["universe_id"]) == universe_id:
                    return _load(conn, universe_id)
                logger.warning(
                    "Project already belongs to a musical universe",
                    extra={"code": "universe_project_busy"},
                )
                raise MusicalUniverseError(
                    "universe_project_busy",
                    "This project already belongs to a musical universe",
                    http_status=409,
                    details={"project_id": project_id},
                )
            if conn.execute(
                "SELECT 1 FROM musical_universes WHERE id = ?",
                (universe_id,),
            ).fetchone() is None:
                raise MusicalUniverseError(
                    "musical_universe_not_found",
                    "Musical universe id was not found",
                    http_status=404,
                    details={"universe_id": universe_id},
                )
            conn.execute(
                """
                INSERT INTO musical_universe_members (universe_id, project_id, created_at)
                VALUES (?, ?, ?)
                """,
                (universe_id, project_id, now),
            )
            record = _load(conn, universe_id)
    except MusicalUniverseError:
        raise
    except sqlite3.IntegrityError as exc:
        raise _map_integrity(exc) from exc
    logger.info(
        "Musical universe member added",
        extra={
            "universe_id": universe_id,
            "project_id": project_id,
            "member_count": len(record.member_project_ids),
        },
    )
    return record


def remove_member(
    universe_id: str,
    project_id: str,
    *,
    db_path: Path | str | None = None,
) -> MusicalUniverseRecord:
    path = Path(db_path) if db_path is not None else get_project_db_path()
    with get_connection(path) as conn:
        _load(conn, universe_id)
        deleted = conn.execute(
            """
            DELETE FROM musical_universe_members
            WHERE universe_id = ? AND project_id = ?
            """,
            (universe_id, project_id),
        )
        if deleted.rowcount != 1:
            logger.warning(
                "Musical universe member removal missed",
                extra={"code": "universe_project_not_member"},
            )
            raise MusicalUniverseError(
                "universe_project_not_member",
                "Project is not a member of this universe",
                http_status=404,
                details={"project_id": project_id},
            )
        record = _load(conn, universe_id)
    logger.info(
        "Musical universe member removed",
        extra={
            "universe_id": universe_id,
            "project_id": project_id,
            "member_count": len(record.member_project_ids),
        },
    )
    return record


def delete_universe(universe_id: str, *, db_path: Path | str | None = None) -> None:
    """Delete the document and its memberships. Project rows stay."""
    path = Path(db_path) if db_path is not None else get_project_db_path()
    with get_connection(path) as conn:
        deleted = conn.execute(
            "DELETE FROM musical_universes WHERE id = ?",
            (universe_id,),
        )
        if deleted.rowcount != 1:
            raise MusicalUniverseError(
                "musical_universe_not_found",
                "Musical universe id was not found",
                http_status=404,
                details={"universe_id": universe_id},
            )
    logger.info("Musical universe deleted", extra={"universe_id": universe_id})


def update_universe_document(
    universe: MusicalUniverseV1,
    *,
    expected_revision: int,
    db_path: Path | str | None = None,
    connection: sqlite3.Connection | None = None,
) -> MusicalUniverseRecord:
    """CAS-write ``body_json``. A caller-owned connection is not committed here."""
    prepared, body_json = _prepare_document(universe)
    normalized = normalize_universe_name(prepared.name)
    now = _utc_now_iso()
    logger.debug(
        "Updating musical universe",
        extra={
            "universe_id": prepared.id,
            "expected_revision": expected_revision,
            "entity_count": len(prepared.entities),
            "theme_count": len(prepared.themes),
            "uses_caller_connection": connection is not None,
        },
    )

    def _apply(conn: sqlite3.Connection) -> MusicalUniverseRecord:
        cursor = conn.execute(
            """
            UPDATE musical_universes
            SET name = ?, normalized_name = ?, schema_version = ?, body_json = ?,
                document_revision = document_revision + 1, updated_at = ?
            WHERE id = ? AND document_revision = ?
            """,
            (
                prepared.name,
                normalized,
                MUSICAL_UNIVERSE_SCHEMA,
                body_json,
                now,
                prepared.id,
                expected_revision,
            ),
        )
        if cursor.rowcount != 1:
            logger.warning(
                "Musical universe revision conflict",
                extra={"code": "musical_universe_conflict"},
            )
            raise MusicalUniverseError(
                "musical_universe_conflict",
                "expected_universe_revision does not match the stored universe",
                http_status=409,
                details={"expected_document_revision": expected_revision},
            )
        return _load(conn, prepared.id)

    try:
        if connection is not None:
            record = _apply(connection)
        else:
            path = Path(db_path) if db_path is not None else get_project_db_path()
            with get_connection(path) as conn:
                record = _apply(conn)
    except MusicalUniverseError:
        raise
    except sqlite3.IntegrityError as exc:
        raise _map_integrity(exc) from exc
    _log_write("Musical universe CAS updated", record)
    return record
