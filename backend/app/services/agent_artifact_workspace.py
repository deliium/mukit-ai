"""Immutable agent-artifact workspace (trust-boundary service).

Inventory / non-goals checklist (Task 1 freeze):

(a) Reuse existing contracts:
    - ``AgentArtifactV1`` envelope (``agent.artifact.v1``)
    - ``agent.brief.v1`` / ``agent.critique.v1`` / ``agent.workflow_plan.v1``
    - ``composition.plan.v1`` (CompositionPlan; forbidden playable top-level fields)
    - ``composition.analysis.v1`` (MusicAnalysis sidecar; durable = bounded projection)

(b) Forbidden playable field set (shared with plan schemas):
    tracks, events, notes, note_events, musicxml, midi, wav, composition, music, analysis
    Plans and typed agent payloads must never become alternate playable scores.

(c) Agents under ``ai_agents/`` must NOT write SQLite, import this module, or touch
    ``PROJECT_DB_PATH`` / ``project_store`` / ``project_history_store``.
    Durable writes happen only at router / Apply trust boundaries.

(d) No ``composition.v4``. Canonical playable schema remains ``composition.v2``.
    Product V4 = multi-agent orchestration only.

(e) Default staging is session-only (``artifact_log`` in workflow preview) until
    ``multi-agent-apply``. Optional temporary SQLite rows require
    ``project_id`` + ``persist_workspace_artifacts=true``. Promote durable rows
    + ``revision_artifact_links`` in the same transaction as DurableCommit.

APIs: insert_temporary, get_artifact, list_project_artifacts, promote_and_link_revision,
gc_expired_temporary. INSERT-only payloads; never UPDATE payload_json/content_type.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.agent_artifact_settings import load_agent_artifact_settings
from app.ai_agents.artifact_schemas import validate_artifact_payload
from app.ai_agents.errors import (
    ArtifactImmutableViolationError,
    ArtifactNotFoundError,
    ArtifactPayloadRejectedError,
    ArtifactPlayableFieldsForbiddenError,
    ArtifactRetentionRejectedError,
    WorkspaceQuotaExceededError,
)
from app.ai_agents.schemas import AgentArtifactV1
from app.db.connection import get_connection, get_project_db_path
from app.services.persistence_secret_guard import (
    PersistenceSecretError,
    assert_no_secret_fields,
    assert_no_secret_values,
)

logger = logging.getLogger(__name__)

INVENTORY_CHECKLIST_IDS: tuple[str, ...] = (
    "reuse_envelope_brief_critique_plan_analysis",
    "forbidden_playable_field_set",
    "agents_must_not_write_sqlite_or_import_workspace",
    "no_composition_v4",
    "session_only_until_apply_default",
)

_WORKSPACE_IMPLEMENTED = True

LINK_ROLES: frozenset[str] = frozenset(
    {
        "brief",
        "harmony_plan",
        "motif_plan",
        "arrangement_plan",
        "critique",
        "revision_plan",
    }
)

ROLE_EXPECTED_CONTENT_TYPES: dict[str, frozenset[str]] = {
    "brief": frozenset({"agent.brief.v1"}),
    "harmony_plan": frozenset({"agent.harmony_plan.v1"}),
    "motif_plan": frozenset({"agent.motif_plan.v1"}),
    "arrangement_plan": frozenset({"agent.arrangement_plan.v1"}),
    "critique": frozenset({"agent.critique.v1"}),
    "revision_plan": frozenset({"agent.revision_plan.v1"}),
}

AI_ARTIFACT_SUMMARY_SCHEMA = "revision.ai_artifact_summary.v1"


def inventory_checklist() -> tuple[str, ...]:
    logger.debug(
        "Agent artifact workspace inventory checklist",
        extra={"checklist_ids": list(INVENTORY_CHECKLIST_IDS)},
    )
    return INVENTORY_CHECKLIST_IDS


def workspace_writes_enabled() -> bool:
    return _WORKSPACE_IMPLEMENTED


def canonical_json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def canonical_payload_digest(payload: Mapping[str, Any] | dict[str, Any]) -> str:
    material = canonical_json_dumps(dict(payload)).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_iso(value: str) -> datetime:
    cleaned = value.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    return datetime.fromisoformat(cleaned)


def _resolve_path(db_path: Path | str | None) -> Path:
    return Path(db_path) if db_path is not None else get_project_db_path()


def _validate_for_persist(artifact: AgentArtifactV1) -> dict[str, Any]:
    try:
        payload = validate_artifact_payload(artifact.content_type, artifact.payload)
    except ValueError as exc:
        msg = str(exc)
        if "playable" in msg.lower() or "forbidden" in msg.lower():
            raise ArtifactPlayableFieldsForbiddenError(msg) from exc
        raise ArtifactPayloadRejectedError(msg) from exc
    try:
        assert_no_secret_fields(payload, context="agent_artifact_payload")
        assert_no_secret_values(
            canonical_json_dumps(payload),
            field_name="agent_artifact_payload",
        )
    except PersistenceSecretError as exc:
        raise ArtifactPayloadRejectedError(str(exc)) from exc
    return payload


def _temp_count(conn: sqlite3.Connection, project_id: str) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS c FROM agent_artifacts
        WHERE project_id = ? AND retention_class = 'temporary'
        """,
        (project_id,),
    ).fetchone()
    return int(row["c"] if row else 0)


