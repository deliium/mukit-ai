"""HTTP CRUD and read-only validate for project adaptive scores."""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Body, HTTPException, Query, Response
from fastapi.responses import JSONResponse

from app.adaptive_score_schemas import (
    AdaptiveScoreCommandResponse,
    AdaptiveScoreCreateRequest,
    AdaptiveScoreError,
    AdaptiveScoreGetResponse,
    AdaptiveScoreListResponse,
    AdaptiveScoreUpdateRequest,
    AdaptiveScoreValidateResponse,
    AdaptiveTransitionScheduleRequest,
    AdaptiveTransitionScheduleV1,
    map_adaptive_score_error_to_http,
    parse_adaptive_score_command,
)
from app.routers.collaboration_guard import enforce_current
from app.services.adaptive_score_service import (
    apply_adaptive_score_command,
    create_adaptive_score,
    delete_adaptive_score,
    get_adaptive_score,
    list_adaptive_scores,
    replace_adaptive_score,
    validate_adaptive_score,
)
from app.services.adaptive_score_transition_service import (
    cancel_pending_transition,
    get_pending_transition,
    schedule_adaptive_transition,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects/{project_id}/adaptive-scores", tags=["adaptive-scores"])


def _raise(exc: AdaptiveScoreError) -> None:
    status, detail = map_adaptive_score_error_to_http(exc)
    raise HTTPException(status_code=status, detail=detail) from exc


@router.get("", response_model=AdaptiveScoreListResponse)
async def list_project_adaptive_scores(project_id: str) -> AdaptiveScoreListResponse:
    started = time.perf_counter()
    enforce_current(project_id, "read")
    try:
        scores = list_adaptive_scores(project_id)
    except AdaptiveScoreError as exc:
        _raise(exc)
    logger.info(
        "GET adaptive scores",
        extra={
            "method": "GET",
            "project_id": project_id,
            "http_status": 200,
            "score_count": len(scores),
            "duration_ms": int((time.perf_counter() - started) * 1000),
        },
    )
    return AdaptiveScoreListResponse(scores=scores)


@router.post("", response_model=AdaptiveScoreGetResponse, status_code=201)
async def create_project_adaptive_score(
    project_id: str,
    request: AdaptiveScoreCreateRequest,
) -> AdaptiveScoreGetResponse:
    started = time.perf_counter()
    enforce_current(project_id, "write_score")
    try:
        body = create_adaptive_score(project_id, request)
    except AdaptiveScoreError as exc:
        _raise(exc)
    logger.info(
        "POST adaptive score",
        extra={
            "method": "POST",
            "project_id": project_id,
            "score_id": body.score.id,
            "http_status": 201,
            "state_count": len(body.score.states),
            "binding_status": body.binding_status,
            "document_revision": body.document_revision,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        },
    )
    return body


@router.get("/{score_id}", response_model=AdaptiveScoreGetResponse)
async def get_project_adaptive_score(project_id: str, score_id: str) -> AdaptiveScoreGetResponse:
    started = time.perf_counter()
    enforce_current(project_id, "read")
    try:
        body = get_adaptive_score(project_id, score_id)
    except AdaptiveScoreError as exc:
        _raise(exc)
    logger.info(
        "GET adaptive score",
        extra={
            "method": "GET",
            "project_id": project_id,
            "score_id": score_id,
            "http_status": 200,
            "state_count": len(body.score.states),
            "binding_status": body.binding_status,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        },
    )
    return body


@router.put("/{score_id}", response_model=AdaptiveScoreGetResponse)
async def replace_project_adaptive_score(
    project_id: str,
    score_id: str,
    request: AdaptiveScoreUpdateRequest,
) -> AdaptiveScoreGetResponse:
    started = time.perf_counter()
    enforce_current(project_id, "write_score")
    try:
        body = replace_adaptive_score(project_id, score_id, request)
    except AdaptiveScoreError as exc:
        _raise(exc)
    logger.info(
        "PUT adaptive score",
        extra={
            "method": "PUT",
            "project_id": project_id,
            "score_id": score_id,
            "http_status": 200,
            "state_count": len(body.score.states),
            "document_revision": body.document_revision,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        },
    )
    return body


@router.delete("/{score_id}", status_code=204)
async def delete_project_adaptive_score(project_id: str, score_id: str) -> Response:
    started = time.perf_counter()
    enforce_current(project_id, "write_score")
    try:
        delete_adaptive_score(project_id, score_id)
    except AdaptiveScoreError as exc:
        _raise(exc)
    logger.info(
        "DELETE adaptive score",
        extra={
            "method": "DELETE",
            "project_id": project_id,
            "score_id": score_id,
            "http_status": 204,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        },
    )
    return Response(status_code=204)


@router.post("/{score_id}/validate", response_model=AdaptiveScoreValidateResponse)
async def validate_project_adaptive_score(
    project_id: str,
    score_id: str,
    strict: bool = Query(default=False),
) -> AdaptiveScoreValidateResponse:
    started = time.perf_counter()
    enforce_current(project_id, "read")
    logger.debug(
        "Adaptive score validate request",
        extra={"project_id": project_id, "score_id": score_id, "strict": strict},
    )
    try:
        body = validate_adaptive_score(project_id, score_id, strict=strict)
    except AdaptiveScoreError as exc:
        _raise(exc)
    for finding in body.findings:
        logger.debug(
            "Adaptive score validate code",
            extra={"code": finding.code, "score_id": score_id},
        )
    logger.info(
        "POST adaptive score validate",
        extra={
            "method": "POST",
            "project_id": project_id,
            "score_id": score_id,
            "http_status": 200,
            "error_count": body.error_count,
            "warning_count": body.warning_count,
            "binding_status": body.binding_status,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        },
    )
    return body


@router.post("/{score_id}/commands", response_model=AdaptiveScoreCommandResponse)
async def command_project_adaptive_score(
    project_id: str,
    score_id: str,
    body: dict = Body(...),
) -> AdaptiveScoreCommandResponse:
    started = time.perf_counter()
    enforce_current(project_id, "write_score")
    op = body.get("op") if isinstance(body, dict) else None
    op_label = op if isinstance(op, str) and len(op) <= 40 else None
    try:
        command = parse_adaptive_score_command(body)
        result = apply_adaptive_score_command(project_id, score_id, command)
    except AdaptiveScoreError as exc:
        findings = exc.details.get("findings") if isinstance(exc.details, dict) else None
        codes = [
            item.get("code")
            for item in findings
            if isinstance(item, dict)
        ] if isinstance(findings, list) else []
        logger.debug(
            "Adaptive score command rejected",
            extra={
                "project_id": project_id,
                "score_id": score_id,
                "op": op_label,
                "codes": codes[:16],
            },
        )
        logger.info(
            "POST adaptive score command",
            extra={
                "method": "POST",
                "project_id": project_id,
                "score_id": score_id,
                "op": op_label,
                "http_status": exc.http_status,
                "document_revision": None,
                "duration_ms": int((time.perf_counter() - started) * 1000),
                "state_count": None,
            },
        )
        _raise(exc)
    logger.info(
        "POST adaptive score command",
        extra={
            "method": "POST",
            "project_id": project_id,
            "score_id": score_id,
            "op": command.op,
            "http_status": 200,
            "document_revision": result.document_revision,
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "state_count": len(result.score.states),
        },
    )
    return result


def _schedule_log(
    *,
    method: str,
    project_id: str,
    score_id: str,
    http_status: int,
    started: float,
    schedule: AdaptiveTransitionScheduleV1 | None = None,
    replaced: bool = False,
) -> None:
    logger.info(
        f"{method} adaptive transition request",
        extra={
            "method": method,
            "project_id": project_id,
            "score_id": score_id,
            "http_status": http_status,
            "quantization": None if schedule is None else schedule.quantization,
            "boundary_tick": None if schedule is None else schedule.boundary_tick,
            "latency_ms": None if schedule is None else schedule.latency_ms,
            "latency_ticks": None if schedule is None else schedule.latency_ticks,
            "realization_kind": None if schedule is None else schedule.realization.kind,
            "request_id": None if schedule is None else schedule.request_id,
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "replaced": replaced,
        },
    )


@router.post("/{score_id}/transition-requests", response_model=AdaptiveTransitionScheduleV1)
async def schedule_project_transition(
    project_id: str,
    score_id: str,
    request: AdaptiveTransitionScheduleRequest,
) -> JSONResponse:
    started = time.perf_counter()
    enforce_current(project_id, "read")
    try:
        schedule, status = schedule_adaptive_transition(project_id, score_id, request)
    except AdaptiveScoreError as exc:
        logger.debug(
            "Adaptive transition request rejected",
            extra={"code": exc.code, "project_id": project_id, "score_id": score_id},
        )
        _schedule_log(
            method="POST",
            project_id=project_id,
            score_id=score_id,
            http_status=exc.http_status,
            started=started,
        )
        _raise(exc)
    _schedule_log(
        method="POST",
        project_id=project_id,
        score_id=score_id,
        http_status=status,
        started=started,
        schedule=schedule,
        replaced=schedule.replaced_request_id is not None,
    )
    return JSONResponse(status_code=status, content=schedule.model_dump(mode="json"))


@router.get("/{score_id}/transition-requests/current", response_model=AdaptiveTransitionScheduleV1)
async def get_project_transition_request(project_id: str, score_id: str) -> Response:
    started = time.perf_counter()
    enforce_current(project_id, "read")
    try:
        schedule = get_pending_transition(project_id, score_id)
    except AdaptiveScoreError as exc:
        logger.debug(
            "Adaptive transition request rejected",
            extra={"code": exc.code, "project_id": project_id, "score_id": score_id},
        )
        _schedule_log(
            method="GET",
            project_id=project_id,
            score_id=score_id,
            http_status=exc.http_status,
            started=started,
        )
        _raise(exc)
    if schedule is None:
        _schedule_log(
            method="GET",
            project_id=project_id,
            score_id=score_id,
            http_status=204,
            started=started,
        )
        return Response(status_code=204)
    _schedule_log(
        method="GET",
        project_id=project_id,
        score_id=score_id,
        http_status=200,
        started=started,
        schedule=schedule,
        replaced=schedule.replaced_request_id is not None,
    )
    return JSONResponse(status_code=200, content=schedule.model_dump(mode="json"))


@router.delete("/{score_id}/transition-requests/{request_id}", status_code=204)
async def cancel_project_transition_request(
    project_id: str,
    score_id: str,
    request_id: str,
) -> Response:
    started = time.perf_counter()
    enforce_current(project_id, "read")
    try:
        cancel_pending_transition(project_id, score_id, request_id)
    except AdaptiveScoreError as exc:
        logger.debug(
            "Adaptive transition request rejected",
            extra={
                "code": exc.code,
                "project_id": project_id,
                "score_id": score_id,
                "request_id": request_id,
            },
        )
        _schedule_log(
            method="DELETE",
            project_id=project_id,
            score_id=score_id,
            http_status=exc.http_status,
            started=started,
        )
        _raise(exc)
    _schedule_log(
        method="DELETE",
        project_id=project_id,
        score_id=score_id,
        http_status=204,
        started=started,
    )
    return Response(status_code=204)

