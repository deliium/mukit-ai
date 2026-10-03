"""SQLite CRUD for project-scoped ``performance.plan.v1`` documents.

Does not load Composition. CAS via ``expected_document_revision``.
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
from app.performance_schemas import (
    PERFORMANCE_ERROR_CODES,
    PerformancePlanError,
    PerformancePlanSummaryV1,
    PerformancePlanV1,
    parse_performance_plan,
    reject_plan_embedded_material,
)
from app.performance_settings import load_performance_settings
from app.services.persistence_secret_guard import (
    PersistenceSecretError,
    assert_no_secret_fields,
    assert_no_secret_values,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PerformancePlanRecord:
    id: str
    project_id: str
    name: str
    preset_id: str
    schema_version: str
    plan: PerformancePlanV1
    document_revision: int
    source_composition_fingerprint: str
    created_at: str
    updated_at: str


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _new_plan_id() -> str:
    return f"pplan_{secrets.token_hex(8)}"


def is_stale_fingerprint(pin: str, request_fingerprint: str) -> bool:
    """Soft-stale when plan pin ≠ provided (request) fingerprint."""
    stale = pin != request_fingerprint
    if stale:
        logger.debug(
            "Performance plan soft-stale",
            extra={"code": "performance_plan_stale"},
        )
    return stale


def _guard_body(payload: dict[str, Any]) -> str:
    settings = load_performance_settings()
    try:
        assert_no_secret_fields(payload, context="performance_plan")
        body_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        assert_no_secret_values(body_json, field_name="body_json")
        if isinstance(payload.get("name"), str):
            assert_no_secret_values(payload["name"], field_name="name")
    except PersistenceSecretError as exc:
        logger.warning(
            "Performance plan secret guard rejected payload",
            extra={"code": "persistence_secret_rejected"},
        )
        raise PerformancePlanError(
            "persistence_secret_rejected",
            PERFORMANCE_ERROR_CODES["persistence_secret_rejected"],
            http_status=422,
            details={"cause": exc.code},
        ) from exc
    if len(body_json.encode("utf-8")) > settings.max_body_bytes:
        raise PerformancePlanError(
            "performance_plan_invalid",
            "Performance plan body exceeds the configured byte cap",
            http_status=422,
        )
    return body_json


def _prepare_plan(
    plan: PerformancePlanV1,
    *,
    project_id: str,
    plan_id: str,
) -> tuple[PerformancePlanV1, str]:
    payload = plan.model_dump(mode="json")
    reject_plan_embedded_material(payload)
    payload["id"] = plan_id
    payload["project_id"] = project_id
    parsed = parse_performance_plan(payload)
    body_json = _guard_body(parsed.model_dump(mode="json"))
    return parsed, body_json


def _row_plan(row: sqlite3.Row) -> PerformancePlanRecord:
    try:
        body = json.loads(row["body_json"])
        plan = parse_performance_plan(body if isinstance(body, dict) else {})
    except (PerformancePlanError, json.JSONDecodeError) as exc:
        logger.error(
            "Stored performance plan body failed validation",
            extra={"plan_id": row["id"], "code": "performance_plan_invalid"},
        )
        raise PerformancePlanError(
            "performance_plan_invalid",
            "Stored performance plan body is invalid",
            http_status=500,
            details={"plan_id": row["id"]},
        ) from exc
    return PerformancePlanRecord(
        id=row["id"],
        project_id=row["project_id"],
        name=row["name"],
        preset_id=row["preset_id"],
        schema_version=row["schema_version"],
        plan=plan.model_copy(
            update={
                "id": row["id"],
                "project_id": row["project_id"],
                "name": row["name"],
                "source_composition_fingerprint": row["source_composition_fingerprint"],
            }
        ),
        document_revision=int(row["document_revision"]),
        source_composition_fingerprint=row["source_composition_fingerprint"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _map_integrity(exc: sqlite3.IntegrityError, *, plan_id: str | None) -> PerformancePlanError:
    text = str(exc).lower()
    if "foreign key" in text:
        code = "project_not_found"
        status = 404
    else:
        code = "performance_plan_store_failed"
        status = 500
    logger.error(
        "Performance plan SQLite constraint failed",
        extra={"code": code, "plan_id": plan_id},
    )
    return PerformancePlanError(
        code,
        PERFORMANCE_ERROR_CODES.get(code, "Performance plan could not be stored"),
        http_status=status,
        details={"plan_id": plan_id} if plan_id else {},
    )


def create_plan(
    project_id: str,
    plan: PerformancePlanV1,
    *,
    db_path: Path | None = None,
) -> PerformancePlanRecord:
    path = db_path or get_project_db_path()
    if plan.id:
        raise PerformancePlanError(
            "identity_mismatch",
            "Create does not accept a client plan id",
            http_status=422,
        )
    if plan.project_id not in (None, project_id):
        raise PerformancePlanError(
            "identity_mismatch",
            "project_id does not match the route",
            http_status=422,
            details={"project_id": project_id},
        )
    plan_id = _new_plan_id()
    prepared, body_json = _prepare_plan(plan, project_id=project_id, plan_id=plan_id)
    now = _utc_now_iso()
    try:
        with get_connection(path) as conn:
            conn.execute(
                """
                INSERT INTO performance_plans (
                    id, project_id, name, preset_id, schema_version, body_json,
                    document_revision, source_composition_fingerprint,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    plan_id,
                    project_id,
                    prepared.name,
                    prepared.preset_id,
                    prepared.schema_version,
                    body_json,
                    prepared.source_composition_fingerprint,
                    now,
                    now,
                ),
            )
            conn.commit()
    except sqlite3.IntegrityError as exc:
        raise _map_integrity(exc, plan_id=plan_id) from exc
    logger.info(
        "Created performance plan",
        extra={
            "plan_id": plan_id,
            "project_id": project_id,
            "preset_id": prepared.preset_id,
            "document_revision": 1,
        },
    )
    return get_plan(project_id, plan_id, db_path=path)


