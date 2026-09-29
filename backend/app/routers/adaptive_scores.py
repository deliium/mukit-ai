"""HTTP CRUD and read-only validate for project adaptive scores."""

from __future__ import annotations

import asyncio
import logging
import time

from fastapi import APIRouter, Body, HTTPException, Query, Response
from fastapi.responses import JSONResponse

from app.adaptive_musical_context_schemas import (
    AdaptiveMusicalContextV1,
    parse_adaptive_context_external,
    parse_adaptive_context_start,
)
from app.adaptive_playback_schemas import (
    AdaptivePlaybackRuntimeV1,
    parse_adaptive_playback_command,
    parse_adaptive_playback_start,
)
from app.adaptive_runtime_continuation_schemas import (
    AdaptiveRuntimeBufferV1,
    AdaptiveRuntimeContinuationV1,
    parse_adaptive_runtime_continuation_start,
)
from app.adaptive_score_schemas import (
    AdaptiveLayerIntensityV1,
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
    parse_adaptive_layer_intensity_request,
    parse_adaptive_layer_plan_preview_request,
    parse_adaptive_score_command,
)
from app.routers.collaboration_guard import enforce_current
from app.services.adaptive_musical_context_service import (
    get_adaptive_musical_context,
    sample_adaptive_musical_context,
    start_adaptive_musical_context,
    stop_adaptive_musical_context,
)
from app.services.adaptive_playback_service import (
    command_adaptive_playback,
    get_adaptive_playback,
    start_adaptive_playback,
    stop_adaptive_playback,
)
from app.services.adaptive_runtime_continuation_service import (
    get_adaptive_runtime_buffer,
    get_adaptive_runtime_continuation,
    maintain_adaptive_runtime_continuation,
    start_adaptive_runtime_continuation,
    stop_adaptive_runtime_continuation,
)
from app.services.adaptive_score_layer_service import (
    preview_adaptive_layer_plan,
    resolve_adaptive_layer_intensity,
)
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


@router.post("/{score_id}/playback", response_model=AdaptivePlaybackRuntimeV1)
async def start_project_playback(project_id: str, score_id: str, body: dict = Body(...)) -> AdaptivePlaybackRuntimeV1:
    enforce_current(project_id, "read")
    try:
        request = parse_adaptive_playback_start(body)
        return start_adaptive_playback(project_id, score_id, request)
    except AdaptiveScoreError as exc:
        logger.debug(
            "Adaptive playback rejected",
            extra={"code": exc.code, "project_id": project_id, "score_id": score_id},
        )
        _raise(exc)
    raise AssertionError("adaptive playback start")


@router.get("/{score_id}/playback", response_model=AdaptivePlaybackRuntimeV1)
async def get_project_playback(project_id: str, score_id: str) -> Response:
    enforce_current(project_id, "read")
    try:
        snapshot = get_adaptive_playback(project_id, score_id)
    except AdaptiveScoreError as exc:
        logger.debug(
            "Adaptive playback rejected",
            extra={"code": exc.code, "project_id": project_id, "score_id": score_id},
        )
        _raise(exc)
    if snapshot is None:
        return Response(status_code=204)
    return JSONResponse(status_code=200, content=snapshot.model_dump(mode="json"))


@router.post("/{score_id}/playback/commands", response_model=AdaptivePlaybackRuntimeV1)
async def command_project_playback(
    project_id: str,
    score_id: str,
    body: dict = Body(...),
) -> AdaptivePlaybackRuntimeV1:
    enforce_current(project_id, "read")
    try:
        command = parse_adaptive_playback_command(body)
        return command_adaptive_playback(project_id, score_id, command)
    except AdaptiveScoreError as exc:
        logger.debug(
            "Adaptive playback rejected",
            extra={"code": exc.code, "project_id": project_id, "score_id": score_id},
        )
        _raise(exc)
    raise AssertionError("adaptive playback command")


@router.delete("/{score_id}/playback", status_code=204)
async def stop_project_playback(project_id: str, score_id: str) -> Response:
    enforce_current(project_id, "read")
    try:
        stop_adaptive_playback(project_id, score_id)
    except AdaptiveScoreError as exc:
        logger.debug(
            "Adaptive playback rejected",
            extra={"code": exc.code, "project_id": project_id, "score_id": score_id},
        )
        _raise(exc)
    return Response(status_code=204)


@router.post("/{score_id}/layer-intensity", response_model=AdaptiveLayerIntensityV1)
async def map_project_layer_intensity(
    project_id: str,
    score_id: str,
    body: dict = Body(...),
) -> AdaptiveLayerIntensityV1:
    enforce_current(project_id, "read")
    try:
        request = parse_adaptive_layer_intensity_request(body)
        return resolve_adaptive_layer_intensity(project_id, score_id, request)
    except AdaptiveScoreError as exc:
        _raise(exc)
    raise AssertionError("adaptive layer intensity")


@router.post("/{score_id}/layer-plans/preview", response_model=AdaptiveLayerIntensityV1)
async def preview_project_layer_plan(
    project_id: str,
    score_id: str,
    body: dict = Body(...),
) -> AdaptiveLayerIntensityV1:
    enforce_current(project_id, "read")
    try:
        request = parse_adaptive_layer_plan_preview_request(body)
        return preview_adaptive_layer_plan(project_id, score_id, request)
    except AdaptiveScoreError as exc:
        _raise(exc)
    raise AssertionError("adaptive layer plan preview")


