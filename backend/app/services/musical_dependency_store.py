"""SQLite rows for ``musical.dependency.edge.v1``.

Does not import ``project_store``, ``musical_universe_store``, or
``composition_schemas``. A caller-owned connection is not committed here.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

from app.db.connection import get_connection, get_project_db_path
from app.musical_dependency_schemas import (
    DEPENDENCY_EDGE_SCHEMA,
    MusicalDependencyEdgeV1,
    MusicalDependencyError,
    MUSICAL_DEPENDENCY_ERROR_CODES,
    downstream_node_key,
    parse_musical_dependency_edge,
    reject_embedded_note_material,
    upstream_node_key,
)
from app.services.composition_snapshot_encoding import snapshot_fingerprint_log_prefix

logger = logging.getLogger(__name__)

PROJECT_EDGE_CAP = 256
UNIVERSE_EDGE_CAP = 512

_COLUMNS = (
    "id",
    "schema_version",
    "dependency_type",
    "upstream_kind",
    "downstream_kind",
    "universe_id",
    "upstream_project_id",
    "downstream_project_id",
    "upstream_theme_id",
    "variant_id",
    "motif_id",
    "occurrence_id",
    "source_motif_id",
    "source_occurrence_id",
    "upstream_revision_id",
    "downstream_revision_id",
    "downstream_asset_id",
    "upstream_fingerprint",
    "downstream_fingerprint",
    "created_at",
    "upstream_node_key",
    "downstream_node_key",
)


def _refuse(code: str, *, http_status: int = 422, details: dict[str, Any] | None = None) -> MusicalDependencyError:
    logger.warning("Dependency edge refused", extra={"code": code})
    return MusicalDependencyError(
        code,
        MUSICAL_DEPENDENCY_ERROR_CODES.get(code, MUSICAL_DEPENDENCY_ERROR_CODES["dependency_invalid"]),
        http_status=http_status,
        details=details,
    )


def _row_edge(row: sqlite3.Row) -> MusicalDependencyEdgeV1:
    payload = {column: row[column] for column in _COLUMNS if column not in {"upstream_node_key", "downstream_node_key"}}
    return parse_musical_dependency_edge(payload)


def _reject_cycle(
    conn: sqlite3.Connection,
    upstream_key: str,
    downstream_key: str,
) -> None:
    """Raise when ``downstream_key`` already reaches ``upstream_key``.

    Walk edges backward: a row whose downstream node is the current node
    points at an ancestor. A self-edge is a cycle before the walk.
    """
    if upstream_key == downstream_key:
        raise _refuse("dependency_cycle")
    seen: set[str] = set()
    stack = [upstream_key]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        rows = conn.execute(
            """
            SELECT upstream_node_key
            FROM musical_dependency_edges
            WHERE downstream_node_key = ?
            """,
            (current,),
        ).fetchall()
        for row in rows:
            ancestor = str(row["upstream_node_key"])
            if ancestor == downstream_key:
                raise _refuse("dependency_cycle")
            stack.append(ancestor)


def _project_ids(edge: MusicalDependencyEdgeV1) -> list[str]:
    found: list[str] = []
    for project_id in (edge.upstream_project_id, edge.downstream_project_id):
        if isinstance(project_id, str) and project_id and project_id not in found:
            found.append(project_id)
    return found


def _reject_caps(conn: sqlite3.Connection, edge: MusicalDependencyEdgeV1) -> None:
    for project_id in _project_ids(edge):
        row = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM musical_dependency_edges
            WHERE upstream_project_id = ? OR downstream_project_id = ?
            """,
            (project_id, project_id),
        ).fetchone()
        if row is not None and int(row["n"]) >= PROJECT_EDGE_CAP:
            raise _refuse("dependency_graph_limit")
    if edge.universe_id:
        row = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM musical_dependency_edges
            WHERE universe_id = ?
            """,
            (edge.universe_id,),
        ).fetchone()
        if row is not None and int(row["n"]) >= UNIVERSE_EDGE_CAP:
            raise _refuse("dependency_graph_limit")


def _existing_id(
    conn: sqlite3.Connection,
    dependency_type: str,
    upstream_key: str,
    downstream_key: str,
) -> str | None:
    row = conn.execute(
        """
        SELECT id
        FROM musical_dependency_edges
        WHERE dependency_type = ? AND upstream_node_key = ? AND downstream_node_key = ?
        """,
        (dependency_type, upstream_key, downstream_key),
    ).fetchone()
    if row is None:
        return None
    return str(row["id"])


def _insert(conn: sqlite3.Connection, edge: MusicalDependencyEdgeV1, upstream_key: str, downstream_key: str) -> None:
    conn.execute(
        f"""
        INSERT INTO musical_dependency_edges ({", ".join(_COLUMNS)})
        VALUES ({", ".join("?" for _ in _COLUMNS)})
        """,
        (
            edge.id,
            edge.schema_version,
            edge.dependency_type,
            edge.upstream_kind,
            edge.downstream_kind,
            edge.universe_id,
            edge.upstream_project_id,
            edge.downstream_project_id,
            edge.upstream_theme_id,
            edge.variant_id,
            edge.motif_id,
            edge.occurrence_id,
            edge.source_motif_id,
            edge.source_occurrence_id,
            edge.upstream_revision_id,
            edge.downstream_revision_id,
            edge.downstream_asset_id,
            edge.upstream_fingerprint,
            edge.downstream_fingerprint,
            edge.created_at,
            upstream_key,
            downstream_key,
        ),
    )


def _apply(conn: sqlite3.Connection, edge: MusicalDependencyEdgeV1) -> MusicalDependencyEdgeV1:
    reject_embedded_note_material(edge.model_dump(mode="json"))
    upstream_key = upstream_node_key(edge)
    downstream_key = downstream_node_key(edge)
    existing = _existing_id(conn, edge.dependency_type, upstream_key, downstream_key)
    _reject_cycle(conn, upstream_key, downstream_key)
    if existing is not None:
        conn.execute(
            """
            UPDATE musical_dependency_edges
            SET upstream_fingerprint = ?
            WHERE id = ?
            """,
            (edge.upstream_fingerprint, existing),
        )
        logger.info(
            "Dependency edge fingerprint updated",
            extra={
                "edge_id": existing,
                "dependency_type": edge.dependency_type,
                "upstream_fingerprint_prefix": snapshot_fingerprint_log_prefix(edge.upstream_fingerprint),
            },
        )
        return get_dependency_edge(existing, connection=conn)
    _reject_caps(conn, edge)
    _insert(conn, edge, upstream_key, downstream_key)
    logger.info(
        "Dependency edge inserted",
        extra={
            "edge_id": edge.id,
            "dependency_type": edge.dependency_type,
            "upstream_fingerprint_prefix": snapshot_fingerprint_log_prefix(edge.upstream_fingerprint),
        },
    )
    return edge


def record_dependency_edge(
    connection: sqlite3.Connection | None,
    edge: MusicalDependencyEdgeV1 | dict[str, Any],
    *,
    db_path: Path | str | None = None,
) -> MusicalDependencyEdgeV1:
    """Insert an edge, or refresh ``upstream_fingerprint`` when the endpoints exist.

    Does not commit a caller-owned connection. A failure leaves that transaction
    for the caller to roll back.
    """
    logger.debug(
        "Recording dependency edge",
        extra={"uses_caller_connection": connection is not None},
    )
    parsed = edge if isinstance(edge, MusicalDependencyEdgeV1) else parse_musical_dependency_edge(edge)
    if parsed.schema_version != DEPENDENCY_EDGE_SCHEMA:
        raise _refuse("dependency_invalid")
    if connection is not None:
        return _apply(connection, parsed)
    path = Path(db_path) if db_path is not None else get_project_db_path()
    with get_connection(path) as conn:
        return _apply(conn, parsed)


def get_dependency_edge(
    edge_id: str,
    *,
    connection: sqlite3.Connection | None = None,
    db_path: Path | str | None = None,
) -> MusicalDependencyEdgeV1:
    """Load one edge or raise ``dependency_not_found``."""

    def _load(conn: sqlite3.Connection) -> MusicalDependencyEdgeV1:
        row = conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM musical_dependency_edges WHERE id = ?",
            (edge_id,),
        ).fetchone()
        if row is None:
            raise _refuse(
                "dependency_not_found",
                http_status=404,
                details={"edge_id": edge_id},
            )
        return _row_edge(row)

    if connection is not None:
        return _load(connection)
    path = Path(db_path) if db_path is not None else get_project_db_path()
    with get_connection(path) as conn:
        return _load(conn)


def list_dependency_edges(
    conn: sqlite3.Connection,
    *,
    universe_id: str | None = None,
    theme_id: str | None = None,
    project_id: str | None = None,
) -> list[MusicalDependencyEdgeV1]:
    """Read edges for one universe, theme, or project. No score load."""
    clauses: list[str] = []
    params: list[str] = []
    if universe_id is not None:
        clauses.append("universe_id = ?")
        params.append(universe_id)
    if theme_id is not None:
        clauses.append("upstream_theme_id = ?")
        params.append(theme_id)
    if project_id is not None:
        clauses.append("(upstream_project_id = ? OR downstream_project_id = ?)")
        params.extend((project_id, project_id))
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"""
        SELECT {", ".join(_COLUMNS)}
        FROM musical_dependency_edges
        {where}
        ORDER BY created_at ASC, id ASC
        """,
        params,
    ).fetchall()
    return [_row_edge(row) for row in rows]


def list_edges_pointing_at(conn: sqlite3.Connection, node_key: str) -> list[sqlite3.Row]:
    """Rows whose upstream is ``node_key`` (downstream hop)."""
    return list(
        conn.execute(
            f"""
            SELECT {", ".join(_COLUMNS)}
            FROM musical_dependency_edges
            WHERE upstream_node_key = ?
            ORDER BY created_at ASC, id ASC
            """,
            (node_key,),
        ).fetchall()
    )


def set_upstream_fingerprint(
    conn: sqlite3.Connection,
    edge_id: str,
    fingerprint: str,
) -> MusicalDependencyEdgeV1:
    """Replace one upstream digest. Does not commit."""
    updated = conn.execute(
        """
        UPDATE musical_dependency_edges
        SET upstream_fingerprint = ?
        WHERE id = ?
        """,
        (fingerprint, edge_id),
    )
    if updated.rowcount != 1:
        raise _refuse("dependency_not_found", http_status=404, details={"edge_id": edge_id})
    logger.info(
        "Dependency edge fingerprint updated",
        extra={
            "edge_id": edge_id,
            "upstream_fingerprint_prefix": snapshot_fingerprint_log_prefix(fingerprint),
        },
    )
    return get_dependency_edge(edge_id, connection=conn)


def delete_edges_for_downstream_project(conn: sqlite3.Connection, project_id: str) -> int:
    """Remove rows whose downstream project is ``project_id``. Upstream-only rows stay."""
    cursor = conn.execute(
        "DELETE FROM musical_dependency_edges WHERE downstream_project_id = ?",
        (project_id,),
    )
    deleted = int(cursor.rowcount)
    logger.info(
        "Dependency edges deleted for downstream project",
        extra={"project_id": project_id, "deleted_count": deleted},
    )
    return deleted


def delete_edges_for_universe(conn: sqlite3.Connection, universe_id: str) -> int:
    """Remove rows that name this universe. Member scores stay."""
    cursor = conn.execute(
        "DELETE FROM musical_dependency_edges WHERE universe_id = ?",
        (universe_id,),
    )
    deleted = int(cursor.rowcount)
    logger.info(
        "Dependency edges deleted for universe",
        extra={"universe_id": universe_id, "deleted_count": deleted},
    )
    return deleted


def count_dependency_edges(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) AS n FROM musical_dependency_edges").fetchone()
    return int(row["n"]) if row is not None else 0
