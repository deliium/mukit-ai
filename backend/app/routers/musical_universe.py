"""HTTP membership, commands, validation, and mechanical theme reuse.

Opening these routes loads the stored universe. They do not copy notes into
the document. Reuse is the only route that writes destination note events.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from app.musical_universe_schemas import (
    MUSICAL_UNIVERSE_ERROR_CODES,
    CreateThemeCommand,
    MusicalUniverseCreateRequest,
    MusicalUniverseError,
    MusicalUniverseGetResponse,
    MusicalUniverseListResponse,
    MusicalUniverseMemberRequest,
    MusicalUniverseValidateResponse,
    map_musical_universe_error_to_http,
    parse_musical_universe_command_request,
    parse_musical_universe_reuse_request,
)
from app.routers.collaboration_guard import enforce_current
from app.services.musical_universe_bind import bind_theme_source, bind_universe_references
from app.services.musical_universe_commands import apply_musical_universe_command
from app.services.musical_universe_reuse import ThemeReuseRequest, reuse_theme
from app.services.musical_universe_store import (
    MusicalUniverseRecord,
    add_member,
    create_universe,
    delete_universe,
    get_universe,
    get_universe_for_project,
    list_universes,
    remove_member,
    update_universe_document,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["musical-universe"])

_THEME_SOURCE_REFUSAL = frozenset(
    {
        "universe_source_missing",
        "universe_theme_source_not_original",
        "universe_project_not_member",
    }
)


def _raise(exc: MusicalUniverseError) -> None:
    status, detail = map_musical_universe_error_to_http(exc)
    raise HTTPException(status_code=status, detail=detail) from exc


def _document(record: MusicalUniverseRecord) -> MusicalUniverseGetResponse:
    return MusicalUniverseGetResponse(
        universe=record.universe,
        member_project_ids=list(record.member_project_ids),
        document_revision=record.document_revision,
    )


def _log_route(
    method: str,
    *,
    universe_id: str | None,
    project_id: str | None,
    status: int,
    started: float,
) -> None:
    logger.info(
        "Musical universe route finished",
        extra={
            "method": method,
            "universe_id": universe_id,
            "project_id": project_id,
            "http_status": status,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        },
    )


def _read_members(project_ids: list[str]) -> None:
    for project_id in project_ids:
        enforce_current(project_id, "read")


def _write_members(project_ids: list[str]) -> None:
    for project_id in project_ids:
        enforce_current(project_id, "write_score")


@router.post("/musical-universes", response_model=MusicalUniverseGetResponse, status_code=201)
async def create_musical_universe(request: MusicalUniverseCreateRequest) -> MusicalUniverseGetResponse:
    started = time.perf_counter()
    enforce_current(request.project_id, "write_score")
    try:
        record = create_universe(request.name, request.project_id)
    except MusicalUniverseError as exc:
        _log_route("POST", universe_id=None, project_id=request.project_id, status=exc.http_status, started=started)
        _raise(exc)
    _log_route(
        "POST",
        universe_id=record.universe.id,
        project_id=request.project_id,
        status=201,
        started=started,
    )
    return _document(record)


@router.get("/musical-universes", response_model=MusicalUniverseListResponse)
async def list_musical_universes() -> MusicalUniverseListResponse:
    started = time.perf_counter()
    summaries = list_universes()
    _log_route("GET", universe_id=None, project_id=None, status=200, started=started)
    return MusicalUniverseListResponse(universes=summaries)


@router.get("/musical-universes/{universe_id}", response_model=MusicalUniverseGetResponse)
async def get_musical_universe(universe_id: str) -> MusicalUniverseGetResponse:
    started = time.perf_counter()
    try:
        record = get_universe(universe_id)
    except MusicalUniverseError as exc:
        _log_route("GET", universe_id=universe_id, project_id=None, status=exc.http_status, started=started)
        _raise(exc)
    _read_members(record.member_project_ids)
    _log_route("GET", universe_id=universe_id, project_id=None, status=200, started=started)
    return _document(record)


@router.get("/projects/{project_id}/musical-universe", response_model=MusicalUniverseGetResponse)
async def get_project_musical_universe(project_id: str) -> MusicalUniverseGetResponse:
    started = time.perf_counter()
    enforce_current(project_id, "read")
    record = get_universe_for_project(project_id)
    if record is None:
        exc = MusicalUniverseError(
            "universe_not_linked",
            MUSICAL_UNIVERSE_ERROR_CODES["universe_not_linked"],
            http_status=404,
            details={"project_id": project_id},
        )
        _log_route("GET", universe_id=None, project_id=project_id, status=404, started=started)
        _raise(exc)
    _read_members(record.member_project_ids)
    _log_route("GET", universe_id=record.universe.id, project_id=project_id, status=200, started=started)
    return _document(record)


@router.post("/musical-universes/{universe_id}/members", response_model=MusicalUniverseGetResponse)
async def add_musical_universe_member(
    universe_id: str,
    request: MusicalUniverseMemberRequest,
) -> MusicalUniverseGetResponse:
    started = time.perf_counter()
    enforce_current(request.project_id, "write_score")
    try:
        record = add_member(universe_id, request.project_id)
    except MusicalUniverseError as exc:
        _log_route(
            "POST",
            universe_id=universe_id,
            project_id=request.project_id,
            status=exc.http_status,
            started=started,
        )
        _raise(exc)
    _log_route("POST", universe_id=universe_id, project_id=request.project_id, status=200, started=started)
    return _document(record)


@router.delete("/musical-universes/{universe_id}/members/{project_id}", response_model=MusicalUniverseGetResponse)
async def remove_musical_universe_member(universe_id: str, project_id: str) -> MusicalUniverseGetResponse:
    started = time.perf_counter()
    enforce_current(project_id, "write_score")
    try:
        record = remove_member(universe_id, project_id)
    except MusicalUniverseError as exc:
        _log_route("DELETE", universe_id=universe_id, project_id=project_id, status=exc.http_status, started=started)
        _raise(exc)
    _log_route("DELETE", universe_id=universe_id, project_id=project_id, status=200, started=started)
    return _document(record)


@router.delete("/musical-universes/{universe_id}", status_code=204)
async def delete_musical_universe(universe_id: str) -> None:
    started = time.perf_counter()
    try:
        record = get_universe(universe_id)
    except MusicalUniverseError as exc:
        _log_route("DELETE", universe_id=universe_id, project_id=None, status=exc.http_status, started=started)
        _raise(exc)
    _write_members(record.member_project_ids)
    try:
        delete_universe(universe_id)
    except MusicalUniverseError as exc:
        _log_route("DELETE", universe_id=universe_id, project_id=None, status=exc.http_status, started=started)
        _raise(exc)
    _log_route("DELETE", universe_id=universe_id, project_id=None, status=204, started=started)


@router.post("/musical-universes/{universe_id}/commands", response_model=MusicalUniverseGetResponse)
async def command_musical_universe(universe_id: str, body: dict[str, Any] = Body(...)) -> MusicalUniverseGetResponse:
    started = time.perf_counter()
    try:
        request = parse_musical_universe_command_request(body)
        record = get_universe(universe_id)
        _write_members(record.member_project_ids)
        _refuse_unchecked_theme(record, request.command)
        logger.debug("Musical universe command", extra={"command": request.command.op, "universe_id": universe_id})
        updated = apply_musical_universe_command(record.universe, request.command)
        record = update_universe_document(updated, expected_revision=request.expected_document_revision)
    except MusicalUniverseError as exc:
        _log_route("POST", universe_id=universe_id, project_id=None, status=exc.http_status, started=started)
        _raise(exc)
    _log_route("POST", universe_id=universe_id, project_id=None, status=200, started=started)
    return _document(record)


@router.post("/musical-universes/{universe_id}/validate", response_model=MusicalUniverseValidateResponse)
async def validate_musical_universe(universe_id: str) -> MusicalUniverseValidateResponse:
    started = time.perf_counter()
    try:
        record = get_universe(universe_id)
    except MusicalUniverseError as exc:
        _log_route("POST", universe_id=universe_id, project_id=None, status=exc.http_status, started=started)
        _raise(exc)
    _read_members(record.member_project_ids)
    findings = bind_universe_references(record.universe, record.member_project_ids)
    _log_route("POST", universe_id=universe_id, project_id=None, status=200, started=started)
    return MusicalUniverseValidateResponse(findings=findings)


@router.post(
    "/musical-universes/{universe_id}/themes/{theme_id}/reuse",
    response_model=MusicalUniverseGetResponse,
)
async def reuse_musical_universe_theme(
    universe_id: str,
    theme_id: str,
    body: dict[str, Any] = Body(...),
) -> MusicalUniverseGetResponse:
    started = time.perf_counter()
    project_id: str | None = None
    try:
        request = parse_musical_universe_reuse_request(body)
        project_id = request.destination_project_id
        record = get_universe(universe_id)
        enforce_current(request.destination_project_id, "write_score")
        theme = next((item for item in record.universe.themes if item.id == theme_id), None)
        if theme is not None and theme.source.project_id != request.destination_project_id:
            enforce_current(theme.source.project_id, "read")
        result = reuse_theme(
            universe_id,
            theme_id,
            ThemeReuseRequest(
                destination_project_id=request.destination_project_id,
                destination_track_id=request.destination_track_id,
                destination_start_bar=request.destination_start_bar,
                operation=request.operation,
                parameters=request.parameters,
                variant_id=request.variant_id,
                expected_universe_revision=request.expected_universe_revision,
                branch_id=request.branch_id,
                expected_active_branch_id=request.expected_active_branch_id,
                expected_working_version=request.expected_working_version,
                expected_head_revision_id=request.expected_head_revision_id,
                expected_source_fingerprint=request.expected_source_fingerprint,
            ),
        )
    except MusicalUniverseError as exc:
        _log_route("POST", universe_id=universe_id, project_id=project_id, status=exc.http_status, started=started)
        _raise(exc)
    _log_route("POST", universe_id=universe_id, project_id=project_id, status=200, started=started)
    return _document(result.universe)


def _refuse_unchecked_theme(record: MusicalUniverseRecord, command: object) -> None:
    if not isinstance(command, CreateThemeCommand):
        return
    findings = bind_theme_source(command.source, record.member_project_ids)
    refused = next((item for item in findings if item.code in _THEME_SOURCE_REFUSAL), None)
    if refused is None:
        return
    status = 422 if refused.code == "universe_theme_source_not_original" else 409
    raise MusicalUniverseError(
        refused.code,
        refused.message or MUSICAL_UNIVERSE_ERROR_CODES[refused.code],
        http_status=status,
        details={"target_id": refused.target_id} if refused.target_id else None,
    )
