"""External HTTP and WebSocket surface for one adaptive engine session.

Auth is the bearer token or a direct loopback peer. ``X-Forwarded-For`` is
never read. The routes do not write the score or the composition.
"""

from __future__ import annotations

import logging
import os
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.adaptive_engine_schemas import (
    AdaptiveEngineCommandResultV1,
    AdaptiveEngineCueEventV1,
    AdaptiveEngineError,
    AdaptiveEngineErrorV1,
    AdaptiveEngineEventV1,
    AdaptiveEngineIntensityCommandV1,
    AdaptiveEngineSessionV1,
    AdaptiveEngineStartV1,
    AdaptiveEngineStateCommandV1,
    AdaptiveEngineStingerEventV1,
    engine_error_detail,
)
from app.adaptive_musical_context_schemas import AdaptiveContextExternalV1
from app.adaptive_score_schemas import log_adaptive_schema_failure
from app.services.adaptive_engine_auth import authorize_engine_request
from app.adaptive_runtime_continuation_schemas import (
    AdaptiveRuntimeBufferV1,
    AdaptiveRuntimeContinuationV1,
)
from app.services.adaptive_engine_continuous import (
    get_engine_continuous_buffer,
    maintain_engine_continuous,
)
from app.services.adaptive_engine_service import (
    command_engine_context,
    command_engine_event,
    command_engine_intensity,
    command_engine_state,
    delete_engine_session,
    get_engine_session,
    start_engine_session,
    subscribe_engine_feed,
    unsubscribe_engine_feed,
)
from app.services.adaptive_engine_service import get_default_engine_registry

logger = logging.getLogger(__name__)

router = APIRouter(tags=["adaptive-engine"])
_bearer = HTTPBearer(auto_error=False, scheme_name="AdaptiveEngineBearer")


