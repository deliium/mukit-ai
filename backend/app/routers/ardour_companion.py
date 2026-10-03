"""HTTP surface for the process-memory Ardour OSC companion."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.ardour_companion_schemas import (
    ArdourCommandAcceptedV1,
    ArdourCompanionConnectV1,
    ArdourCompanionError,
    ArdourCompanionSessionV1,
    ArdourCompanionStatusV1,
    ArdourLocateRequestV1,
    ArdourRecordArmRequestV1,
    ArdourRecordStatusV1,
    ArdourStripBoolValueV1,
    ArdourStripFloatValueV1,
    ArdourStripsResponseV1,
    map_ardour_error_to_http,
)
from app.ardour_companion_settings import load_ardour_companion_settings
from app.services import ardour_companion_service as companion

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ardour/companion", tags=["ardour-companion"])


def _raise(exc: ArdourCompanionError) -> None:
    status, detail = map_ardour_error_to_http(exc)
    raise HTTPException(status_code=status, detail=detail) from exc


@router.get("/status", response_model=ArdourCompanionStatusV1)
async def ardour_companion_status() -> ArdourCompanionStatusV1:
    """Always 200 with ``enabled``; never 403 when the flag is off."""
    settings = load_ardour_companion_settings()
    status = companion.get_ardour_companion_status(settings=settings)
    logger.info(
        "Ardour companion status",
        extra={
            "path": "/ardour/companion/status",
            "code": "ok",
            "connection_state": status.connection_state,
            "enabled": status.enabled,
        },
    )
    return status


@router.post("/connect", response_model=ArdourCompanionSessionV1)
async def ardour_companion_connect(body: ArdourCompanionConnectV1) -> ArdourCompanionSessionV1:
    settings = load_ardour_companion_settings()
    try:
        session = companion.connect_ardour_companion(body, settings=settings)
    except ArdourCompanionError as exc:
        logger.info(
            "Ardour companion connect refused",
            extra={
                "path": "/ardour/companion/connect",
                "code": exc.code,
                "connection_state": "error",
            },
        )
        _raise(exc)
    logger.info(
        "Ardour companion connect",
        extra={
            "path": "/ardour/companion/connect",
            "code": "ok",
            "connection_state": session.connection_state,
        },
    )
    return session


@router.post("/disconnect", response_model=ArdourCommandAcceptedV1)
async def ardour_companion_disconnect() -> ArdourCommandAcceptedV1:
    settings = load_ardour_companion_settings()
    try:
        result = companion.disconnect_ardour_companion(settings=settings)
    except ArdourCompanionError as exc:
        logger.info(
            "Ardour companion disconnect refused",
            extra={
                "path": "/ardour/companion/disconnect",
                "code": exc.code,
                "connection_state": "error",
            },
        )
        _raise(exc)
    logger.info(
        "Ardour companion disconnect",
        extra={
            "path": "/ardour/companion/disconnect",
            "code": "ok",
            "connection_state": result.status.connection_state,
        },
    )
    return result


@router.post("/transport/play", response_model=ArdourCommandAcceptedV1)
async def ardour_transport_play() -> ArdourCommandAcceptedV1:
    settings = load_ardour_companion_settings()
    try:
        return companion.transport_play(settings=settings)
    except ArdourCompanionError as exc:
        logger.info(
            "Ardour transport play",
            extra={
                "path": "/ardour/companion/transport/play",
                "code": exc.code,
                "connection_state": "error",
            },
        )
        _raise(exc)


@router.post("/transport/stop", response_model=ArdourCommandAcceptedV1)
async def ardour_transport_stop() -> ArdourCommandAcceptedV1:
    settings = load_ardour_companion_settings()
    try:
        return companion.transport_stop(settings=settings)
    except ArdourCompanionError as exc:
        logger.info(
            "Ardour transport stop",
            extra={
                "path": "/ardour/companion/transport/stop",
                "code": exc.code,
                "connection_state": "error",
            },
        )
        _raise(exc)


@router.post("/transport/locate", response_model=ArdourCommandAcceptedV1)
async def ardour_transport_locate(body: ArdourLocateRequestV1) -> ArdourCommandAcceptedV1:
    settings = load_ardour_companion_settings()
    try:
        return companion.transport_locate(body.samples, body.roll, settings=settings)
    except ArdourCompanionError as exc:
        logger.info(
            "Ardour transport locate",
            extra={
                "path": "/ardour/companion/transport/locate",
                "code": exc.code,
                "connection_state": "error",
            },
        )
        _raise(exc)


@router.get("/record", response_model=ArdourRecordStatusV1)
async def ardour_record_get() -> ArdourRecordStatusV1:
    settings = load_ardour_companion_settings()
    return companion.get_record_arm(settings=settings)


@router.post("/record", response_model=ArdourCommandAcceptedV1)
async def ardour_record_set(body: ArdourRecordArmRequestV1) -> ArdourCommandAcceptedV1:
    settings = load_ardour_companion_settings()
    try:
        return companion.set_record_arm(body.desired, settings=settings)
    except ArdourCompanionError as exc:
        logger.info(
            "Ardour record arm",
            extra={
                "path": "/ardour/companion/record",
                "code": exc.code,
                "connection_state": "error",
            },
        )
        _raise(exc)


@router.get("/strips", response_model=ArdourStripsResponseV1)
async def ardour_strips() -> ArdourStripsResponseV1:
    settings = load_ardour_companion_settings()
    return companion.get_strips(settings=settings)


@router.post("/strips/{ssid}/fader", response_model=ArdourCommandAcceptedV1)
async def ardour_strip_fader(ssid: int, body: ArdourStripFloatValueV1) -> ArdourCommandAcceptedV1:
    settings = load_ardour_companion_settings()
    try:
        return companion.set_strip_fader(ssid, body.value, settings=settings)
    except ArdourCompanionError as exc:
        logger.info(
            "Ardour strip fader",
            extra={
                "path": "/ardour/companion/strips/fader",
                "code": exc.code,
                "connection_state": "error",
            },
        )
        _raise(exc)


@router.post("/strips/{ssid}/pan", response_model=ArdourCommandAcceptedV1)
async def ardour_strip_pan(ssid: int, body: ArdourStripFloatValueV1) -> ArdourCommandAcceptedV1:
    settings = load_ardour_companion_settings()
    try:
        return companion.set_strip_pan(ssid, body.value, settings=settings)
    except ArdourCompanionError as exc:
        logger.info(
            "Ardour strip pan",
            extra={
                "path": "/ardour/companion/strips/pan",
                "code": exc.code,
                "connection_state": "error",
            },
        )
        _raise(exc)


@router.post("/strips/{ssid}/mute", response_model=ArdourCommandAcceptedV1)
async def ardour_strip_mute(ssid: int, body: ArdourStripBoolValueV1) -> ArdourCommandAcceptedV1:
    settings = load_ardour_companion_settings()
    try:
        return companion.set_strip_mute(ssid, body.value, settings=settings)
    except ArdourCompanionError as exc:
        logger.info(
            "Ardour strip mute",
            extra={
                "path": "/ardour/companion/strips/mute",
                "code": exc.code,
                "connection_state": "error",
            },
        )
        _raise(exc)


@router.post("/strips/{ssid}/solo", response_model=ArdourCommandAcceptedV1)
async def ardour_strip_solo(ssid: int, body: ArdourStripBoolValueV1) -> ArdourCommandAcceptedV1:
    settings = load_ardour_companion_settings()
    try:
        return companion.set_strip_solo(ssid, body.value, settings=settings)
    except ArdourCompanionError as exc:
        logger.info(
            "Ardour strip solo",
            extra={
                "path": "/ardour/companion/strips/solo",
                "code": exc.code,
                "connection_state": "error",
            },
        )
        _raise(exc)
