"""SQLite CRUD for project-scoped ``spatial.scene.v1`` documents.

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
from app.services.persistence_secret_guard import (
    PersistenceSecretError,
    assert_no_secret_fields,
    assert_no_secret_values,
)
from app.spatial_schemas import (
    SPATIAL_ERROR_CODES,
    SpatialSceneError,
    SpatialSceneSummaryV1,
    SpatialSceneV1,
    parse_spatial_scene,
    reject_scene_embedded_material,
)
from app.spatial_settings import load_spatial_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SpatialSceneRecord:
    id: str
    project_id: str
    name: str
    schema_version: str
    scene: SpatialSceneV1
    document_revision: int
    source_composition_fingerprint: str
    source_stem_set_id: str | None
    source_stem_set_fingerprint: str | None
    created_at: str
    updated_at: str


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _new_scene_id() -> str:
    return f"sscene_{secrets.token_hex(8)}"


def is_stale_fingerprint(pin: str | None, request_fingerprint: str | None) -> bool:
    """Soft-stale when scene pin ≠ provided (request) fingerprint."""
    if pin is None or request_fingerprint is None:
        return False
    stale = pin != request_fingerprint
    if stale:
        logger.debug(
            "Spatial scene soft-stale",
            extra={"code": "spatial_scene_stale"},
        )
    return stale


def _guard_body(payload: dict[str, Any]) -> str:
    settings = load_spatial_settings()
    try:
        assert_no_secret_fields(payload, context="spatial_scene")
        body_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        assert_no_secret_values(body_json, field_name="body_json")
        if isinstance(payload.get("name"), str):
            assert_no_secret_values(payload["name"], field_name="name")
    except PersistenceSecretError as exc:
        logger.warning(
            "Spatial scene secret guard rejected payload",
            extra={"code": "persistence_secret_rejected"},
        )
        raise SpatialSceneError(
            "persistence_secret_rejected",
            SPATIAL_ERROR_CODES["persistence_secret_rejected"],
            http_status=422,
            details={"cause": exc.code},
        ) from exc
    if len(body_json.encode("utf-8")) > settings.max_body_bytes:
        raise SpatialSceneError(
            "spatial_scene_invalid",
            "Spatial scene body exceeds the configured byte cap",
            http_status=422,
        )
    return body_json


def _prepare_scene(
    scene: SpatialSceneV1,
    *,
    project_id: str,
    scene_id: str,
) -> tuple[SpatialSceneV1, str]:
    payload = scene.model_dump(mode="json")
    reject_scene_embedded_material(payload)
    payload["id"] = scene_id
    payload["project_id"] = project_id
    parsed = parse_spatial_scene(payload)
    body_json = _guard_body(parsed.model_dump(mode="json"))
    return parsed, body_json


def _row_scene(row: sqlite3.Row) -> SpatialSceneRecord:
    try:
        body = json.loads(row["body_json"])
        scene = parse_spatial_scene(body if isinstance(body, dict) else {})
    except (SpatialSceneError, json.JSONDecodeError) as exc:
        logger.error(
            "Stored spatial scene body failed validation",
            extra={"scene_id": row["id"], "code": "spatial_scene_invalid"},
        )
        raise SpatialSceneError(
            "spatial_scene_invalid",
            "Stored spatial scene body is invalid",
            http_status=500,
            details={"scene_id": row["id"]},
        ) from exc
    return SpatialSceneRecord(
        id=row["id"],
        project_id=row["project_id"],
        name=row["name"],
        schema_version=row["schema_version"],
        scene=scene.model_copy(
            update={
                "id": row["id"],
                "project_id": row["project_id"],
                "name": row["name"],
                "source_composition_fingerprint": row["source_composition_fingerprint"],
                "source_stem_set_id": row["source_stem_set_id"],
                "source_stem_set_fingerprint": row["source_stem_set_fingerprint"],
            }
        ),
        document_revision=int(row["document_revision"]),
        source_composition_fingerprint=row["source_composition_fingerprint"],
        source_stem_set_id=row["source_stem_set_id"],
        source_stem_set_fingerprint=row["source_stem_set_fingerprint"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _map_integrity(exc: sqlite3.IntegrityError, *, scene_id: str | None) -> SpatialSceneError:
    text = str(exc).lower()
    if "foreign key" in text:
        code = "project_not_found"
        status = 404
    else:
        code = "spatial_scene_store_failed"
        status = 500
    logger.error(
        "Spatial scene SQLite constraint failed",
        extra={"code": code, "scene_id": scene_id},
    )
    return SpatialSceneError(
        code,
        SPATIAL_ERROR_CODES.get(code, "Spatial scene could not be stored"),
        http_status=status,
        details={"scene_id": scene_id} if scene_id else {},
    )


def create_scene(
    project_id: str,
    scene: SpatialSceneV1,
    *,
    db_path: Path | None = None,
) -> SpatialSceneRecord:
    path = db_path or get_project_db_path()
    if scene.id:
        raise SpatialSceneError(
            "identity_mismatch",
            "Create does not accept a client scene id",
            http_status=422,
        )
    if scene.project_id not in (None, project_id):
        raise SpatialSceneError(
            "identity_mismatch",
            "project_id does not match the route",
            http_status=422,
            details={"project_id": project_id},
        )
    scene_id = _new_scene_id()
    prepared, body_json = _prepare_scene(scene, project_id=project_id, scene_id=scene_id)
    now = _utc_now_iso()
    try:
        with get_connection(path) as conn:
            conn.execute(
                """
                INSERT INTO spatial_scenes (
                    id, project_id, name, schema_version, body_json,
                    document_revision, source_composition_fingerprint,
                    source_stem_set_id, source_stem_set_fingerprint,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
                """,
                (
                    scene_id,
                    project_id,
                    prepared.name,
                    prepared.schema_version,
                    body_json,
                    prepared.source_composition_fingerprint,
                    prepared.source_stem_set_id,
                    prepared.source_stem_set_fingerprint,
                    now,
                    now,
                ),
            )
            conn.commit()
    except sqlite3.IntegrityError as exc:
        raise _map_integrity(exc, scene_id=scene_id) from exc
    logger.info(
        "Created spatial scene",
        extra={
            "scene_id": scene_id,
            "project_id": project_id,
            "document_revision": 1,
            "source_count": len(prepared.sources),
        },
    )
    return get_scene(project_id, scene_id, db_path=path)


def get_scene(
    project_id: str,
    scene_id: str,
    *,
    db_path: Path | None = None,
) -> SpatialSceneRecord:
    path = db_path or get_project_db_path()
    with get_connection(path) as conn:
        row = conn.execute(
            """
            SELECT * FROM spatial_scenes
            WHERE project_id = ? AND id = ?
            """,
            (project_id, scene_id),
        ).fetchone()
    if row is None:
        raise SpatialSceneError(
            "spatial_scene_not_found",
            SPATIAL_ERROR_CODES["spatial_scene_not_found"],
            http_status=404,
            details={"scene_id": scene_id},
        )
    return _row_scene(row)


def list_scenes(
    project_id: str,
    *,
    db_path: Path | None = None,
) -> list[SpatialSceneSummaryV1]:
    path = db_path or get_project_db_path()
    with get_connection(path) as conn:
        rows = conn.execute(
            """
            SELECT id, project_id, name, document_revision,
                   source_composition_fingerprint, source_stem_set_id,
                   source_stem_set_fingerprint, created_at, updated_at, body_json
            FROM spatial_scenes
            WHERE project_id = ?
            ORDER BY updated_at DESC, id ASC
            """,
            (project_id,),
        ).fetchall()
    summaries: list[SpatialSceneSummaryV1] = []
    for row in rows:
        source_count = 0
        try:
            body = json.loads(row["body_json"])
            if isinstance(body, dict) and isinstance(body.get("sources"), list):
                source_count = len(body["sources"])
        except json.JSONDecodeError:
            source_count = 0
        summaries.append(
            SpatialSceneSummaryV1(
                id=row["id"],
                project_id=row["project_id"],
                name=row["name"],
                document_revision=int(row["document_revision"]),
                source_composition_fingerprint=row["source_composition_fingerprint"],
                source_stem_set_id=row["source_stem_set_id"],
                source_stem_set_fingerprint=row["source_stem_set_fingerprint"],
                source_count=source_count,
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
        )
    return summaries


def replace_scene(
    project_id: str,
    scene_id: str,
    scene: SpatialSceneV1,
    *,
    expected_document_revision: int,
    db_path: Path | None = None,
) -> SpatialSceneRecord:
    path = db_path or get_project_db_path()
    if scene.project_id not in (None, project_id):
        raise SpatialSceneError(
            "identity_mismatch",
            "project_id does not match the route",
            http_status=422,
        )
    if scene.id not in (None, scene_id):
        raise SpatialSceneError(
            "identity_mismatch",
            "scene id does not match the route",
            http_status=422,
        )
    prepared, body_json = _prepare_scene(scene, project_id=project_id, scene_id=scene_id)
    now = _utc_now_iso()
    with get_connection(path) as conn:
        cur = conn.execute(
            """
            UPDATE spatial_scenes
            SET name = ?, schema_version = ?, body_json = ?,
                document_revision = document_revision + 1,
                source_composition_fingerprint = ?,
                source_stem_set_id = ?,
                source_stem_set_fingerprint = ?,
                updated_at = ?
            WHERE project_id = ? AND id = ? AND document_revision = ?
            """,
            (
                prepared.name,
                prepared.schema_version,
                body_json,
                prepared.source_composition_fingerprint,
                prepared.source_stem_set_id,
                prepared.source_stem_set_fingerprint,
                now,
                project_id,
                scene_id,
                expected_document_revision,
            ),
        )
        if cur.rowcount == 0:
            existing = conn.execute(
                "SELECT id FROM spatial_scenes WHERE project_id = ? AND id = ?",
                (project_id, scene_id),
            ).fetchone()
            if existing is None:
                raise SpatialSceneError(
                    "spatial_scene_not_found",
                    SPATIAL_ERROR_CODES["spatial_scene_not_found"],
                    http_status=404,
                    details={"scene_id": scene_id},
                )
            logger.warning(
                "Spatial scene CAS conflict",
                extra={
                    "code": "spatial_scene_conflict",
                    "scene_id": scene_id,
                    "expected_document_revision": expected_document_revision,
                },
            )
            raise SpatialSceneError(
                "spatial_scene_conflict",
                SPATIAL_ERROR_CODES["spatial_scene_conflict"],
                http_status=409,
                details={"expected_document_revision": expected_document_revision},
            )
        conn.commit()
    logger.info(
        "Updated spatial scene",
        extra={
            "scene_id": scene_id,
            "project_id": project_id,
            "expected_document_revision": expected_document_revision,
        },
    )
    return get_scene(project_id, scene_id, db_path=path)


def delete_scene(
    project_id: str,
    scene_id: str,
    *,
    db_path: Path | None = None,
) -> None:
    path = db_path or get_project_db_path()
    with get_connection(path) as conn:
        cur = conn.execute(
            "DELETE FROM spatial_scenes WHERE project_id = ? AND id = ?",
            (project_id, scene_id),
        )
        if cur.rowcount == 0:
            raise SpatialSceneError(
                "spatial_scene_not_found",
                SPATIAL_ERROR_CODES["spatial_scene_not_found"],
                http_status=404,
                details={"scene_id": scene_id},
            )
        conn.commit()
    logger.info(
        "Deleted spatial scene",
        extra={"scene_id": scene_id, "project_id": project_id},
    )