def _insert_row(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    artifact: AgentArtifactV1,
    payload: dict[str, Any],
    retention_class: str,
    expires_at: str | None,
    source_revision_id: str | None = None,
) -> str:
    artifact_id = artifact.artifact_id or str(uuid.uuid4())
    digest = canonical_payload_digest(payload)
    created_at = artifact.created_at or _utc_iso(_utc_now())
    parent_ids = list(artifact.parent_artifact_ids or [])
    depends_on = [e.model_dump(mode="json") for e in (artifact.depends_on or [])]
    provenance = (
        artifact.provenance.model_dump(mode="json") if artifact.provenance is not None else None
    )
    conn.execute(
        """
        INSERT INTO agent_artifacts (
            id, project_id, content_type, kind, producer_agent_id,
            source_revision_id, source_fingerprint, payload_json,
            parent_ids_json, depends_on_json, provenance_json,
            supersedes_artifact_id, retention_class, expires_at,
            content_digest, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            artifact_id,
            project_id,
            artifact.content_type,
            artifact.kind.value if hasattr(artifact.kind, "value") else str(artifact.kind),
            artifact.producer_agent_id,
            source_revision_id or artifact.source_revision_id,
            artifact.source_fingerprint,
            canonical_json_dumps(payload),
            canonical_json_dumps(parent_ids),
            canonical_json_dumps(depends_on),
            canonical_json_dumps(provenance) if provenance is not None else None,
            artifact.supersedes_artifact_id,
            retention_class,
            expires_at,
            digest,
            created_at,
        ),
    )
    return artifact_id


def insert_temporary(
    project_id: str,
    artifact: AgentArtifactV1,
    *,
    conn: sqlite3.Connection | None = None,
    db_path: Path | str | None = None,
) -> str:
    """Insert a temporary workspace row (optional preview persist path only)."""
    started = time.perf_counter()
    settings = load_agent_artifact_settings()
    payload = _validate_for_persist(artifact)
    if artifact.retention_class == "durable":
        raise ArtifactRetentionRejectedError(
            "insert_temporary requires retention_class=temporary",
            artifact_id=artifact.artifact_id,
            project_id=project_id,
        )

    expires_at = artifact.expires_at
    if not expires_at:
        expires_at = _utc_iso(_utc_now() + timedelta(hours=settings.temp_ttl_hours))

    def _do(connection: sqlite3.Connection) -> str:
        gc_expired_temporary(project_id=project_id, conn=connection)
        count = _temp_count(connection, project_id)
        if count >= settings.temp_max_per_project:
            raise WorkspaceQuotaExceededError(
                "temporary artifact quota exceeded",
                project_id=project_id,
                details={"max": settings.temp_max_per_project, "count": count},
            )
        artifact_id = _insert_row(
            connection,
            project_id=project_id,
            artifact=artifact,
            payload=payload,
            retention_class="temporary",
            expires_at=expires_at,
        )
        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "Agent artifact temporary insert",
            extra={
                "project_id_prefix": project_id[:12],
                "artifact_id_prefix": artifact_id[:12],
                "content_type": artifact.content_type[:80],
                "producer_agent_id": artifact.producer_agent_id,
                "retention_class": "temporary",
                "dependency_count": len(artifact.depends_on or []),
                "duration_ms": duration_ms,
            },
        )
        return artifact_id

    if conn is not None:
        return _do(conn)
    with get_connection(_resolve_path(db_path)) as connection:
        return _do(connection)


def _row_to_metadata(row: sqlite3.Row, *, include_payload: bool, max_bytes: int) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "artifact_id": row["id"],
        "project_id": row["project_id"],
        "content_type": row["content_type"],
        "kind": row["kind"],
        "producer_agent_id": row["producer_agent_id"],
        "source_revision_id": row["source_revision_id"],
        "source_fingerprint": row["source_fingerprint"],
        "supersedes_artifact_id": row["supersedes_artifact_id"],
        "retention_class": row["retention_class"],
        "expires_at": row["expires_at"],
        "content_digest": row["content_digest"],
        "created_at": row["created_at"],
        "parent_artifact_ids": json.loads(row["parent_ids_json"] or "[]"),
        "depends_on": json.loads(row["depends_on_json"] or "[]"),
        "provenance": json.loads(row["provenance_json"]) if row["provenance_json"] else None,
    }
    if include_payload:
        raw = row["payload_json"] or "{}"
        if len(raw.encode("utf-8")) > max_bytes:
            meta["payload"] = None
            meta["payload_truncated"] = True
            meta["payload_byte_size"] = len(raw.encode("utf-8"))
        else:
            meta["payload"] = json.loads(raw)
            meta["payload_truncated"] = False
    return meta


def get_artifact(
    project_id: str,
    artifact_id: str,
    *,
    include_payload: bool = False,
    conn: sqlite3.Connection | None = None,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    settings = load_agent_artifact_settings()

    def _do(connection: sqlite3.Connection) -> dict[str, Any]:
        row = connection.execute(
            """
            SELECT * FROM agent_artifacts
            WHERE project_id = ? AND id = ?
            """,
            (project_id, artifact_id),
        ).fetchone()
        if row is None:
            raise ArtifactNotFoundError(
                "artifact not found",
                artifact_id=artifact_id,
                project_id=project_id,
            )
        return _row_to_metadata(
            row,
            include_payload=include_payload,
            max_bytes=settings.payload_inspect_max_bytes,
        )

    if conn is not None:
        return _do(conn)
    with get_connection(_resolve_path(db_path)) as connection:
        return _do(connection)


def list_project_artifacts(
    project_id: str,
    *,
    conn: sqlite3.Connection | None = None,
    db_path: Path | str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Return metadata-only rows (never payloads)."""

    def _do(connection: sqlite3.Connection) -> list[dict[str, Any]]:
        rows = connection.execute(
            """
            SELECT * FROM agent_artifacts
            WHERE project_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (project_id, max(1, min(limit, 1000))),
        ).fetchall()
        return [
            _row_to_metadata(row, include_payload=False, max_bytes=0) for row in rows
        ]

    if conn is not None:
        return _do(conn)
    with get_connection(_resolve_path(db_path)) as connection:
        return _do(connection)


def reject_payload_update(
    project_id: str,
    artifact_id: str,
    *,
    conn: sqlite3.Connection | None = None,
    db_path: Path | str | None = None,
) -> None:
    """Explicit guard — payload UPDATE is forbidden (immutability)."""
    raise ArtifactImmutableViolationError(
        "agent artifact payloads are immutable; insert a new version",
        artifact_id=artifact_id,
        project_id=project_id,
    )


def promote_and_link_revision(
    project_id: str,
    revision_id: str,
    role_map: Mapping[str, Mapping[str, Any] | None],
    envelopes: Sequence[AgentArtifactV1] | Mapping[str, AgentArtifactV1],
    *,
    conn: sqlite3.Connection,
) -> dict[str, str]:
    """INSERT durable rows + revision_artifact_links on the caller's connection.

    Returns role → artifact_id for linked roles. Must run in the same transaction
    as DurableCommit / ApplyAsBranch.
    """
    started = time.perf_counter()
    by_id: dict[str, AgentArtifactV1] = {}
    if isinstance(envelopes, Mapping):
        by_id = dict(envelopes)
    else:
        for env in envelopes:
            by_id[env.artifact_id] = env

    linked: dict[str, str] = {}
    for role, entry in role_map.items():
        if entry is None:
            continue
        if role not in LINK_ROLES:
            raise ArtifactPayloadRejectedError(
                f"unknown artifact role: {role}",
                project_id=project_id,
                details={"role": role},
            )
        artifact_id = str(entry.get("artifact_id") or "")
        content_type = str(entry.get("content_type") or "")
        if not artifact_id:
            raise ArtifactPayloadRejectedError(
                f"role {role} missing artifact_id",
                project_id=project_id,
            )
        expected = ROLE_EXPECTED_CONTENT_TYPES.get(role)
        if expected and content_type and content_type not in expected:
            raise ArtifactPayloadRejectedError(
                f"role {role} content_type mismatch",
                artifact_id=artifact_id,
                project_id=project_id,
                details={"expected": sorted(expected), "got": content_type},
            )
        artifact = by_id.get(artifact_id)
        if artifact is None:
            # Resolve from prior temporary row if present.
            existing = conn.execute(
                "SELECT id, retention_class FROM agent_artifacts WHERE project_id = ? AND id = ?",
                (project_id, artifact_id),
            ).fetchone()
            if existing is None:
                raise ArtifactNotFoundError(
                    "role map artifact not found for promote",
                    artifact_id=artifact_id,
                    project_id=project_id,
                )
            # Already durable or temp: ensure durable + link.
            if existing["retention_class"] != "durable":
                conn.execute(
                    """
                    UPDATE agent_artifacts
                    SET retention_class = 'durable', expires_at = NULL,
                        source_revision_id = ?
                    WHERE id = ? AND project_id = ?
                    """,
                    (revision_id, artifact_id, project_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE agent_artifacts
                    SET source_revision_id = ?
                    WHERE id = ? AND project_id = ? AND source_revision_id IS NULL
                    """,
                    (revision_id, artifact_id, project_id),
                )
        else:
            payload = _validate_for_persist(artifact)
            # Prefer insert; if id already exists as temp, promote retention only.
            existing = conn.execute(
                "SELECT id, retention_class FROM agent_artifacts WHERE project_id = ? AND id = ?",
                (project_id, artifact_id),
            ).fetchone()
            if existing is None:
                _insert_row(
                    conn,
                    project_id=project_id,
                    artifact=artifact,
                    payload=payload,
                    retention_class="durable",
                    expires_at=None,
                    source_revision_id=revision_id,
                )
            elif existing["retention_class"] != "durable":
                # Retention-class promotion only — never rewrite payload_json.
                conn.execute(
                    """
                    UPDATE agent_artifacts
                    SET retention_class = 'durable', expires_at = NULL,
                        source_revision_id = ?
                    WHERE id = ? AND project_id = ?
                    """,
                    (revision_id, artifact_id, project_id),
                )
            # Never UPDATE payload_json / content_type.

        conn.execute(
            """
            INSERT OR REPLACE INTO revision_artifact_links (revision_id, role, artifact_id)
            VALUES (?, ?, ?)
            """,
            (revision_id, role, artifact_id),
        )
        linked[role] = artifact_id

    duration_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "Agent artifacts promoted and linked",
        extra={
            "project_id_prefix": project_id[:12],
            "revision_id_prefix": revision_id[:12],
            "role_keys": sorted(linked.keys()),
            "duration_ms": duration_ms,
        },
    )
    return linked


