"""SQLite CRUD for durable ``composer.profile.v1`` preference documents."""

from __future__ import annotations

import json
import logging
import sqlite3
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.composer_profile_schemas import (
    COMPOSER_PROFILE_SCHEMA,
    ComposerProfileError,
    ComposerProfileListItem,
    ComposerProfileV1,
    DerivedPreferenceFields,
    PreferenceFields,
    empty_composer_profile,
)
from app.composer_profile_settings import load_composer_profile_settings
from app.db.connection import get_connection, get_project_db_path
from app.services.persistence_secret_guard import (
    PersistenceSecretError,
    assert_no_secret_fields,
    assert_no_secret_values,
)
from app.services.project_history_store import normalize_branch_name

logger = logging.getLogger(__name__)


def normalize_profile_name(name: str) -> str:
    """NFKC + casefold uniqueness key (same spirit as branch names)."""
    cleaned = " ".join(unicodedata.normalize("NFKC", name).strip().split())
    return normalize_branch_name(cleaned)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _new_profile_id() -> str:
    return f"prof_{uuid.uuid4().hex[:16]}"


def _guard_body(payload: dict[str, Any], *, context: str) -> None:
    try:
        assert_no_secret_fields(payload, context=context)
        for field_name, text in (
            ("name", payload.get("name")),
            ("notes", payload.get("notes")),
        ):
            if isinstance(text, str) and text:
                assert_no_secret_values(text, field_name=field_name)
        body_text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        assert_no_secret_values(body_text, field_name="body_json")
    except PersistenceSecretError as exc:
        logger.warning(
            "Composer profile secret guard rejected payload",
            extra={"code": "composer_profile_forbidden_payload", "context": context},
        )
        raise ComposerProfileError(
            "composer_profile_forbidden_payload",
            str(exc),
            http_status=422,
            details={"cause": exc.code},
        ) from exc


