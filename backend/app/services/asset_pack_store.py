"""SQLite CRUD for ``asset.pack.v1`` documents and slot status rows.

CAS on ``document_revision``. Never writes ``composition_json`` or note events.
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

from app.asset_pack_schemas import (
    ASSET_PACK_SCHEMA,
    PACK_ID_PREFIX,
    AssetPackError,
    AssetPackPlanV1,
    AssetPackSlotRecordV1,
    AssetPackStatus,
    AssetPackV1,
    reject_embedded_note_keys,
)
from app.db.connection import get_connection, get_project_db_path
from app.services.persistence_secret_guard import (
    PersistenceSecretError,
    assert_no_secret_fields,
    assert_no_secret_values,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AssetPackRecord:
    id: str
    name: str
    schema_version: str
    body: AssetPackV1
    plan: AssetPackPlanV1
    document_revision: int
    universe_id: str | None
    composer_profile_id: str | None
    status: AssetPackStatus
    slots: list[AssetPackSlotRecordV1]
    created_at: str
    updated_at: str


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _new_pack_id() -> str:
    return f"{PACK_ID_PREFIX}{secrets.token_hex(8)}"


def _guard_json(payload: dict[str, Any], *, context: str) -> str:
    reject_embedded_note_keys(payload, model=context)
    try:
        assert_no_secret_fields(payload, context=context)
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        assert_no_secret_values(encoded, field_name=f"{context}_json")
    except PersistenceSecretError as exc:
        logger.warning(
            "Asset pack secret guard rejected payload",
            extra={"code": "persistence_secret_rejected", "context": context},
        )
        raise AssetPackError(
            "asset_pack_invalid",
            "Asset pack payload contains a secret field or value",
        ) from exc
    return encoded


def _slot_rows(conn: sqlite3.Connection, pack_id: str) -> list[AssetPackSlotRecordV1]:
    rows = conn.execute(
        """
        SELECT slot_id, project_id, autonomous_run_id, adaptive_score_id,
               status, head_revision_id, updated_at
        FROM asset_pack_slots
        WHERE pack_id = ?
        ORDER BY slot_id ASC
        """,
        (pack_id,),
    ).fetchall()
    result: list[AssetPackSlotRecordV1] = []
    for row in rows:
        result.append(
            AssetPackSlotRecordV1.model_validate(
                {
                    "slot_id": row["slot_id"],
                    "status": row["status"],
                    "project_id": row["project_id"],
                    "autonomous_run_id": row["autonomous_run_id"],
                    "adaptive_score_id": row["adaptive_score_id"],
                    "head_revision_id": row["head_revision_id"],
                    "updated_at": row["updated_at"],
                }
            )
        )
    return result


def _row_record(conn: sqlite3.Connection, row: sqlite3.Row) -> AssetPackRecord:
    try:
        body_raw = json.loads(row["body_json"])
        plan_raw = json.loads(row["plan_json"])
        body = AssetPackV1.model_validate(body_raw if isinstance(body_raw, dict) else {})
        plan = AssetPackPlanV1.model_validate(plan_raw if isinstance(plan_raw, dict) else {})
    except (AssetPackError, json.JSONDecodeError, ValueError) as exc:
        logger.error(
            "Stored asset pack body failed validation",
            extra={"pack_id": row["id"], "code": "asset_pack_invalid"},
        )
        raise AssetPackError("asset_pack_invalid", "Stored asset pack body is invalid") from exc

    slots = _slot_rows(conn, str(row["id"]))
    label_by_id = {slot.slot_id: slot.label for slot in plan.slots}
    slots_with_labels = [
        slot.model_copy(update={"label": label_by_id.get(slot.slot_id)})
        for slot in slots
    ]
    body = body.model_copy(
        update={
            "id": row["id"],
            "name": row["name"],
            "status": row["status"],
            "document_revision": int(row["document_revision"]),
            "universe_id": row["universe_id"],
            "composer_profile_id": row["composer_profile_id"],
            "slots": slots_with_labels,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
    )
    return AssetPackRecord(
        id=str(row["id"]),
        name=str(row["name"]),
        schema_version=str(row["schema_version"]),
        body=body,
        plan=plan,
        document_revision=int(row["document_revision"]),
        universe_id=row["universe_id"],
        composer_profile_id=row["composer_profile_id"],
        status=row["status"],  # type: ignore[arg-type]
        slots=slots_with_labels,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _load(conn: sqlite3.Connection, pack_id: str) -> AssetPackRecord:
    row = conn.execute(
        """
        SELECT id, name, schema_version, body_json, plan_json, document_revision,
               universe_id, composer_profile_id, status, created_at, updated_at
        FROM asset_packs
        WHERE id = ?
        """,
        (pack_id,),
    ).fetchone()
    if row is None:
        logger.debug(
            "Asset pack missing",
            extra={"pack_id": pack_id, "code": "asset_pack_not_found"},
        )
        raise AssetPackError("asset_pack_not_found")
    return _row_record(conn, row)


def create_pack(
    plan: AssetPackPlanV1,
    *,
    db_path: Path | None = None,
) -> AssetPackRecord:
    """Persist a planned pack + pending slot rows. Does not generate scores."""
    pack_id = _new_pack_id()
    now = _utc_now_iso()
    body = AssetPackV1.model_validate(
        {
            "schema_version": ASSET_PACK_SCHEMA,
            "id": pack_id,
            "name": plan.title,
            "status": "planned",
            "plan_digest": plan.plan_digest,
            "universe_id": None,
            "composer_profile_id": plan.composer_profile_id,
            "composer_profile_strength": plan.composer_profile_strength,
            "document_revision": 1,
            "slots": [
                {"slot_id": slot.slot_id, "label": slot.label, "status": "pending"}
                for slot in plan.slots
            ],
            "created_at": now,
            "updated_at": now,
        }
    )
    body_json = _guard_json(body.model_dump(mode="json"), context="asset_pack_body")
    plan_json = _guard_json(plan.model_dump(mode="json"), context="asset_pack_plan")

    path = db_path or get_project_db_path()
    with get_connection(path) as conn:
        try:
            conn.execute(
                """
                INSERT INTO asset_packs (
                    id, name, schema_version, body_json, plan_json, document_revision,
                    universe_id, composer_profile_id, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 1, NULL, ?, 'planned', ?, ?)
                """,
                (
                    pack_id,
                    plan.title,
                    ASSET_PACK_SCHEMA,
                    body_json,
                    plan_json,
                    plan.composer_profile_id,
                    now,
                    now,
                ),
            )
            for slot in plan.slots:
                conn.execute(
                    """
                    INSERT INTO asset_pack_slots (
                        pack_id, slot_id, project_id, autonomous_run_id,
                        adaptive_score_id, status, head_revision_id, updated_at
                    ) VALUES (?, ?, NULL, NULL, NULL, 'pending', NULL, ?)
                    """,
                    (pack_id, slot.slot_id, now),
                )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            logger.error(
                "Asset pack insert failed",
                extra={"code": "asset_pack_invalid", "pack_id": pack_id},
            )
            raise AssetPackError("asset_pack_invalid", "Asset pack could not be stored") from exc

        record = _load(conn, pack_id)

    logger.info(
        "Asset pack created",
        extra={
            "pack_id": pack_id,
            "status": "planned",
            "slot_count": len(plan.slots),
        },
    )
    logger.debug(
        "Asset pack create detail",
        extra={
            "pack_id": pack_id,
            "digest_prefix": plan.plan_digest[:12],
            "preset_id": plan.preset or "",
        },
    )
    return record


def get_pack(pack_id: str, *, db_path: Path | None = None) -> AssetPackRecord:
    path = db_path or get_project_db_path()
    with get_connection(path) as conn:
        return _load(conn, pack_id)


def list_packs(*, db_path: Path | None = None, limit: int = 100) -> list[AssetPackRecord]:
    path = db_path or get_project_db_path()
    capped = max(1, min(int(limit), 500))
    with get_connection(path) as conn:
        rows = conn.execute(
            """
            SELECT id, name, schema_version, body_json, plan_json, document_revision,
                   universe_id, composer_profile_id, status, created_at, updated_at
            FROM asset_packs
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (capped,),
        ).fetchall()
        return [_row_record(conn, row) for row in rows]