def gc_expired_temporary(
    project_id: str | None = None,
    *,
    conn: sqlite3.Connection | None = None,
    db_path: Path | str | None = None,
    now: datetime | None = None,
) -> int:
    """Delete expired temporary artifacts (and cascading links)."""
    cutoff = _utc_iso(now or _utc_now())

    def _do(connection: sqlite3.Connection) -> int:
        if project_id:
            cursor = connection.execute(
                """
                DELETE FROM agent_artifacts
                WHERE retention_class = 'temporary'
                  AND project_id = ?
                  AND expires_at IS NOT NULL
                  AND expires_at <= ?
                """,
                (project_id, cutoff),
            )
        else:
            cursor = connection.execute(
                """
                DELETE FROM agent_artifacts
                WHERE retention_class = 'temporary'
                  AND expires_at IS NOT NULL
                  AND expires_at <= ?
                """,
                (cutoff,),
            )
        deleted = int(cursor.rowcount or 0)
        if deleted:
            logger.info(
                "Agent artifact GC deleted temporary rows",
                extra={
                    "deleted_count": deleted,
                    "project_id_prefix": (project_id or "")[:12] or None,
                },
            )
        return deleted

    if conn is not None:
        return _do(conn)
    with get_connection(_resolve_path(db_path)) as connection:
        return _do(connection)


