"""Read-only impact for one theme.

Status is computed from live fingerprints. This module does not reuse a theme,
enqueue a render, or write ``tracks[].events[]``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.audio_recovery_settings import load_audio_recovery_settings
from app.composition_schemas import CompositionV2
from app.db.connection import get_connection, get_project_db_path
from app.musical_dependency_schemas import (
    MusicalDependencyEdgeV1,
    MusicalDependencyError,
    MUSICAL_DEPENDENCY_ERROR_CODES,
    MusicalDependencyGraphV1,
    MusicalDependencyImpactV1,
    MusicalDependencyUpdateOfferV1,
    downstream_node_key,
    parse_musical_dependency_graph,
    parse_musical_dependency_impact,
    upstream_node_key,
)
from app.services.composition_snapshot_encoding import (
    composition_snapshot_fingerprint,
    motif_occurrence_fingerprint,
    snapshot_fingerprint_log_prefix,
)
from app.services.musical_dependency_store import (
    get_dependency_edge,
    list_dependency_edges,
    set_upstream_fingerprint,
)
from app.services.musical_universe_store import get_universe, get_universe_for_project
from app.musical_universe_schemas import MusicalUniverseError, MusicalUniverseV1
from app.services.project_store import ProjectNotFoundError, get_project

logger = logging.getLogger(__name__)

_WALK_CAP = 512
_DEPENDENT_CAP = 256


def _error(code: str, *, http_status: int = 422) -> MusicalDependencyError:
    return MusicalDependencyError(
        code,
        MUSICAL_DEPENDENCY_ERROR_CODES.get(code, MUSICAL_DEPENDENCY_ERROR_CODES["dependency_invalid"]),
        http_status=http_status,
    )


def _load_composition(project_id: str, *, db_path: Path) -> CompositionV2 | None:
    try:
        record = get_project(project_id, db_path=db_path)
    except ProjectNotFoundError:
        logger.debug("Dependency upstream project missing", extra={"project_id": project_id})
        return None
    if not record.composition_json:
        return None
    try:
        payload = json.loads(record.composition_json)
        return CompositionV2.model_validate(payload)
    except (json.JSONDecodeError, ValidationError):
        logger.debug("Dependency upstream composition unreadable", extra={"project_id": project_id})
        return None


def _score_fingerprint(project_id: str, *, db_path: Path) -> str | None:
    composition = _load_composition(project_id, db_path=db_path)
    if composition is None:
        return None
    return composition_snapshot_fingerprint(composition)


def _occurrence_events(
    composition: CompositionV2,
    motif_id: str,
    occurrence_id: str,
) -> list[Any] | None:
    for motif in composition.motifs or []:
        if motif.id != motif_id:
            continue
        for occurrence in motif.occurrences:
            if occurrence.id != occurrence_id:
                continue
            by_id = {event.id: event for track in composition.tracks for event in track.events}
            ordered: list[Any] = []
            for event_id in occurrence.event_ids:
                event = by_id.get(event_id)
                if event is None:
                    return None
                ordered.append(event)
            return ordered
    return None


def _revision_fingerprint(conn: sqlite3.Connection, project_id: str | None, revision_id: str | None) -> str | None:
    if not project_id or not revision_id:
        return None
    row = conn.execute(
        """
        SELECT snapshot_fingerprint
        FROM project_revisions
        WHERE id = ? AND project_id = ?
        """,
        (revision_id, project_id),
    ).fetchone()
    if row is None:
        return None
    fingerprint = str(row["snapshot_fingerprint"])
    if len(fingerprint) == 64 and all(char in "0123456789abcdef" for char in fingerprint):
        return fingerprint
    return fingerprint


def _asset_fingerprint(conn: sqlite3.Connection, asset_id: str | None) -> str | None:
    if not asset_id:
        return None
    row = conn.execute(
        "SELECT relpath FROM audio_recovery_assets WHERE id = ?",
        (asset_id,),
    ).fetchone()
    if row is None:
        return None
    settings = load_audio_recovery_settings()
    path = settings.asset_root / str(row["relpath"])
    if not path.is_file():
        logger.debug("Dependency audio asset file missing", extra={"asset_id": asset_id})
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def current_upstream_fingerprint(
    conn: sqlite3.Connection,
    edge: MusicalDependencyEdgeV1,
    *,
    theme_source_project_id: str | None,
    db_path: Path,
) -> str | None:
    """Fingerprint the impact read would compute now. ``None`` means missing."""
    if edge.dependency_type == "variation_of" and edge.upstream_kind == "theme":
        if not theme_source_project_id:
            return None
        return _score_fingerprint(theme_source_project_id, db_path=db_path)
    if edge.dependency_type == "motif_derived_from" and edge.upstream_kind == "theme":
        if not theme_source_project_id:
            return None
        return _score_fingerprint(theme_source_project_id, db_path=db_path)
    if edge.dependency_type in {"arrangement_of", "variation_of"} and edge.upstream_kind == "revision":
        return _revision_fingerprint(conn, edge.upstream_project_id, edge.upstream_revision_id)
    if edge.dependency_type == "arrangement_of":
        return _revision_fingerprint(conn, edge.upstream_project_id, edge.upstream_revision_id)
    if edge.dependency_type == "rendered_from":
        if not edge.upstream_project_id:
            return None
        return _score_fingerprint(edge.upstream_project_id, db_path=db_path)
    if edge.dependency_type == "transcribed_from":
        return _asset_fingerprint(conn, edge.downstream_asset_id)
    if edge.dependency_type == "reference_conditioned_by":
        if not edge.upstream_project_id:
            return None
        return _score_fingerprint(edge.upstream_project_id, db_path=db_path)
    if edge.dependency_type == "motif_derived_from":
        if not edge.upstream_project_id:
            return None
        same_project = edge.upstream_project_id == edge.downstream_project_id
        if not same_project:
            return _score_fingerprint(edge.upstream_project_id, db_path=db_path)
        composition = _load_composition(edge.upstream_project_id, db_path=db_path)
        if composition is None or not edge.source_motif_id or not edge.source_occurrence_id:
            return None
        events = _occurrence_events(composition, edge.source_motif_id, edge.source_occurrence_id)
        if events is None:
            return None
        return motif_occurrence_fingerprint(events)
    return None


def _own_status(
    edge: MusicalDependencyEdgeV1,
    live: str | None,
) -> str:
    if edge.dependency_type in {"arrangement_of", "variation_of"} and edge.upstream_kind == "revision":
        return "fresh" if live is not None else "missing"
    if edge.dependency_type == "arrangement_of" and edge.upstream_kind == "motif_occurrence":
        return "fresh" if live is not None else "missing"
    if live is None:
        return "missing"
    if edge.dependency_type == "variation_of" and edge.upstream_kind == "revision":
        return "fresh"
    if live != edge.upstream_fingerprint:
        return "stale"
    return "fresh"


def _offer(edge: MusicalDependencyEdgeV1, *, status: str) -> MusicalDependencyUpdateOfferV1:
    action = "review_only"
    if status != "missing":
        if edge.dependency_type == "variation_of" and edge.universe_id and edge.variant_id:
            action = "reuse_theme"
        elif edge.dependency_type == "motif_derived_from":
            action = "open_motif"
        elif edge.dependency_type == "arrangement_of":
            action = "open_arrangement"
        elif edge.dependency_type == "variation_of":
            action = "open_development"
        elif edge.dependency_type == "rendered_from":
            action = "open_neural_render"
        elif edge.dependency_type == "transcribed_from":
            action = "open_transcription"
        elif edge.dependency_type == "reference_conditioned_by":
            action = "open_reference"
    project_id = edge.upstream_project_id if action == "open_reference" else edge.downstream_project_id
    return MusicalDependencyUpdateOfferV1(
        dependency_type=edge.dependency_type,
        action=action,
        edge_id=edge.id,
        universe_id=edge.universe_id,
        theme_id=edge.upstream_theme_id,
        variant_id=edge.variant_id,
        project_id=project_id,
    )


def _label(edge: MusicalDependencyEdgeV1, universe: MusicalUniverseV1 | None) -> str:
    if edge.variant_id and universe is not None:
        for theme in universe.themes:
            for variant in theme.variants:
                if variant.id == edge.variant_id:
                    return variant.label
        return edge.variant_id
    if edge.variant_id:
        return edge.variant_id
    return edge.dependency_type


def _collect_edges(
    conn: sqlite3.Connection,
    universe_id: str,
    theme_id: str,
) -> list[MusicalDependencyEdgeV1]:
    themed = [
        edge
        for edge in list_dependency_edges(conn, universe_id=universe_id, theme_id=theme_id)
        if edge.upstream_theme_id == theme_id or edge.universe_id == universe_id
    ]
    project_ids: list[str] = []
    for edge in themed:
        for project_id in (edge.upstream_project_id, edge.downstream_project_id):
            if project_id and project_id not in project_ids:
                project_ids.append(project_id)
    pooled: dict[str, MusicalDependencyEdgeV1] = {edge.id: edge for edge in themed}
    for project_id in project_ids:
        for edge in list_dependency_edges(conn, project_id=project_id):
            pooled.setdefault(edge.id, edge)
    ordered = sorted(pooled.values(), key=lambda edge: (edge.created_at, edge.id))
    return ordered[:_WALK_CAP]


def _mark_downstream_of_stale(
    edges: list[MusicalDependencyEdgeV1],
    statuses: dict[str, str],
) -> None:
    by_upstream: dict[str, list[MusicalDependencyEdgeV1]] = {}
    for edge in edges:
        by_upstream.setdefault(upstream_node_key(edge), []).append(edge)
    queue: list[str] = []
    for edge in edges:
        if statuses.get(edge.id) != "stale":
            continue
        queue.append(downstream_node_key(edge))
        if edge.downstream_project_id:
            queue.append(f"project:{edge.downstream_project_id}")
    seen: set[str] = set()
    steps = 0
    while queue and steps < _WALK_CAP:
        current = queue.pop(0)
        steps += 1
        if current in seen:
            continue
        seen.add(current)
        for edge in by_upstream.get(current, []):
            if statuses.get(edge.id) == "fresh":
                statuses[edge.id] = "downstream_of_stale"
            queue.append(downstream_node_key(edge))
            if edge.downstream_project_id:
                queue.append(f"project:{edge.downstream_project_id}")


def _reachable_edge_ids(edges: list[MusicalDependencyEdgeV1], theme_key: str) -> set[str]:
    """Edges reached by walking downstream from the theme, including project hops."""
    by_upstream: dict[str, list[MusicalDependencyEdgeV1]] = {}
    for edge in edges:
        by_upstream.setdefault(upstream_node_key(edge), []).append(edge)
    reached: set[str] = set()
    seen: set[str] = set()
    queue = [theme_key]
    steps = 0
    while queue and steps < _WALK_CAP:
        current = queue.pop(0)
        steps += 1
        if current in seen:
            continue
        seen.add(current)
        for edge in by_upstream.get(current, []):
            reached.add(edge.id)
            queue.append(downstream_node_key(edge))
            if edge.downstream_project_id:
                queue.append(f"project:{edge.downstream_project_id}")
    return reached


def impact_for_theme(
    universe_id: str,
    theme_id: str,
    *,
    db_path: Path | str | None = None,
) -> MusicalDependencyImpactV1:
    """List dependents of one theme. Does not write the universe or any score."""
    path = Path(db_path) if db_path is not None else get_project_db_path()
    logger.debug(
        "Dependency impact start",
        extra={"universe_id": universe_id, "theme_id": theme_id},
    )
    try:
        record = get_universe(universe_id, db_path=path)
    except MusicalUniverseError as exc:
        raise _error("dependency_not_found", http_status=404) from exc
    theme = next((item for item in record.universe.themes if item.id == theme_id), None)
    if theme is None:
        raise _error("dependency_not_found", http_status=404)
    source_project_id = theme.source.project_id
    with get_connection(path) as conn:
        live_source = _score_fingerprint(source_project_id, db_path=path)
        if live_source is None:
            stored = theme.source_fingerprint
            live_source = stored if len(stored) == 64 else "0" * 64
        edges = _collect_edges(conn, universe_id, theme_id)
        statuses: dict[str, str] = {}
        for edge in edges:
            live = current_upstream_fingerprint(
                conn,
                edge,
                theme_source_project_id=source_project_id,
                db_path=path,
            )
            statuses[edge.id] = _own_status(edge, live)
            logger.debug(
                "Dependency edge status",
                extra={"edge_id": edge.id, "status": statuses[edge.id]},
            )
        _mark_downstream_of_stale(edges, statuses)
        reachable = _reachable_edge_ids(edges, f"theme:{universe_id}:{theme_id}")
    dependents = []
    for edge in edges:
        if edge.id not in reachable:
            continue
        status = statuses[edge.id]
        dependents.append(
            {
                "edge_id": edge.id,
                "dependency_type": edge.dependency_type,
                "label": _label(edge, record.universe),
                "node_key": downstream_node_key(edge),
                "status": status,
                "update_offer": _offer(edge, status=status).model_dump(mode="json"),
            }
        )
        if len(dependents) >= _DEPENDENT_CAP:
            break
    stale_count = sum(1 for item in dependents if item["status"] == "stale")
    logger.info(
        "Dependency impact finished",
        extra={
            "theme_id": theme_id,
            "dependent_count": len(dependents),
            "stale_count": stale_count,
            "live_source_fingerprint_prefix": snapshot_fingerprint_log_prefix(live_source),
        },
    )
    return parse_musical_dependency_impact(
        {
            "schema_version": "musical.dependency.impact.v1",
            "theme_id": theme_id,
            "live_source_fingerprint": live_source,
            "dependents": dependents,
        }
    )


def _kind_for_key(node_key: str) -> str:
    prefix = node_key.split(":", 1)[0]
    return {
        "theme": "theme",
        "motif": "motif_occurrence",
        "revision": "revision",
        "project": "project",
        "audio": "audio_asset",
        "render": "neural_render",
        "stems": "neural_stem_set",
    }.get(prefix, "project")


def _status_map(
    conn: sqlite3.Connection,
    edges: list[MusicalDependencyEdgeV1],
    universe: MusicalUniverseV1 | None,
    *,
    db_path: Path,
) -> dict[str, str]:
    sources = {}
    if universe is not None:
        sources = {theme.id: theme.source.project_id for theme in universe.themes}
    statuses: dict[str, str] = {}
    for edge in edges:
        source = sources.get(edge.upstream_theme_id) if edge.upstream_theme_id else None
        live = current_upstream_fingerprint(
            conn,
            edge,
            theme_source_project_id=source,
            db_path=db_path,
        )
        statuses[edge.id] = _own_status(edge, live)
    _mark_downstream_of_stale(edges, statuses)
    return statuses


def _node(
    node_key: str,
    *,
    edge: MusicalDependencyEdgeV1 | None,
    universe: MusicalUniverseV1 | None,
    side: str,
) -> dict[str, Any]:
    kind = _kind_for_key(node_key)
    label = ""
    if kind == "theme" and universe is not None and edge is not None and edge.upstream_theme_id:
        match = next((theme for theme in universe.themes if theme.id == edge.upstream_theme_id), None)
        label = match.label if match is not None else ""
    elif edge is not None and side == "downstream" and kind == "motif_occurrence":
        label = _label(edge, universe)
    payload: dict[str, Any] = {"node_key": node_key, "kind": kind, "label": label[:120]}
    if edge is None:
        return payload
    if kind == "theme":
        payload["universe_id"] = edge.universe_id
        payload["theme_id"] = edge.upstream_theme_id
    elif kind == "motif_occurrence":
        if side == "upstream":
            payload["project_id"] = edge.upstream_project_id
            payload["motif_id"] = edge.source_motif_id
            payload["occurrence_id"] = edge.source_occurrence_id
        else:
            payload["project_id"] = edge.downstream_project_id
            payload["motif_id"] = edge.motif_id
            payload["occurrence_id"] = edge.occurrence_id
            payload["variant_id"] = edge.variant_id
    elif kind == "revision":
        payload["project_id"] = edge.upstream_project_id if side == "upstream" else edge.downstream_project_id
        payload["revision_id"] = (
            edge.upstream_revision_id if side == "upstream" else edge.downstream_revision_id
        )
    elif kind == "project":
        payload["project_id"] = edge.upstream_project_id if side == "upstream" else edge.downstream_project_id
    elif kind in {"audio_asset", "neural_render", "neural_stem_set"}:
        payload["asset_id"] = edge.downstream_asset_id
        payload["project_id"] = edge.downstream_project_id
    return payload


def _assemble_graph(
    edges: list[MusicalDependencyEdgeV1],
    statuses: dict[str, str],
    universe: MusicalUniverseV1 | None,
    *,
    universe_id: str | None = None,
) -> MusicalDependencyGraphV1:
    nodes: dict[str, dict[str, Any]] = {}
    if universe is not None and universe_id:
        for theme in universe.themes:
            key = f"theme:{universe_id}:{theme.id}"
            nodes[key] = {
                "node_key": key,
                "kind": "theme",
                "label": theme.label[:120],
                "universe_id": universe_id,
                "theme_id": theme.id,
            }
    graph_edges = []
    for edge in edges[:256]:
        up = upstream_node_key(edge)
        down = downstream_node_key(edge)
        nodes.setdefault(up, _node(up, edge=edge, universe=universe, side="upstream"))
        nodes.setdefault(down, _node(down, edge=edge, universe=universe, side="downstream"))
        graph_edges.append(
            {
                "edge_id": edge.id,
                "dependency_type": edge.dependency_type,
                "upstream_node_key": up,
                "downstream_node_key": down,
                "status": statuses.get(edge.id, "missing"),
            }
        )
    return parse_musical_dependency_graph(
        {
            "schema_version": "musical.dependency.graph.v1",
            "nodes": list(nodes.values())[:256],
            "edges": graph_edges[:256],
        }
    )


def graph_for_universe(
    universe_id: str,
    *,
    db_path: Path | str | None = None,
) -> MusicalDependencyGraphV1:
    """Assemble the universe graph. Does not write scores or edge rows."""
    path = Path(db_path) if db_path is not None else get_project_db_path()
    try:
        record = get_universe(universe_id, db_path=path)
    except MusicalUniverseError as exc:
        raise _error("dependency_not_found", http_status=404) from exc
    with get_connection(path) as conn:
        pooled: dict[str, MusicalDependencyEdgeV1] = {}
        if record.universe.themes:
            for theme in record.universe.themes:
                for edge in _collect_edges(conn, universe_id, theme.id):
                    pooled.setdefault(edge.id, edge)
        else:
            for edge in list_dependency_edges(conn, universe_id=universe_id):
                pooled.setdefault(edge.id, edge)
        edges = sorted(pooled.values(), key=lambda edge: (edge.created_at, edge.id))
        statuses = _status_map(conn, edges, record.universe, db_path=path)
    logger.info(
        "Dependency graph finished",
        extra={"universe_id": universe_id, "edge_count": min(len(edges), 256)},
    )
    return _assemble_graph(edges, statuses, record.universe, universe_id=universe_id)


def graph_for_project(
    project_id: str,
    *,
    db_path: Path | str | None = None,
) -> MusicalDependencyGraphV1:
    """Edges that name this project on either side."""
    path = Path(db_path) if db_path is not None else get_project_db_path()
    record = get_universe_for_project(project_id, db_path=path)
    universe = None if record is None else record.universe
    universe_id = None if record is None else record.id
    with get_connection(path) as conn:
        edges = list_dependency_edges(conn, project_id=project_id)
        statuses = _status_map(conn, edges, universe, db_path=path)
    logger.info(
        "Dependency graph finished",
        extra={"project_id": project_id, "edge_count": min(len(edges), 256)},
    )
    return _assemble_graph(edges, statuses, universe, universe_id=universe_id)


def edge_project_ids(
    *,
    universe_id: str | None = None,
    theme_id: str | None = None,
    project_id: str | None = None,
    db_path: Path | str | None = None,
) -> list[str]:
    """Project ids named by selected edges, before any score is loaded."""
    path = Path(db_path) if db_path is not None else get_project_db_path()
    with get_connection(path) as conn:
        edges = list_dependency_edges(
            conn,
            universe_id=universe_id,
            theme_id=theme_id,
            project_id=project_id,
        )
    found: list[str] = []
    for edge in edges:
        for named in (edge.upstream_project_id, edge.downstream_project_id):
            if named and named not in found:
                found.append(named)
    return found


def _theme_source_project(edge: MusicalDependencyEdgeV1, *, db_path: Path) -> str | None:
    if not edge.universe_id or not edge.upstream_theme_id:
        return None
    try:
        record = get_universe(edge.universe_id, db_path=db_path)
    except MusicalUniverseError:
        return None
    theme = next((item for item in record.universe.themes if item.id == edge.upstream_theme_id), None)
    if theme is None:
        return None
    return theme.source.project_id


def live_downstream_fingerprint(
    edge: MusicalDependencyEdgeV1,
    *,
    db_path: Path,
) -> str | None:
    """Hash the derived asset. A motif uses its occurrence events."""
    if edge.downstream_kind == "motif_occurrence":
        if not edge.downstream_project_id or not edge.motif_id or not edge.occurrence_id:
            return None
        composition = _load_composition(edge.downstream_project_id, db_path=db_path)
        if composition is None:
            return None
        events = _occurrence_events(composition, edge.motif_id, edge.occurrence_id)
        if events is None:
            return None
        return motif_occurrence_fingerprint(events)
    if not edge.downstream_project_id:
        return None
    return _score_fingerprint(edge.downstream_project_id, db_path=db_path)


def accept_current_edge(
    edge_id: str,
    *,
    db_path: Path | str | None = None,
) -> MusicalDependencyEdgeV1:
    """Store the live upstream fingerprint. Does not rewrite notes."""
    path = Path(db_path) if db_path is not None else get_project_db_path()
    logger.debug("Dependency accept current", extra={"edge_id": edge_id})
    with get_connection(path) as conn:
        edge = get_dependency_edge(edge_id, connection=conn)
        live = current_upstream_fingerprint(
            conn,
            edge,
            theme_source_project_id=_theme_source_project(edge, db_path=path),
            db_path=path,
        )
        if live is None:
            raise _error("dependency_not_found", http_status=404)
        return set_upstream_fingerprint(conn, edge_id, live)


def record_refresh_edge(
    edge_id: str,
    observed_downstream_fingerprint: str,
    *,
    db_path: Path | str | None = None,
) -> MusicalDependencyEdgeV1:
    """Store the live upstream fingerprint when the downstream hash matches."""
    path = Path(db_path) if db_path is not None else get_project_db_path()
    logger.debug("Dependency record refresh", extra={"edge_id": edge_id})
    with get_connection(path) as conn:
        edge = get_dependency_edge(edge_id, connection=conn)
        live_down = live_downstream_fingerprint(edge, db_path=path)
        if live_down != observed_downstream_fingerprint:
            raise _error("dependency_refresh_conflict", http_status=409)
        live = current_upstream_fingerprint(
            conn,
            edge,
            theme_source_project_id=_theme_source_project(edge, db_path=path),
            db_path=path,
        )
        if live is None:
            raise _error("dependency_refresh_conflict", http_status=409)
        return set_upstream_fingerprint(conn, edge_id, live)
