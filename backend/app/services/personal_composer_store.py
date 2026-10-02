"""SQLite rows for ``personal.training_job.v1``.

Does not copy scores and does not import ``project_store``. Manifest JSON is
text without composition documents. ``ai_agents`` must not import this module.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.db.connection import get_connection, get_project_db_path
from app.personal_composer_schemas import (
    TRAINING_JOB_SCHEMA,
    PersonalComposerError,
    PersonalEvalV1,
    PersonalTrainingJobV1,
    PersonalTrainingManifestV1,
    utc_now_iso,
)

logger = logging.getLogger(__name__)

_COLUMNS = (
    "id",
    "schema_version",
    "display_name",
    "status",
    "registry_model_id",
    "engine",
    "base_model_id",
    "snapshot_version",
    "step",
    "max_steps",
    "manifest_json",
    "eval_json",
    "owner_actor_id",
    "error_code",
    "created_at",
    "updated_at",
)


@dataclass(frozen=True)
class PersonalComposerRow:
    job: PersonalTrainingJobV1
    eval_report: PersonalEvalV1 | None


def _refuse(
    code: str,
    message: str,
    *,
    http_status: int = 422,
    details: dict[str, Any] | None = None,
) -> PersonalComposerError:
    logger.warning("Personal composer store refused", extra={"code": code})
    return PersonalComposerError(code, message, http_status=http_status, details=details)


def _parse_manifest(raw: str) -> PersonalTrainingManifestV1:
    try:
        payload = json.loads(raw)
        return PersonalTrainingManifestV1.model_validate(payload)
    except (json.JSONDecodeError, ValidationError) as exc:
        logger.debug(
            "Personal composer manifest parse failed",
            extra={"field_name": "manifest_json", "code": "personal_manifest_invalid"},
        )
        raise _refuse(
            "personal_manifest_invalid",
            "Stored personal composer manifest is not valid.",
            http_status=500,
        ) from exc


def _parse_eval(raw: str | None) -> PersonalEvalV1 | None:
    if not raw:
        return None
    try:
        return PersonalEvalV1.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as exc:
        logger.debug(
            "Personal composer eval parse failed",
            extra={"field_name": "eval_json", "code": "personal_eval_invalid"},
        )
        raise _refuse(
            "personal_eval_invalid",
            "Stored personal composer eval is not valid.",
            http_status=500,
        ) from exc


def _row_to_record(row: sqlite3.Row) -> PersonalComposerRow:
    manifest = _parse_manifest(row["manifest_json"])
    job = PersonalTrainingJobV1(
        adapter_id=row["id"],
        display_name=row["display_name"],
        status=row["status"],
        registry_model_id=row["registry_model_id"],
        engine=row["engine"],
        base_model_id=row["base_model_id"],
        snapshot_version=row["snapshot_version"],
        step=int(row["step"]),
        max_steps=int(row["max_steps"]),
        error_code=row["error_code"],
        manifest=manifest,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        owner_actor_id=row["owner_actor_id"],
    )
    return PersonalComposerRow(job=job, eval_report=_parse_eval(row["eval_json"]))


def _fetch(conn: sqlite3.Connection, adapter_id: str) -> sqlite3.Row | None:
    return conn.execute(
        f"SELECT {', '.join(_COLUMNS)} FROM personal_composer_adapters WHERE id = ?",
        (adapter_id,),
    ).fetchone()


def insert_adapter(
    *,
    adapter_id: str,
    display_name: str,
    registry_model_id: str,
    engine: str,
    base_model_id: str,
    snapshot_version: str,
    max_steps: int,
    manifest: PersonalTrainingManifestV1,
    owner_actor_id: str | None,
    step: int = 0,
    db_path: Path | str | None = None,
) -> PersonalComposerRow:
    """Insert one running row. A live display name is ``personal_name_taken``."""
    path = Path(db_path) if db_path is not None else get_project_db_path()
    now = utc_now_iso()
    manifest_text = json.dumps(manifest.model_dump(mode="json"), separators=(",", ":"), sort_keys=True)
    logger.info(
        "Personal composer insert started",
        extra={"adapter_id": adapter_id, "status": "running", "engine": engine},
    )
    try:
        with get_connection(path) as conn:
            taken = conn.execute(
                """
                SELECT id FROM personal_composer_adapters
                WHERE display_name = ? AND status != 'deleted'
                """,
                (display_name,),
            ).fetchone()
            if taken is not None:
                raise _refuse(
                    "personal_name_taken",
                    "That display name already belongs to a personal composer.",
                    http_status=409,
                )
            conn.execute(
                """
                INSERT INTO personal_composer_adapters (
                    id, schema_version, display_name, status, registry_model_id,
                    engine, base_model_id, snapshot_version, step, max_steps,
                    manifest_json, eval_json, owner_actor_id, error_code,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 'running', ?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL, ?, ?)
                """,
                (
                    adapter_id,
                    TRAINING_JOB_SCHEMA,
                    display_name,
                    registry_model_id,
                    engine,
                    base_model_id,
                    snapshot_version,
                    step,
                    max_steps,
                    manifest_text,
                    owner_actor_id,
                    now,
                    now,
                ),
            )
            stored = _fetch(conn, adapter_id)
    except sqlite3.IntegrityError as exc:
        logger.warning("Personal composer insert conflict", extra={"code": "personal_name_taken"})
        raise _refuse(
            "personal_name_taken",
            "That display name already belongs to a personal composer.",
            http_status=409,
        ) from exc
    if stored is None:
        raise _refuse("personal_not_found", "Personal composer row was not stored.", http_status=500)
    record = _row_to_record(stored)
    logger.info(
        "Personal composer inserted",
        extra={
            "adapter_id": adapter_id,
            "status": record.job.status,
            "engine": record.job.engine,
        },
    )
    return record


def get_adapter(
    adapter_id: str,
    *,
    db_path: Path | str | None = None,
) -> PersonalComposerRow:
    """Return one non-deleted row. Deleted and missing ids are not found."""
    path = Path(db_path) if db_path is not None else get_project_db_path()
    with get_connection(path) as conn:
        row = _fetch(conn, adapter_id)
    if row is None or row["status"] == "deleted":
        raise _refuse(
            "personal_not_found",
            "Personal composer was not found.",
            http_status=404,
            details={"adapter_id": adapter_id},
        )
    return _row_to_record(row)


def get_by_registry_id(
    registry_model_id: str,
    *,
    db_path: Path | str | None = None,
) -> PersonalComposerRow | None:
    """Return a non-deleted row for a registry id, or None."""
    path = Path(db_path) if db_path is not None else get_project_db_path()
    with get_connection(path) as conn:
        row = conn.execute(
            f"""
            SELECT {', '.join(_COLUMNS)}
            FROM personal_composer_adapters
            WHERE registry_model_id = ? AND status != 'deleted'
            """,
            (registry_model_id,),
        ).fetchone()
    if row is None:
        return None
    return _row_to_record(row)


def list_adapters(
    *,
    owner_actor_id: str | None = None,
    filter_owner: bool = False,
    db_path: Path | str | None = None,
) -> list[PersonalComposerRow]:
    """List non-deleted jobs. This read does not change status."""
    path = Path(db_path) if db_path is not None else get_project_db_path()
    logger.debug(
        "Personal composer list started",
        extra={"filter_owner": filter_owner},
    )
    sql = f"""
        SELECT {', '.join(_COLUMNS)}
        FROM personal_composer_adapters
        WHERE status != 'deleted'
    """
    params: tuple[Any, ...] = ()
    if filter_owner:
        sql += " AND owner_actor_id = ?"
        params = (owner_actor_id,)
    sql += " ORDER BY created_at ASC"
    with get_connection(path) as conn:
        rows = conn.execute(sql, params).fetchall()
    records = [_row_to_record(row) for row in rows]
    logger.debug("Personal composer list finished", extra={"count": len(records)})
    return records


def update_adapter(
    adapter_id: str,
    *,
    status: str | None = None,
    step: int | None = None,
    error_code: str | None = None,
    clear_error: bool = False,
    eval_report: PersonalEvalV1 | None = None,
    db_path: Path | str | None = None,
) -> PersonalComposerRow:
    """Update status, step, or eval. A deleted row stays deleted."""
    path = Path(db_path) if db_path is not None else get_project_db_path()
    with get_connection(path) as conn:
        current = _fetch(conn, adapter_id)
        if current is None:
            raise _refuse(
                "personal_not_found",
                "Personal composer was not found.",
                http_status=404,
                details={"adapter_id": adapter_id},
            )
        if current["status"] == "deleted":
            logger.warning(
                "Personal composer transition refused",
                extra={"adapter_id": adapter_id, "code": "personal_deleted"},
            )
            return _row_to_record(current)
        next_status = status if status is not None else current["status"]
        next_step = int(current["step"] if step is None else step)
        next_error = None if clear_error else current["error_code"]
        if error_code is not None:
            next_error = error_code
        next_eval = current["eval_json"]
        if eval_report is not None:
            next_eval = json.dumps(eval_report.model_dump(mode="json"), separators=(",", ":"), sort_keys=True)
        now = utc_now_iso()
        conn.execute(
            """
            UPDATE personal_composer_adapters
            SET status = ?, step = ?, error_code = ?, eval_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (next_status, next_step, next_error, next_eval, now, adapter_id),
        )
        stored = _fetch(conn, adapter_id)
    if stored is None:
        raise _refuse("personal_not_found", "Personal composer was not found.", http_status=404)
    logger.info(
        "Personal composer status changed",
        extra={"adapter_id": adapter_id, "status": stored["status"], "engine": stored["engine"]},
    )
    return _row_to_record(stored)