async def require_engine_access(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> None:
    """HTTP gate. The header value is not logged."""
    del credentials
    _refuse_if_unauthorized(
        authorization_header=request.headers.get("authorization"),
        peer_host=None if request.client is None else request.client.host,
        query_token_present="token" in request.query_params,
    )


def _refuse_if_unauthorized(
    *,
    authorization_header: str | None,
    peer_host: str | None,
    query_token_present: bool,
) -> None:
    decision = authorize_engine_request(
        configured_token=os.environ.get("ADAPTIVE_ENGINE_TOKEN"),
        authorization_header=authorization_header,
        peer_host=peer_host,
        query_token_present=query_token_present,
    )
    if decision.verdict != "allow":
        raise _http_error(AdaptiveEngineError("engine_unauthorized"))


def _http_error(exc: AdaptiveEngineError) -> HTTPException:
    logger.info(
        "Adaptive engine request refused",
        extra={"status": exc.http_status, "code": exc.code},
    )
    return HTTPException(status_code=exc.http_status, detail=engine_error_detail(exc))


def _engine_path(path: str) -> bool:
    return path == "/adaptive/session" or path.startswith("/adaptive/session/") or path.startswith("/adaptive/")


async def adaptive_engine_validation_handler(request: Request, exc: RequestValidationError):
    """Map engine-route body errors to the public payload code. Other routes stay unchanged."""
    if not _engine_path(request.url.path):
        return await request_validation_exception_handler(request, exc)
    field = _validation_loc(exc)
    log_adaptive_schema_failure("AdaptiveEngineRequest", field, "engine_payload_invalid")
    detail = AdaptiveEngineError("engine_payload_invalid").to_model().model_dump(mode="json")
    logger.info(
        "Adaptive engine request refused",
        extra={"status": 422, "code": "engine_payload_invalid"},
    )
    return JSONResponse(status_code=422, content={"detail": detail})


def _validation_loc(exc: RequestValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "body"
    loc = errors[0].get("loc") or ()
    if not loc:
        return "body"
    return str(loc[-1])


@router.post(
    "/adaptive/session",
    response_model=AdaptiveEngineSessionV1,
    status_code=201,
    responses={401: {"model": AdaptiveEngineErrorV1}, 409: {"model": AdaptiveEngineErrorV1}, 422: {"model": AdaptiveEngineErrorV1}, 429: {"model": AdaptiveEngineErrorV1}},
)
def post_session(
    body: AdaptiveEngineStartV1,
    _: Annotated[None, Depends(require_engine_access)],
) -> AdaptiveEngineSessionV1:
    logger.debug("Adaptive engine HTTP session start")
    try:
        return start_engine_session(body)
    except AdaptiveEngineError as exc:
        raise _http_error(exc) from exc


@router.get(
    "/adaptive/session/{session_id}",
    response_model=AdaptiveEngineSessionV1,
    responses={401: {"model": AdaptiveEngineErrorV1}, 404: {"model": AdaptiveEngineErrorV1}},
)
def get_session(
    session_id: str,
    _: Annotated[None, Depends(require_engine_access)],
) -> AdaptiveEngineSessionV1:
    logger.debug("Adaptive engine HTTP session read", extra={"session_id": session_id})
    try:
        return get_engine_session(session_id)
    except AdaptiveEngineError as exc:
        raise _http_error(exc) from exc


@router.delete(
    "/adaptive/session/{session_id}",
    status_code=204,
    responses={401: {"model": AdaptiveEngineErrorV1}, 404: {"model": AdaptiveEngineErrorV1}},
)
def delete_session(
    session_id: str,
    _: Annotated[None, Depends(require_engine_access)],
) -> Response:
    logger.debug("Adaptive engine HTTP session delete", extra={"session_id": session_id})
    try:
        delete_engine_session(session_id)
    except AdaptiveEngineError as exc:
        raise _http_error(exc) from exc
    return Response(status_code=204)


@router.post(
    "/adaptive/session/{session_id}/state",
    response_model=AdaptiveEngineCommandResultV1,
    responses={401: {"model": AdaptiveEngineErrorV1}, 404: {"model": AdaptiveEngineErrorV1}, 409: {"model": AdaptiveEngineErrorV1}, 422: {"model": AdaptiveEngineErrorV1}, 429: {"model": AdaptiveEngineErrorV1}},
)
def post_state(
    session_id: str,
    body: AdaptiveEngineStateCommandV1,
    _: Annotated[None, Depends(require_engine_access)],
) -> AdaptiveEngineCommandResultV1:
    return _invoke(lambda: command_engine_state(session_id, body))


@router.post(
    "/adaptive/session/{session_id}/intensity",
    response_model=AdaptiveEngineCommandResultV1,
    responses={401: {"model": AdaptiveEngineErrorV1}, 429: {"model": AdaptiveEngineErrorV1}},
)
def post_intensity(
    session_id: str,
    body: AdaptiveEngineIntensityCommandV1,
    _: Annotated[None, Depends(require_engine_access)],
) -> AdaptiveEngineCommandResultV1:
    return _invoke(lambda: command_engine_intensity(session_id, body))


@router.post(
    "/adaptive/session/{session_id}/event",
    response_model=AdaptiveEngineCommandResultV1,
    responses={401: {"model": AdaptiveEngineErrorV1}, 422: {"model": AdaptiveEngineErrorV1}},
)
def post_event(
    session_id: str,
    body: AdaptiveEngineEventV1,
    _: Annotated[None, Depends(require_engine_access)],
) -> AdaptiveEngineCommandResultV1:
    if not isinstance(body, (AdaptiveEngineStingerEventV1, AdaptiveEngineCueEventV1)):
        raise _http_error(AdaptiveEngineError("engine_payload_invalid"))
    return _invoke(lambda: command_engine_event(session_id, body))


@router.post(
    "/adaptive/session/{session_id}/continuous/maintain",
    response_model=AdaptiveRuntimeContinuationV1,
    responses={
        401: {"model": AdaptiveEngineErrorV1},
        404: {"model": AdaptiveEngineErrorV1},
        422: {"model": AdaptiveEngineErrorV1},
    },
)
async def post_continuous_maintain(
    session_id: str,
    _: Annotated[None, Depends(require_engine_access)],
) -> AdaptiveRuntimeContinuationV1:
    """Opt-in continuous maintain. Not auto-armed on engine start. Not on /ready."""
    logger.info(
        "Adaptive engine continuous maintain",
        extra={
            "code": "engine_continuous_maintain",
            "session_id": session_id if session_id.startswith("aeng_") else "invalid",
        },
    )
    try:
        return maintain_engine_continuous(session_id)
    except AdaptiveEngineError as exc:
        raise _http_error(exc) from exc


@router.get(
    "/adaptive/session/{session_id}/continuous/buffer",
    response_model=AdaptiveRuntimeBufferV1,
    responses={
        401: {"model": AdaptiveEngineErrorV1},
        404: {"model": AdaptiveEngineErrorV1},
        422: {"model": AdaptiveEngineErrorV1},
    },
)
def get_continuous_buffer(
    session_id: str,
    _: Annotated[None, Depends(require_engine_access)],
) -> AdaptiveRuntimeBufferV1 | Response:
    try:
        buffer = get_engine_continuous_buffer(session_id)
    except AdaptiveEngineError as exc:
        raise _http_error(exc) from exc
    if buffer is None:
        return Response(status_code=204)
    return buffer


@router.post(
    "/adaptive/session/{session_id}/context",
    response_model=AdaptiveEngineCommandResultV1,
    responses={
        202: {"model": AdaptiveEngineCommandResultV1},
        401: {"model": AdaptiveEngineErrorV1},
        409: {"model": AdaptiveEngineErrorV1},
        422: {"model": AdaptiveEngineErrorV1},
    },
)
def post_context(
    session_id: str,
    body: Annotated[dict[str, Any], Body()],
    _: Annotated[None, Depends(require_engine_access)],
) -> JSONResponse:
    logger.debug(
        "Adaptive engine context HTTP",
        extra={"model": AdaptiveContextExternalV1.__name__},
    )
    try:
        result = command_engine_context(session_id, body)
    except AdaptiveEngineError as exc:
        raise _http_error(exc) from exc
    status = 202 if result.coalesced and not result.applied else 200
    return JSONResponse(status_code=status, content=result.model_dump(mode="json"))


def _invoke(action):
    try:
        return action()
    except AdaptiveEngineError as exc:
        raise _http_error(exc) from exc


@router.websocket("/adaptive/events")
async def engine_events(websocket: WebSocket, session_id: str = Query(...)) -> None:
    await _socket(websocket, session_id, "events")


@router.websocket("/adaptive/status")
async def engine_status(websocket: WebSocket, session_id: str = Query(...)) -> None:
    await _socket(websocket, session_id, "status")


async def _socket(websocket: WebSocket, session_id: str, kind: str) -> None:
    safe_id = session_id if session_id.startswith("aeng_") else "invalid"
    if "token" in websocket.query_params or not _socket_allowed(websocket):
        await websocket.accept()
        await websocket.close(code=4401)
        logger.info(
            "Adaptive engine socket closed",
            extra={"session_id": safe_id, "close_code": 4401},
        )
        return
    if get_default_engine_registry().by_id(session_id) is None:
        await websocket.accept()
        await websocket.close(code=4404)
        logger.info(
            "Adaptive engine socket closed",
            extra={"session_id": safe_id, "close_code": 4404},
        )
        return
    await websocket.accept()
    try:
        feed = subscribe_engine_feed(session_id, "events" if kind == "events" else "status")
    except AdaptiveEngineError:
        await websocket.close(code=4404)
        logger.info(
            "Adaptive engine socket closed",
            extra={"session_id": safe_id, "close_code": 4404},
        )
        return
    close_code = 1000
    try:
        while True:
            item = await feed.queue.get()
            if item.get("frame") == "close":
                close_code = int(item.get("code") or feed.close_code or 1000)
                await websocket.close(code=close_code)
                return
            await websocket.send_json(item["body"])
            pending_close = feed.close_code
            if pending_close is not None and feed.queue.empty():
                close_code = pending_close
                logger.info(
                    "[FIX] Adaptive engine socket closed after a full acknowledgement queue",
                    extra={"session_id": safe_id, "close_code": close_code},
                )
                await websocket.close(code=close_code)
                return
    except WebSocketDisconnect as exc:
        close_code = int(exc.code or 1000)
    finally:
        unsubscribe_engine_feed(session_id, feed, close_code)


def _socket_allowed(websocket: WebSocket) -> bool:
    host = None if websocket.client is None else websocket.client.host
    decision = authorize_engine_request(
        configured_token=os.environ.get("ADAPTIVE_ENGINE_TOKEN"),
        authorization_header=websocket.headers.get("authorization"),
        peer_host=host,
        query_token_present=False,
    )
    return decision.verdict == "allow"
