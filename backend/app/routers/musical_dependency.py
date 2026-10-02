"""Impact, graph, and edge-fingerprint routes.

These routes do not insert edges and do not write ``tracks[].events[]``.
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException

from app.musical_dependency_schemas import (
    MusicalDependencyError,
    map_musical_dependency_error_to_http,
    parse_musical_dependency_record_refresh,
)
from app.musical_universe_schemas import MusicalUniverseError
from app.routers.collaboration_guard import enforce_current
from app.services.musical_dependency_impact import (
    accept_current_edge,
    edge_project_ids,
    graph_for_project,
    graph_for_universe,
    impact_for_theme,
    record_refresh_edge,
)
from app.services.musical_universe_store import get_universe

logger = logging.getLogger(__name__)

router = APIRouter(tags=["musical-dependency"])


def _raise(exc: MusicalDependencyError) -> None:
    status, detail = map_musical_dependency_error_to_http(exc)
    raise HTTPException(status_code=status, detail=detail) from exc


def _authorize_read(project_ids: list[str]) -> None:
    for project_id in project_ids:
        enforce_current(project_id, "read")


def _with_theme_sources(universe_id: str, project_ids: list[str]) -> list[str]:
    found = list(project_ids)
    try:
        record = get_universe(universe_id)
    except MusicalUniverseError:
        return found
    for theme in record.universe.themes:
        source_id = theme.source.project_id
        if source_id and source_id not in found:
            found.append(source_id)
    return found


def _finish(method: str, path_id: str, status: int, started: float) -> None:
    logger.info(
        "Dependency route finished",
        extra={
            "method": method,
            "path_id": path_id,
            "status": status,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        },
    )


@router.get("/musical-universes/{universe_id}/themes/{theme_id}/dependents")
def get_theme_dependents(universe_id: str, theme_id: str):
    started = time.perf_counter()
    status = 500
    try:
        project_ids = _with_theme_sources(
            universe_id,
            edge_project_ids(universe_id=universe_id, theme_id=theme_id),
        )
        _authorize_read(project_ids)
        body = impact_for_theme(universe_id, theme_id)
        status = 200
        return body
    except MusicalDependencyError as exc:
        status = exc.http_status
        _raise(exc)
    finally:
        _finish("GET", theme_id, status, started)


@router.get("/musical-universes/{universe_id}/dependency-graph")
def get_universe_graph(universe_id: str):
    started = time.perf_counter()
    status = 500
    try:
        project_ids = _with_theme_sources(universe_id, edge_project_ids(universe_id=universe_id))
        _authorize_read(project_ids)
        body = graph_for_universe(universe_id)
        status = 200
        return body
    except MusicalDependencyError as exc:
        status = exc.http_status
        _raise(exc)
    finally:
        _finish("GET", universe_id, status, started)


@router.get("/projects/{project_id}/dependency-graph")
def get_project_graph(project_id: str):
    started = time.perf_counter()
    status = 500
    try:
        project_ids = edge_project_ids(project_id=project_id)
        if project_id not in project_ids:
            project_ids.append(project_id)
        _authorize_read(project_ids)
        body = graph_for_project(project_id)
        status = 200
        return body
    except MusicalDependencyError as exc:
        status = exc.http_status
        _raise(exc)
    finally:
        _finish("GET", project_id, status, started)


@router.post("/musical-dependency/edges/{edge_id}/accept-current")
def post_accept_current(edge_id: str):
    started = time.perf_counter()
    status = 500
    try:
        from app.services.musical_dependency_store import get_dependency_edge

        edge = get_dependency_edge(edge_id)
        enforce_current(edge.downstream_project_id, "write_score")
        body = accept_current_edge(edge_id)
        status = 200
        return body
    except MusicalDependencyError as exc:
        status = exc.http_status
        _raise(exc)
    finally:
        _finish("POST", edge_id, status, started)


@router.post("/musical-dependency/edges/{edge_id}/record-refresh")
def post_record_refresh(edge_id: str, payload: dict):
    started = time.perf_counter()
    status = 500
    try:
        from app.services.musical_dependency_store import get_dependency_edge

        request = parse_musical_dependency_record_refresh(payload)
        edge = get_dependency_edge(edge_id)
        enforce_current(edge.downstream_project_id, "write_score")
        body = record_refresh_edge(edge_id, request.observed_downstream_fingerprint)
        status = 200
        return body
    except MusicalDependencyError as exc:
        status = exc.http_status
        _raise(exc)
    finally:
        _finish("POST", edge_id, status, started)