def update_pack_cas(
    pack_id: str,
    *,
    expected_revision: int,
    status: AssetPackStatus | None = None,
    universe_id: str | None = None,
    body_updates: dict[str, Any] | None = None,
    db_path: Path | None = None,
) -> AssetPackRecord:
    """CAS update pack metadata. Does not rewrite plan_json."""
    path = db_path or get_project_db_path()
    now = _utc_now_iso()
    with get_connection(path) as conn:
        current = _load(conn, pack_id)
        if current.document_revision != expected_revision:
            logger.warning(
                "Asset pack CAS conflict",
                extra={
                    "pack_id": pack_id,
                    "code": "asset_pack_conflict",
                    "expected_revision": expected_revision,
                    "actual_revision": current.document_revision,
                },
            )
            raise AssetPackError("asset_pack_conflict")

        new_status = status or current.status
        new_universe = universe_id if universe_id is not None else current.universe_id
        body_payload = current.body.model_dump(mode="json")
        body_payload["status"] = new_status
        body_payload["universe_id"] = new_universe
        body_payload["document_revision"] = expected_revision + 1
        body_payload["updated_at"] = now
        if body_updates:
            body_payload.update(body_updates)
        body_json = _guard_json(body_payload, context="asset_pack_body")

        conn.execute(
            """
            UPDATE asset_packs
            SET body_json = ?, document_revision = ?, status = ?,
                universe_id = ?, updated_at = ?
            WHERE id = ? AND document_revision = ?
            """,
            (
                body_json,
                expected_revision + 1,
                new_status,
                new_universe,
                now,
                pack_id,
                expected_revision,
            ),
        )
        if conn.total_changes == 0:
            raise AssetPackError("asset_pack_conflict")
        conn.commit()
        return _load(conn, pack_id)