@router.post("/{score_id}/context", response_model=AdaptiveMusicalContextV1)
async def start_project_musical_context(
    project_id: str,
    score_id: str,
    body: dict = Body(...),
) -> AdaptiveMusicalContextV1:
    enforce_current(project_id, "read")
    try:
        request = parse_adaptive_context_start(body)
        return start_adaptive_musical_context(project_id, score_id, request)
    except AdaptiveScoreError as exc:
        logger.debug(
            "Adaptive musical context rejected",
            extra={"code": exc.code, "project_id": project_id, "score_id": score_id},
        )
        _raise(exc)
    raise AssertionError("adaptive musical context start")


@router.get("/{score_id}/context", response_model=AdaptiveMusicalContextV1)
async def get_project_musical_context(project_id: str, score_id: str) -> Response:
    enforce_current(project_id, "read")
    try:
        snapshot = get_adaptive_musical_context(project_id, score_id)
    except AdaptiveScoreError as exc:
        logger.debug(
            "Adaptive musical context rejected",
            extra={"code": exc.code, "project_id": project_id, "score_id": score_id},
        )
        _raise(exc)
    if snapshot is None:
        return Response(status_code=204)
    return JSONResponse(status_code=200, content=snapshot.model_dump(mode="json"))


@router.post("/{score_id}/context/samples", response_model=AdaptiveMusicalContextV1)
async def sample_project_musical_context(
    project_id: str,
    score_id: str,
    body: dict = Body(...),
) -> AdaptiveMusicalContextV1:
    enforce_current(project_id, "read")
    try:
        sample = parse_adaptive_context_external(body)
        return sample_adaptive_musical_context(project_id, score_id, sample)
    except AdaptiveScoreError as exc:
        logger.debug(
            "Adaptive musical context rejected",
            extra={"code": exc.code, "project_id": project_id, "score_id": score_id},
        )
        _raise(exc)
    raise AssertionError("adaptive musical context sample")


@router.delete("/{score_id}/context", status_code=204)
async def stop_project_musical_context(project_id: str, score_id: str) -> Response:
    enforce_current(project_id, "read")
    try:
        stop_adaptive_musical_context(project_id, score_id)
    except AdaptiveScoreError as exc:
        logger.debug(
            "Adaptive musical context rejected",
            extra={"code": exc.code, "project_id": project_id, "score_id": score_id},
        )
        _raise(exc)
    return Response(status_code=204)


def _schedule_continuation(coro):
    return asyncio.get_running_loop().create_task(coro)


@router.post("/{score_id}/continuation", response_model=AdaptiveRuntimeContinuationV1)
async def start_project_continuation(
    project_id: str,
    score_id: str,
    body: dict = Body(...),
) -> AdaptiveRuntimeContinuationV1:
    enforce_current(project_id, "read")
    try:
        request = parse_adaptive_runtime_continuation_start(body)
        return start_adaptive_runtime_continuation(project_id, score_id, request)
    except AdaptiveScoreError as exc:
        logger.debug(
            "Adaptive runtime continuation rejected",
            extra={"code": exc.code, "project_id": project_id, "score_id": score_id},
        )
        _raise(exc)
    raise AssertionError("adaptive runtime continuation start")


@router.get("/{score_id}/continuation", response_model=AdaptiveRuntimeContinuationV1)
async def get_project_continuation(project_id: str, score_id: str) -> Response:
    enforce_current(project_id, "read")
    try:
        snapshot = get_adaptive_runtime_continuation(project_id, score_id)
    except AdaptiveScoreError as exc:
        logger.debug(
            "Adaptive runtime continuation rejected",
            extra={"code": exc.code, "project_id": project_id, "score_id": score_id},
        )
        _raise(exc)
    if snapshot is None:
        return Response(status_code=204)
    return JSONResponse(status_code=200, content=snapshot.model_dump(mode="json"))


@router.post("/{score_id}/continuation/maintain", response_model=AdaptiveRuntimeContinuationV1)
async def maintain_project_continuation(
    project_id: str,
    score_id: str,
) -> AdaptiveRuntimeContinuationV1:
    enforce_current(project_id, "read")
    try:
        return maintain_adaptive_runtime_continuation(
            project_id,
            score_id,
            scheduler=_schedule_continuation,
        )
    except AdaptiveScoreError as exc:
        logger.debug(
            "Adaptive runtime continuation rejected",
            extra={"code": exc.code, "project_id": project_id, "score_id": score_id},
        )
        _raise(exc)
    raise AssertionError("adaptive runtime continuation maintain")


@router.get("/{score_id}/continuation/buffer", response_model=AdaptiveRuntimeBufferV1)
async def get_project_continuation_buffer(project_id: str, score_id: str) -> Response:
    enforce_current(project_id, "read")
    try:
        buffer = get_adaptive_runtime_buffer(project_id, score_id)
    except AdaptiveScoreError as exc:
        logger.debug(
            "Adaptive runtime continuation rejected",
            extra={"code": exc.code, "project_id": project_id, "score_id": score_id},
        )
        _raise(exc)
    if buffer is None:
        return Response(status_code=204)
    return JSONResponse(status_code=200, content=buffer.model_dump(mode="json"))


@router.delete("/{score_id}/continuation", status_code=204)
async def stop_project_continuation(project_id: str, score_id: str) -> Response:
    enforce_current(project_id, "read")
    try:
        stop_adaptive_runtime_continuation(project_id, score_id)
    except AdaptiveScoreError as exc:
        logger.debug(
            "Adaptive runtime continuation rejected",
            extra={"code": exc.code, "project_id": project_id, "score_id": score_id},
        )
        _raise(exc)
    return Response(status_code=204)

