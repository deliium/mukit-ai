"""SQLite persistence for ExecutionNode registration documents.

Stores last registration JSON and CAS revision in ``PROJECT_DB_PATH``.
Does not import Composition, project_store, or adaptive engine modules.
Live availability TTL is owned by the controller service, not this store.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from app.db.connection import get_connection, get_project_db_path
from app.execution_node_schemas import ExecutionNodeError, ExecutionNodeV1

logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _resolve(db_path: Path | str | None) -> Path:
    return Path(db_path) if db_path is not None else get_project_db_path()


def _dump(node: ExecutionNodeV1) -> str:
    return json.dumps(node.model_dump(mode="json"), separators=(",", ":"), sort_keys=True)


def get_node(node_id: str, *, db_path: Path | str | None = None) -> ExecutionNodeV1 | None:
    """Return one durable node document, or None."""
    path = _resolve(db_path)
    logger.debug("execution node get", extra={"node_id": node_id})
    with get_connection(path) as conn:
        row = conn.execute(
            "SELECT body_json FROM execution_nodes WHERE node_id = ?",
            (node_id,),
        ).fetchone()
    if row is None:
        return None
    return ExecutionNodeV1.model_validate(json.loads(row["body_json"]))


def list_nodes(*, db_path: Path | str | None = None) -> list[ExecutionNodeV1]:
    """Return every durable node document ordered by node_id."""
    path = _resolve(db_path)
    with get_connection(path) as conn:
        rows = conn.execute(
            "SELECT body_json FROM execution_nodes ORDER BY node_id"
        ).fetchall()
    nodes = [ExecutionNodeV1.model_validate(json.loads(row["body_json"])) for row in rows]
    logger.debug("execution node list", extra={"node_count": len(nodes)})
    return nodes


def upsert_node(node: ExecutionNodeV1, *, db_path: Path | str | None = None) -> ExecutionNodeV1:
    """Insert or replace the durable registration document."""
    path = _resolve(db_path)
    updated_at = _utc_now()
    body = _dump(node)
    with get_connection(path) as conn:
        conn.execute(
            """
            INSERT INTO execution_nodes (node_id, body_json, document_revision, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(node_id) DO UPDATE SET
                body_json = excluded.body_json,
                document_revision = excluded.document_revision,
                updated_at = excluded.updated_at
            """,
            (node.node_id, body, node.document_revision, updated_at),
        )
    logger.info(
        "execution node upserted",
        extra={"node_id": node.node_id, "document_revision": node.document_revision},
    )
    return node


def cas_update_node(
    node: ExecutionNodeV1,
    *,
    expected_revision: int,
    db_path: Path | str | None = None,
) -> ExecutionNodeV1:
    """CAS-update when the stored revision equals ``expected_revision``.

    ``node.document_revision`` must be the new revision (typically
    ``expected_revision + 1``).
    """
    path = _resolve(db_path)
    updated_at = _utc_now()
    body = _dump(node)
    with get_connection(path) as conn:
        cursor = conn.execute(
            """
            UPDATE execution_nodes
            SET body_json = ?, document_revision = ?, updated_at = ?
            WHERE node_id = ? AND document_revision = ?
            """,
            (body, node.document_revision, updated_at, node.node_id, expected_revision),
        )
        if cursor.rowcount != 1:
            logger.debug(
                "execution node CAS conflict",
                extra={
                    "node_id": node.node_id,
                    "expected_revision": expected_revision,
                    "document_revision": node.document_revision,
                },
            )
            raise ExecutionNodeError(
                "execution_node_conflict",
                "Execution node revision conflict.",
                details={"node_id": node.node_id, "expected_revision": expected_revision},
            )
    logger.info(
        "execution node CAS updated",
        extra={"node_id": node.node_id, "document_revision": node.document_revision},
    )
    return node


def delete_node(node_id: str, *, db_path: Path | str | None = None) -> None:
    """Delete a durable node row. Missing ids raise not_found."""
    path = _resolve(db_path)
    with get_connection(path) as conn:
        cursor = conn.execute(
            "DELETE FROM execution_nodes WHERE node_id = ?",
            (node_id,),
        )
        if cursor.rowcount != 1:
            logger.info(
                "execution node delete missed",
                extra={"node_id": node_id, "code": "execution_node_not_found"},
            )
            raise ExecutionNodeError(
                "execution_node_not_found",
                "Execution node was not found.",
                details={"node_id": node_id},
            )
    logger.info("execution node deleted", extra={"node_id": node_id})


def count_nodes(*, db_path: Path | str | None = None) -> int:
    path = _resolve(db_path)
    with get_connection(path) as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM execution_nodes").fetchone()
    return int(row["n"] if row is not None else 0)