def _row_to_profile(row: sqlite3.Row) -> ComposerProfileV1:
    try:
        body = json.loads(row["body_json"])
        profile = ComposerProfileV1.model_validate(body)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Stored composer profile body failed validation",
            extra={"profile_id": row["id"], "error_type": type(exc).__name__},
        )
        raise ComposerProfileError(
            "composer_profile_invalid",
            "Stored profile body is invalid",
            http_status=500,
            details={"profile_id": row["id"]},
        ) from exc
    # Trust row timestamps / id / name as authoritative over nested drift.
    return profile.model_copy(
        update={
            "id": row["id"],
            "name": row["name"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
    )


def list_profiles(*, db_path: Path | None = None) -> list[ComposerProfileListItem]:
    path = db_path or get_project_db_path()
    with get_connection(path) as conn:
        rows = conn.execute(
            """
            SELECT id, name, updated_at, body_json
            FROM composer_profiles
            ORDER BY updated_at DESC, name ASC
            """
        ).fetchall()
    items: list[ComposerProfileListItem] = []
    for row in rows:
        source_count = 0
        try:
            body = json.loads(row["body_json"])
            sources = body.get("source_projects") if isinstance(body, dict) else None
            if isinstance(sources, list):
                source_count = len(sources)
        except json.JSONDecodeError:
            source_count = 0
        items.append(
            ComposerProfileListItem(
                id=row["id"],
                name=row["name"],
                updated_at=row["updated_at"],
                source_count=source_count,
            )
        )
    logger.debug(
        "Listed composer profiles",
        extra={"count": len(items)},
    )
    return items


def get_profile(profile_id: str, *, db_path: Path | None = None) -> ComposerProfileV1:
    path = db_path or get_project_db_path()
    with get_connection(path) as conn:
        row = conn.execute(
            "SELECT id, name, body_json, created_at, updated_at FROM composer_profiles WHERE id = ?",
            (profile_id,),
        ).fetchone()
    if row is None:
        raise ComposerProfileError(
            "composer_profile_not_found",
            f"Profile {profile_id} not found",
            http_status=404,
            details={"profile_id": profile_id},
        )
    return _row_to_profile(row)


def create_profile(
    *,
    name: str,
    notes: str | None = None,
    explicit: PreferenceFields | None = None,
    derived: DerivedPreferenceFields | None = None,
    source_projects: list[Any] | None = None,
    profile_id: str | None = None,
    db_path: Path | None = None,
) -> ComposerProfileV1:
    settings = load_composer_profile_settings()
    path = db_path or get_project_db_path()
    now = _utc_now_iso()
    pid = profile_id or _new_profile_id()
    profile = empty_composer_profile(
        profile_id=pid,
        name=name,
        created_at=now,
        updated_at=now,
    )
    updates: dict[str, Any] = {}
    if notes is not None:
        updates["notes"] = notes
    if explicit is not None:
        updates["explicit"] = explicit
    if derived is not None:
        updates["derived"] = derived
    if source_projects is not None:
        updates["source_projects"] = source_projects
    if updates:
        profile = profile.model_copy(update=updates)
    # Re-validate after copy.
    profile = ComposerProfileV1.model_validate(profile.model_dump())
    payload = profile.model_dump(mode="json")
    _guard_body(payload, context="composer_profile.create")
    normalized = normalize_profile_name(profile.name)
    body_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    with get_connection(path) as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM composer_profiles").fetchone()["c"]
        if count >= settings.max_profiles:
            logger.warning(
                "Composer profile cap exceeded",
                extra={
                    "code": "composer_profile_cap_exceeded",
                    "count": count,
                    "max_profiles": settings.max_profiles,
                },
            )
            raise ComposerProfileError(
                "composer_profile_cap_exceeded",
                f"Maximum profiles ({settings.max_profiles}) reached",
                http_status=422,
                details={"max_profiles": settings.max_profiles, "count": count},
            )
        try:
            conn.execute(
                """
                INSERT INTO composer_profiles (
                    id, name, normalized_name, schema_version, body_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    profile.id,
                    profile.name,
                    normalized,
                    COMPOSER_PROFILE_SCHEMA,
                    body_json,
                    profile.created_at,
                    profile.updated_at,
                ),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            logger.warning(
                "Composer profile name conflict on create",
                extra={"code": "composer_profile_name_conflict", "name_len": len(profile.name)},
            )
            raise ComposerProfileError(
                "composer_profile_name_conflict",
                "A profile with this name already exists",
                http_status=409,
                details={"name_len": len(profile.name)},
            ) from exc

    logger.info(
        "Composer profile created",
        extra={"profile_id": profile.id, "name_len": len(profile.name)},
    )
    return profile


def update_profile(
    profile_id: str,
    *,
    expected_updated_at: str,
    name: str | None = None,
    notes: str | None = ...,  # type: ignore[assignment]
    explicit: PreferenceFields | None = None,
    derived: DerivedPreferenceFields | None = None,
    source_projects: list[Any] | None = None,
    replace_body: bool = False,
    db_path: Path | None = None,
) -> ComposerProfileV1:
    """Replace/edit profile. Requires CAS ``expected_updated_at``."""
    path = db_path or get_project_db_path()
    with get_connection(path) as conn:
        row = conn.execute(
            "SELECT id, name, body_json, created_at, updated_at, normalized_name "
            "FROM composer_profiles WHERE id = ?",
            (profile_id,),
        ).fetchone()
        if row is None:
            raise ComposerProfileError(
                "composer_profile_not_found",
                f"Profile {profile_id} not found",
                http_status=404,
                details={"profile_id": profile_id},
            )
        if row["updated_at"] != expected_updated_at:
            logger.warning(
                "Composer profile CAS conflict",
                extra={
                    "code": "composer_profile_conflict",
                    "profile_id": profile_id,
                },
            )
            raise ComposerProfileError(
                "composer_profile_conflict",
                "expected_updated_at does not match stored profile",
                http_status=409,
                details={
                    "profile_id": profile_id,
                    "expected_updated_at": expected_updated_at,
                    "actual_updated_at": row["updated_at"],
                },
            )

        current = _row_to_profile(row)
        now = _utc_now_iso()
        updates: dict[str, Any] = {"updated_at": now}
        if name is not None:
            updates["name"] = name
        if notes is not ...:
            updates["notes"] = notes
        if explicit is not None or replace_body:
            updates["explicit"] = explicit if explicit is not None else PreferenceFields()
        if derived is not None or replace_body:
            updates["derived"] = (
                derived if derived is not None else DerivedPreferenceFields()
            )
        if source_projects is not None or replace_body:
            updates["source_projects"] = source_projects if source_projects is not None else []

        updated = current.model_copy(update=updates)
        updated = ComposerProfileV1.model_validate(updated.model_dump())
        payload = updated.model_dump(mode="json")
        _guard_body(payload, context="composer_profile.update")
        normalized = normalize_profile_name(updated.name)
        body_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

        try:
            conn.execute(
                """
                UPDATE composer_profiles
                SET name = ?, normalized_name = ?, body_json = ?, updated_at = ?
                WHERE id = ? AND updated_at = ?
                """,
                (
                    updated.name,
                    normalized,
                    body_json,
                    updated.updated_at,
                    profile_id,
                    expected_updated_at,
                ),
            )
            if conn.total_changes == 0:
                raise ComposerProfileError(
                    "composer_profile_conflict",
                    "CAS update lost race",
                    http_status=409,
                    details={"profile_id": profile_id},
                )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            logger.warning(
                "Composer profile name conflict on update",
                extra={"code": "composer_profile_name_conflict", "profile_id": profile_id},
            )
            raise ComposerProfileError(
                "composer_profile_name_conflict",
                "A profile with this name already exists",
                http_status=409,
                details={"profile_id": profile_id},
            ) from exc

    logger.info(
        "Composer profile updated",
        extra={"profile_id": profile_id, "name_len": len(updated.name)},
    )
    return updated


def delete_profile(profile_id: str, *, db_path: Path | None = None) -> None:
    path = db_path or get_project_db_path()
    with get_connection(path) as conn:
        cur = conn.execute("DELETE FROM composer_profiles WHERE id = ?", (profile_id,))
        conn.commit()
        if cur.rowcount == 0:
            raise ComposerProfileError(
                "composer_profile_not_found",
                f"Profile {profile_id} not found",
                http_status=404,
                details={"profile_id": profile_id},
            )
    logger.info("Composer profile deleted", extra={"profile_id": profile_id})


def replace_profile_body(
    profile_id: str,
    profile: ComposerProfileV1,
    *,
    expected_updated_at: str,
    db_path: Path | None = None,
) -> ComposerProfileV1:
    """Full body replace used by derive-save / import / promote helpers."""
    return update_profile(
        profile_id,
        expected_updated_at=expected_updated_at,
        name=profile.name,
        notes=profile.notes,
        explicit=profile.explicit,
        derived=profile.derived,
        source_projects=list(profile.source_projects),
        replace_body=True,
        db_path=db_path,
    )


def count_profiles(*, db_path: Path | None = None) -> int:
    path = db_path or get_project_db_path()
    with get_connection(path) as conn:
        return int(conn.execute("SELECT COUNT(*) AS c FROM composer_profiles").fetchone()["c"])