def summarize_revision_artifacts(
    revision_id: str,
    *,
    conn: sqlite3.Connection,
) -> dict[str, Any]:
    """Build ``revision.ai_artifact_summary.v1`` role metadata (no payloads)."""
    rows = conn.execute(
        """
        SELECT l.role, a.id, a.content_type, a.producer_agent_id, a.created_at, a.provenance_json
        FROM revision_artifact_links l
        JOIN agent_artifacts a ON a.id = l.artifact_id
        WHERE l.revision_id = ?
        """,
        (revision_id,),
    ).fetchall()
    roles: dict[str, Any] = {role: None for role in sorted(LINK_ROLES)}
    for row in rows:
        model_id = None
        if row["provenance_json"]:
            try:
                prov = json.loads(row["provenance_json"])
                if isinstance(prov, dict):
                    model_id = prov.get("model_id")
            except json.JSONDecodeError:
                model_id = None
        roles[row["role"]] = {
            "artifact_id": row["id"],
            "content_type": row["content_type"],
            "producer_agent_id": row["producer_agent_id"],
            "created_at": row["created_at"],
            "model_id": model_id,
        }
    present = sum(1 for v in roles.values() if v is not None)
    logger.info(
        "Revision AI artifact summary built",
        extra={"revision_id_prefix": revision_id[:12], "role_count": present},
    )
    logger.debug(
        "Revision AI artifact summary missing roles",
        extra={
            "missing_roles": [k for k, v in roles.items() if v is None],
        },
    )
    return {
        "schema_version": AI_ARTIFACT_SUMMARY_SCHEMA,
        "roles": roles,
    }


logger.info(
    "Agent artifact workspace module loaded",
    extra={
        "checklist_count": len(INVENTORY_CHECKLIST_IDS),
        "writes_enabled": _WORKSPACE_IMPLEMENTED,
        "link_role_count": len(LINK_ROLES),
    },
)
