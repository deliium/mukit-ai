"""SQLite desired state for discovered plugins. Does not import plugin code."""

from __future__ import annotations

import json
import logging
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

logger = logging.getLogger(__name__)

DESIRED_STATES = frozenset({"installed", "enabled", "disabled"})


@dataclass(frozen=True)
class PluginInstallation:
    """One durable install row. ``config`` is the user override object."""

    plugin_id: str
    installed_version: str
    desired_state: str
    config: dict[str, Any]
    last_error_code: str | None
    created_at: str
    updated_at: str


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _db_configured() -> bool:
    try:
        return bool(get_project_db_path())
    except Exception:
        return False


def _guard_config(config: dict[str, Any]) -> None:
    try:
        assert_no_secret_fields(config, context="plugin_installation")
        body_text = json.dumps(config, ensure_ascii=False, separators=(",", ":"))
        assert_no_secret_values(body_text, field_name="config_json")
    except PersistenceSecretError as exc:
        logger.warning(
            "plugin installation secret guard rejected",
            extra={"code": "persistence_secret_rejected", "db_configured": _db_configured()},
        )
        raise PersistenceSecretError(
            "persistence_secret_rejected",
            code="persistence_secret_rejected",
        ) from exc


def _row_to_installation(row: sqlite3.Row) -> PluginInstallation:
    try:
        config = json.loads(row["config_json"])
    except json.JSONDecodeError:
        config = {}
    if not isinstance(config, dict):
        config = {}
    return PluginInstallation(
        plugin_id=row["plugin_id"],
        installed_version=row["installed_version"],
        desired_state=row["desired_state"],
        config=config,
        last_error_code=row["last_error_code"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def get_installation(plugin_id: str, *, db_path: Path | None = None) -> PluginInstallation | None:
    """Return one installation row, or None when the id was never installed."""
    logger.debug(
        "plugin installation get",
        extra={"plugin_id": plugin_id, "db_configured": _db_configured()},
    )
    with get_connection(db_path) as connection:
        row = connection.execute(
            "SELECT * FROM plugin_installations WHERE plugin_id = ?",
            (plugin_id,),
        ).fetchone()
    if row is None:
        return None
    return _row_to_installation(row)


def list_installations(*, db_path: Path | None = None) -> list[PluginInstallation]:
    """Return every installation row. Debug-logs the count, not config."""
    with get_connection(db_path) as connection:
        rows = connection.execute(
            "SELECT * FROM plugin_installations ORDER BY plugin_id"
        ).fetchall()
    installations = [_row_to_installation(row) for row in rows]
    logger.debug(
        "plugin installation list",
        extra={
            "row_count": len(installations),
            "db_configured": _db_configured(),
        },
    )
    return installations


def upsert_installation(
    *,
    plugin_id: str,
    installed_version: str,
    desired_state: str,
    config: dict[str, Any] | None = None,
    last_error_code: str | None = None,
    db_path: Path | None = None,
) -> PluginInstallation:
    """Insert or replace desired state. Never deletes rows and never logs config."""
    if desired_state not in DESIRED_STATES:
        raise ValueError("desired_state is not a lifecycle value")
    payload = dict(config or {})
    _guard_config(payload)
    now = _utc_now_iso()
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    logger.info(
        "plugin installation upsert",
        extra={
            "plugin_id": plugin_id,
            "desired_state": desired_state,
            "version": installed_version,
            "db_configured": _db_configured(),
        },
    )
    with get_connection(db_path) as connection:
        existing = connection.execute(
            "SELECT created_at FROM plugin_installations WHERE plugin_id = ?",
            (plugin_id,),
        ).fetchone()
        created_at = existing["created_at"] if existing is not None else now
        connection.execute(
            """
            INSERT INTO plugin_installations (
                plugin_id, installed_version, desired_state, config_json,
                last_error_code, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(plugin_id) DO UPDATE SET
                installed_version = excluded.installed_version,
                desired_state = excluded.desired_state,
                config_json = excluded.config_json,
                last_error_code = excluded.last_error_code,
                updated_at = excluded.updated_at
            """,
            (plugin_id, installed_version, desired_state, encoded, last_error_code, created_at, now),
        )
        connection.commit()
        row = connection.execute(
            "SELECT * FROM plugin_installations WHERE plugin_id = ?",
            (plugin_id,),
        ).fetchone()
    if row is None:
        logger.error(
            "plugin installation upsert missing row",
            extra={"plugin_id": plugin_id, "db_configured": _db_configured()},
        )
        raise RuntimeError("plugin installation upsert failed")
    return _row_to_installation(row)


def clear_last_error(plugin_id: str, *, db_path: Path | None = None) -> None:
    """Clear ``last_error_code`` after a successful enable. No-op when the row is absent."""
    logger.debug(
        "plugin installation clear error",
        extra={"plugin_id": plugin_id, "db_configured": _db_configured()},
    )
    with get_connection(db_path) as connection:
        connection.execute(
            """
            UPDATE plugin_installations
            SET last_error_code = NULL, updated_at = ?
            WHERE plugin_id = ?
            """,
            (_utc_now_iso(), plugin_id),
        )
        connection.commit()
