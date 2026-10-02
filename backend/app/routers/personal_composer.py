"""Personal composer jobs. Opening a list does not start training."""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.collaboration_schemas import CollaborationError
from app.personal_composer_schemas import (
    ADAPTER_ID_PATTERN,
    PersonalComposerError,
    map_personal_composer_error_to_http,
    reject_embedded_note_material,
)
from app.routers.collaboration_guard import raise_collaboration
from app.services.personal_composer_service import (
    delete_personal_composer,
    evaluate_personal_composer,
    get_personal_composer,
    list_personal_composers,
    resume_personal_composer,
    start_personal_composer,
    stop_personal_composer,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["personal-composer"])


def _raise_personal(exc: PersonalComposerError) -> None:
    status, detail = map_personal_composer_error_to_http(exc)
    raise HTTPException(status_code=status, detail=detail) from exc


def _known_id(adapter_id: str) -> str:
    if ADAPTER_ID_PATTERN.fullmatch(adapter_id) is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "personal_not_found", "message": "Personal composer was not found."},
        )
    return adapter_id


def _finish(method: str, adapter_id: str | None, status: int, started: float) -> None:
    logger.info(
        "Personal composer route finished",
        extra={
            "method": method,
            "adapter_id": adapter_id,
            "status_code": status,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        },
    )


@router.post("/personal-composers")
def post_personal_composer(payload: dict[str, Any]) -> JSONResponse:
    started = time.perf_counter()
    status = 500
    try:
        reject_embedded_note_material(payload)
        job = start_personal_composer(payload)
        status = 200
        return JSONResponse(status_code=status, content=job.model_dump(mode="json"))
    except CollaborationError as exc:
        http_exc = raise_collaboration(exc)
        status = http_exc.status_code
        raise http_exc
    except PersonalComposerError as exc:
        status = exc.http_status
        _raise_personal(exc)
    finally:
        _finish("POST", None, status, started)
    raise AssertionError("personal composer post did not return")


@router.get("/personal-composers")
def get_personal_composers() -> list[dict[str, Any]]:
    started = time.perf_counter()
    status = 500
    try:
        jobs = list_personal_composers()
        status = 200
        return [job.model_dump(mode="json") for job in jobs]
    except CollaborationError as exc:
        http_exc = raise_collaboration(exc)
        status = http_exc.status_code
        raise http_exc
    except PersonalComposerError as exc:
        status = exc.http_status
        _raise_personal(exc)
    finally:
        _finish("GET", None, status, started)
    return []


@router.get("/personal-composers/{adapter_id}")
def get_one_personal_composer(adapter_id: str) -> dict[str, Any]:
    started = time.perf_counter()
    status = 500
    try:
        _known_id(adapter_id)
        job = get_personal_composer(adapter_id)
        status = 200
        return job.model_dump(mode="json")
    except PersonalComposerError as exc:
        status = exc.http_status
        _raise_personal(exc)
    finally:
        _finish("GET", adapter_id, status, started)
    raise AssertionError("personal composer get did not return")


@router.post("/personal-composers/{adapter_id}/stop")
def post_stop_personal_composer(adapter_id: str) -> dict[str, Any]:
    started = time.perf_counter()
    status = 500
    try:
        _known_id(adapter_id)
        job = stop_personal_composer(adapter_id)
        status = 200
        return job.model_dump(mode="json")
    except PersonalComposerError as exc:
        status = exc.http_status
        _raise_personal(exc)
    finally:
        _finish("POST", adapter_id, status, started)
    raise AssertionError("personal composer stop did not return")


@router.post("/personal-composers/{adapter_id}/resume")
def post_resume_personal_composer(adapter_id: str) -> dict[str, Any]:
    started = time.perf_counter()
    status = 500
    try:
        _known_id(adapter_id)
        job = resume_personal_composer(adapter_id)
        status = 200
        return job.model_dump(mode="json")
    except PersonalComposerError as exc:
        status = exc.http_status
        _raise_personal(exc)
    finally:
        _finish("POST", adapter_id, status, started)
    raise AssertionError("personal composer resume did not return")


@router.post("/personal-composers/{adapter_id}/evaluate")
def post_evaluate_personal_composer(adapter_id: str) -> dict[str, Any]:
    started = time.perf_counter()
    status = 500
    try:
        _known_id(adapter_id)
        report = evaluate_personal_composer(adapter_id)
        status = 200
        return report.model_dump(mode="json")
    except PersonalComposerError as exc:
        status = exc.http_status
        _raise_personal(exc)
    finally:
        _finish("POST", adapter_id, status, started)
    raise AssertionError("personal composer evaluate did not return")


@router.delete("/personal-composers/{adapter_id}")
def delete_one_personal_composer(adapter_id: str) -> dict[str, Any]:
    started = time.perf_counter()
    status = 500
    try:
        _known_id(adapter_id)
        job = delete_personal_composer(adapter_id)
        status = 200
        return job.model_dump(mode="json")
    except PersonalComposerError as exc:
        status = exc.http_status
        _raise_personal(exc)
    finally:
        _finish("DELETE", adapter_id, status, started)
    raise AssertionError("personal composer delete did not return")