def get_plan(
    project_id: str,
    plan_id: str,
    *,
    db_path: Path | None = None,
) -> PerformancePlanRecord:
    path = db_path or get_project_db_path()
    with get_connection(path) as conn:
        row = conn.execute(
            """
            SELECT * FROM performance_plans
            WHERE project_id = ? AND id = ?
            """,
            (project_id, plan_id),
        ).fetchone()
    if row is None:
        raise PerformancePlanError(
            "performance_plan_not_found",
            PERFORMANCE_ERROR_CODES["performance_plan_not_found"],
            http_status=404,
            details={"plan_id": plan_id},
        )
    return _row_plan(row)


def list_plans(
    project_id: str,
    *,
    db_path: Path | None = None,
) -> list[PerformancePlanSummaryV1]:
    path = db_path or get_project_db_path()
    with get_connection(path) as conn:
        rows = conn.execute(
            """
            SELECT id, project_id, name, preset_id, document_revision,
                   source_composition_fingerprint, created_at, updated_at, body_json
            FROM performance_plans
            WHERE project_id = ?
            ORDER BY updated_at DESC, id ASC
            """,
            (project_id,),
        ).fetchall()
    summaries: list[PerformancePlanSummaryV1] = []
    for row in rows:
        try:
            body = json.loads(row["body_json"])
            engine = body.get("engine", "deterministic") if isinstance(body, dict) else "deterministic"
        except json.JSONDecodeError:
            engine = "deterministic"
        summaries.append(
            PerformancePlanSummaryV1(
                id=row["id"],
                project_id=row["project_id"],
                name=row["name"],
                preset_id=row["preset_id"],
                engine=engine,
                document_revision=int(row["document_revision"]),
                source_composition_fingerprint=row["source_composition_fingerprint"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
        )
    return summaries


def replace_plan(
    project_id: str,
    plan_id: str,
    plan: PerformancePlanV1,
    *,
    expected_document_revision: int,
    db_path: Path | None = None,
) -> PerformancePlanRecord:
    path = db_path or get_project_db_path()
    if plan.project_id not in (None, project_id):
        raise PerformancePlanError(
            "identity_mismatch",
            "project_id does not match the route",
            http_status=422,
        )
    if plan.id not in (None, plan_id):
        raise PerformancePlanError(
            "identity_mismatch",
            "plan id does not match the route",
            http_status=422,
        )
    prepared, body_json = _prepare_plan(plan, project_id=project_id, plan_id=plan_id)
    now = _utc_now_iso()
    with get_connection(path) as conn:
        cur = conn.execute(
            """
            UPDATE performance_plans
            SET name = ?, preset_id = ?, schema_version = ?, body_json = ?,
                document_revision = document_revision + 1,
                source_composition_fingerprint = ?, updated_at = ?
            WHERE project_id = ? AND id = ? AND document_revision = ?
            """,
            (
                prepared.name,
                prepared.preset_id,
                prepared.schema_version,
                body_json,
                prepared.source_composition_fingerprint,
                now,
                project_id,
                plan_id,
                expected_document_revision,
            ),
        )
        if cur.rowcount == 0:
            existing = conn.execute(
                "SELECT id FROM performance_plans WHERE project_id = ? AND id = ?",
                (project_id, plan_id),
            ).fetchone()
            if existing is None:
                raise PerformancePlanError(
                    "performance_plan_not_found",
                    PERFORMANCE_ERROR_CODES["performance_plan_not_found"],
                    http_status=404,
                    details={"plan_id": plan_id},
                )
            logger.warning(
                "Performance plan CAS conflict",
                extra={
                    "code": "performance_plan_conflict",
                    "plan_id": plan_id,
                    "expected_document_revision": expected_document_revision,
                },
            )
            raise PerformancePlanError(
                "performance_plan_conflict",
                PERFORMANCE_ERROR_CODES["performance_plan_conflict"],
                http_status=409,
                details={"expected_document_revision": expected_document_revision},
            )
        conn.commit()
    logger.info(
        "Updated performance plan",
        extra={
            "plan_id": plan_id,
            "project_id": project_id,
            "expected_document_revision": expected_document_revision,
        },
    )
    return get_plan(project_id, plan_id, db_path=path)


def delete_plan(
    project_id: str,
    plan_id: str,
    *,
    db_path: Path | None = None,
) -> None:
    path = db_path or get_project_db_path()
    with get_connection(path) as conn:
        cur = conn.execute(
            "DELETE FROM performance_plans WHERE project_id = ? AND id = ?",
            (project_id, plan_id),
        )
        if cur.rowcount == 0:
            raise PerformancePlanError(
                "performance_plan_not_found",
                PERFORMANCE_ERROR_CODES["performance_plan_not_found"],
                http_status=404,
                details={"plan_id": plan_id},
            )
        conn.commit()
    logger.info(
        "Deleted performance plan",
        extra={"plan_id": plan_id, "project_id": project_id},
    )
