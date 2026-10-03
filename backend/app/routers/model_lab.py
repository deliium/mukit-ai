"""Model Lab HTTP surface. Status is never on ``/ready``.

Opening catalog/list GETs never starts training. Mutating routes require
``MODEL_LAB_ENABLED`` and Collab C when collaboration is on.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.collaboration_schemas import CollaborationError
from app.collaboration_settings import collaboration_enabled
from app.model_lab_schemas import (
    EXPERIMENT_ID_PATTERN,
    ModelLabError,
    ModelLabStatusV1,
    map_model_lab_error_to_http,
    reject_model_lab_payload,
)
from app.model_lab_settings import load_model_lab_settings
from app.routers.collaboration_guard import raise_collaboration
from app.services.collaboration_access import (
    authorize_studio_lab,
    request_actor_header,
    resolve_request_actor,
)
from app.services.model_lab_catalog import list_dataset_catalog, list_presets
from app.services.model_lab_service import (
    compare_model_lab_experiments,
    create_model_lab_experiment,
    delete_model_lab_experiment,
    evaluate_model_lab_experiment,
    get_model_lab_experiment,
    get_model_lab_listening,
    get_model_lab_metrics,
    list_model_lab_checkpoints,
    list_model_lab_experiments,
    stop_model_lab_experiment,
)
from app.services.model_lab_store import register_checkpoint

logger = logging.getLogger(__name__)

router = APIRouter(tags=["model-lab"])


def _raise_lab(exc: ModelLabError) -> None:
    status, detail = map_model_lab_error_to_http(exc)
    raise HTTPException(status_code=status, detail=detail) from exc


def _known_id(experiment_id: str) -> str:
    if EXPERIMENT_ID_PATTERN.fullmatch(experiment_id) is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "model_lab_not_found",
                "message": "Model Lab experiment was not found.",
            },
        )
    return experiment_id


def _finish(method: str, experiment_id: str | None, status: int, started: float) -> None:
    logger.info(
        "Model Lab route finished",
        extra={
            "method": method,
            "experiment_id": experiment_id,
            "status_code": status,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        },
    )


def _mutate_owner() -> str | None:
    return authorize_studio_lab("train_model_lab")


@router.get("/model-lab/status")
def get_model_lab_status() -> dict[str, Any]:
    """Always 200. Not wired into ``/ready`` or ``/health``."""
    settings = load_model_lab_settings()
    body = ModelLabStatusV1(
        enabled=settings.enabled,
        fake=settings.fake,
        max_steps=settings.max_steps,
        max_batch=settings.max_batch,
        max_concurrent=settings.max_concurrent,
        max_compare=settings.max_compare,
        allow_accelerator=settings.allow_accelerator,
        retention=settings.retention,
        root_basename=settings.root.name,
    )
    return body.model_dump(mode="json")


@router.get("/model-lab/datasets")
def get_model_lab_datasets() -> list[dict[str, Any]]:
    settings = load_model_lab_settings()
    entries = list_dataset_catalog(include_lab_fixture=settings.fake)
    return [entry.model_dump(mode="json") for entry in entries]


@router.get("/model-lab/presets")
def get_model_lab_presets() -> dict[str, Any]:
    return list_presets().model_dump(mode="json")


@router.get("/model-lab/experiments")
def get_model_lab_experiments() -> list[dict[str, Any]]:
    started = time.perf_counter()
    status = 500
    try:
        filter_owner = collaboration_enabled()
        owner = None
        if filter_owner:
            owner = resolve_request_actor(request_actor_header())
        rows = list_model_lab_experiments(owner_actor_id=owner, filter_owner=filter_owner)
        status = 200
        return [row.model_dump(mode="json") for row in rows]
    except CollaborationError as exc:
        http_exc = raise_collaboration(exc)
        status = http_exc.status_code
        raise http_exc
    except ModelLabError as exc:
        status = exc.http_status
        _raise_lab(exc)
    finally:
        _finish("GET /model-lab/experiments", None, status, started)
    return []


@router.get("/model-lab/experiments/{experiment_id}")
def get_one_model_lab_experiment(experiment_id: str) -> dict[str, Any]:
    started = time.perf_counter()
    status = 500
    try:
        _known_id(experiment_id)
        row = get_model_lab_experiment(experiment_id)
        status = 200
        return row.model_dump(mode="json")
    except ModelLabError as exc:
        status = exc.http_status
        _raise_lab(exc)
    finally:
        _finish("GET detail", experiment_id, status, started)
    raise AssertionError("model lab get did not return")


@router.get("/model-lab/experiments/{experiment_id}/metrics")
def get_metrics(experiment_id: str) -> dict[str, Any]:
    try:
        _known_id(experiment_id)
        return get_model_lab_metrics(experiment_id).model_dump(mode="json")
    except ModelLabError as exc:
        _raise_lab(exc)
    raise AssertionError("model lab metrics did not return")


@router.get("/model-lab/experiments/{experiment_id}/checkpoints")
def get_checkpoints(experiment_id: str) -> list[dict[str, Any]]:
    try:
        _known_id(experiment_id)
        return [ref.model_dump(mode="json") for ref in list_model_lab_checkpoints(experiment_id)]
    except ModelLabError as exc:
        _raise_lab(exc)
    return []


@router.get("/model-lab/experiments/{experiment_id}/listening")
def get_listening(experiment_id: str) -> dict[str, Any]:
    try:
        _known_id(experiment_id)
        return get_model_lab_listening(experiment_id)
    except ModelLabError as exc:
        _raise_lab(exc)
    raise AssertionError("model lab listening did not return")


@router.post("/model-lab/experiments")
def post_model_lab_experiment(payload: dict[str, Any]) -> JSONResponse:
    started = time.perf_counter()
    status = 500
    experiment_id: str | None = None
    try:
        reject_model_lab_payload(payload)
        owner = _mutate_owner()
        row = create_model_lab_experiment(payload, owner_actor_id=owner)
        experiment_id = row.id
        status = 200
        logger.info(
            "Model Lab create route",
            extra={"experiment_id": experiment_id, "code": "ok"},
        )
        return JSONResponse(status_code=status, content=row.model_dump(mode="json"))
    except CollaborationError as exc:
        http_exc = raise_collaboration(exc)
        status = http_exc.status_code
        raise http_exc
    except ModelLabError as exc:
        status = exc.http_status
        _raise_lab(exc)
    finally:
        _finish("POST create", experiment_id, status, started)
    raise AssertionError("model lab create did not return")


@router.post("/model-lab/experiments/{experiment_id}/evaluate")
def post_evaluate(experiment_id: str) -> dict[str, Any]:
    started = time.perf_counter()
    status = 500
    try:
        _known_id(experiment_id)
        _mutate_owner()
        report = evaluate_model_lab_experiment(experiment_id)
        status = 200
        return report
    except CollaborationError as exc:
        http_exc = raise_collaboration(exc)
        status = http_exc.status_code
        raise http_exc
    except ModelLabError as exc:
        status = exc.http_status
        _raise_lab(exc)
    finally:
        _finish("POST evaluate", experiment_id, status, started)
    raise AssertionError("model lab evaluate did not return")


@router.post("/model-lab/experiments/{experiment_id}/stop")
def post_stop(experiment_id: str) -> dict[str, Any]:
    started = time.perf_counter()
    status = 500
    try:
        _known_id(experiment_id)
        _mutate_owner()
        row = stop_model_lab_experiment(experiment_id)
        status = 200
        return row.model_dump(mode="json")
    except CollaborationError as exc:
        http_exc = raise_collaboration(exc)
        status = http_exc.status_code
        raise http_exc
    except ModelLabError as exc:
        status = exc.http_status
        _raise_lab(exc)
    finally:
        _finish("POST stop", experiment_id, status, started)
    raise AssertionError("model lab stop did not return")


@router.post("/model-lab/experiments/{experiment_id}/register")
def post_register(experiment_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    status = 500
    try:
        _known_id(experiment_id)
        _mutate_owner()
        body = payload or {}
        reject_model_lab_payload(body)
        step = body.get("checkpoint_step")
        if step is None:
            refs = list_model_lab_checkpoints(experiment_id)
            if not refs:
                raise ModelLabError(
                    "model_lab_not_registerable",
                    "No checkpoint is available to register.",
                    http_status=409,
                )
            step = max(ref.step for ref in refs)
        row = register_checkpoint(experiment_id, checkpoint_step=int(step))
        status = 200
        logger.info(
            "Model Lab register route",
            extra={"experiment_id": experiment_id, "code": "ok"},
        )
        return row.model_dump(mode="json")
    except CollaborationError as exc:
        http_exc = raise_collaboration(exc)
        status = http_exc.status_code
        raise http_exc
    except ModelLabError as exc:
        status = exc.http_status
        _raise_lab(exc)
    finally:
        _finish("POST register", experiment_id, status, started)
    raise AssertionError("model lab register did not return")


@router.delete("/model-lab/experiments/{experiment_id}")
def delete_experiment(experiment_id: str) -> dict[str, Any]:
    started = time.perf_counter()
    status = 500
    try:
        _known_id(experiment_id)
        _mutate_owner()
        row = delete_model_lab_experiment(experiment_id)
        status = 200
        return row.model_dump(mode="json")
    except CollaborationError as exc:
        http_exc = raise_collaboration(exc)
        status = http_exc.status_code
        raise http_exc
    except ModelLabError as exc:
        status = exc.http_status
        _raise_lab(exc)
    finally:
        _finish("DELETE", experiment_id, status, started)
    raise AssertionError("model lab delete did not return")


@router.post("/model-lab/compare")
def post_compare(payload: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    status = 500
    try:
        reject_model_lab_payload(payload)
        _mutate_owner()
        ids = payload.get("experiment_ids")
        if not isinstance(ids, list):
            raise ModelLabError(
                "model_lab_payload_refused",
                "experiment_ids must be a list.",
                http_status=422,
            )
        report = compare_model_lab_experiments([str(item) for item in ids])
        status = 200
        logger.info(
            "Model Lab compare route",
            extra={"code": "ok", "id_count": len(ids)},
        )
        return report.model_dump(mode="json")
    except CollaborationError as exc:
        http_exc = raise_collaboration(exc)
        status = http_exc.status_code
        raise http_exc
    except ModelLabError as exc:
        status = exc.http_status
        _raise_lab(exc)
    finally:
        _finish("POST compare", None, status, started)
    raise AssertionError("model lab compare did not return")