def upsert_slot_status(
    pack_id: str,
    slot_id: str,
    *,
    status: str,
    project_id: str | None = None,
    autonomous_run_id: str | None = None,
    adaptive_score_id: str | None = None,
    head_revision_id: str | None = None,
    db_path: Path | None = None,
) -> AssetPackSlotRecordV1:
    """Update one slot row. Called before continuing to the next generate slot."""
    path = db_path or get_project_db_path()
    now = _utc_now_iso()
    with get_connection(path) as conn:
        row = conn.execute(
            """
            SELECT slot_id FROM asset_pack_slots
            WHERE pack_id = ? AND slot_id = ?
            """,
            (pack_id, slot_id),
        ).fetchone()
        if row is None:
            raise AssetPackError("asset_pack_slot_unknown")

        conn.execute(
            """
            UPDATE asset_pack_slots
            SET status = ?,
                project_id = COALESCE(?, project_id),
                autonomous_run_id = COALESCE(?, autonomous_run_id),
                adaptive_score_id = COALESCE(?, adaptive_score_id),
                head_revision_id = COALESCE(?, head_revision_id),
                updated_at = ?
            WHERE pack_id = ? AND slot_id = ?
            """,
            (
                status,
                project_id,
                autonomous_run_id,
                adaptive_score_id,
                head_revision_id,
                now,
                pack_id,
                slot_id,
            ),
        )
        conn.commit()
        slots = _slot_rows(conn, pack_id)
    for slot in slots:
        if slot.slot_id == slot_id:
            logger.info(
                "Asset pack slot status",
                extra={
                    "pack_id": pack_id,
                    "slot_id": slot_id,
                    "status": status,
                    "project_id": project_id or "",
                },
            )
            return slot
    raise AssetPackError("asset_pack_slot_unknown")


def list_slot_records(pack_id: str, *, db_path: Path | None = None) -> list[AssetPackSlotRecordV1]:
    path = db_path or get_project_db_path()
    with get_connection(path) as conn:
        _load(conn, pack_id)
        return _slot_rows(conn, pack_id)


__all__ = [
    "AssetPackRecord",
    "create_pack",
    "get_pack",
    "list_packs",
    "list_slot_records",
    "update_pack_cas",
    "upsert_slot_status",
]
