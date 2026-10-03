"""HTTP CRUD + compile for project spatial scenes."""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException

from app.routers.collaboration_guard import enforce_current
from app.services.spatial_presets import list_preset_catalog
from app.services.spatial_scene_service import (
    compile_spatial_scene,
    create_spatial_scene,
    delete_spatial_scene,
    get_spatial_scene,
    list_spatial_scenes,
    update_spatial_scene,
)
from app.spatial_schemas import (
    SpatialCompileRequest,
    SpatialCompileResponse,
    SpatialPresetCatalogResponse,
    SpatialSceneCreateRequest,
    SpatialSceneError,
    SpatialSceneGetResponse,
    SpatialSceneListResponse,
    SpatialSceneUpdateRequest,
    map_spatial_error_to_http,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/projects/{project_id}/spatial-scenes",
    tags=["spatial-scenes"],
)


def _raise(exc: SpatialSceneError) -> None:
    status, detail = map_spatial_error_to_http(exc)
    raise HTTPException(status_code=status, detail=detail) from exc


@router.get("/presets", response_model=SpatialPresetCatalogResponse)
async def get_spatial_presets(project_id: str) -> SpatialPresetCatalogResponse:
    started = time.perf_counter()
    enforce_current(project_id, "read")
    presets = list_preset_catalog()
    logger.debug(
        "GET spatial preset catalog",
        extra={
            "project_id": project_id,
            "preset_count": len(presets),
            "duration_ms": int((time.perf_counter() - started) * 1000),
        },
    )
    return SpatialPresetCatalogResponse(presets=presets)


@router.get("", response_model=SpatialSceneListResponse)
async def list_project_spatial_scenes(project_id: str) -> SpatialSceneListResponse:
    started = time.perf_counter()
    enforce_current(project_id, "read")
    try:
        body = list_spatial_scenes(project_id)
    except SpatialSceneError as exc:
        _raise(exc)
    logger.info(
        "GET spatial scenes",
        extra={
            "project_id": project_id,
            "scene_count": len(body.scenes),
            "http_status": 200,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        },
    )
    return body


@router.post("", response_model=SpatialSceneGetResponse, status_code=201)
async def create_project_spatial_scene(
    project_id: str,
    request: SpatialSceneCreateRequest,
) -> SpatialSceneGetResponse:
    started = time.perf_counter()
    enforce_current(project_id, "write_score")
    try:
        body = create_spatial_scene(project_id, request)
    except SpatialSceneError as exc:
        _raise(exc)
    logger.info(
        "POST spatial scene",
        extra={
            "project_id": project_id,
            "scene_id": body.scene.id,
            "source_count": len(body.scene.sources),
            "http_status": 201,
            "document_revision": body.document_revision,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        },
    )
    return body


@router.get("/{scene_id}", response_model=SpatialSceneGetResponse)
async def get_project_spatial_scene(
    project_id: str,
    scene_id: str,
) -> SpatialSceneGetResponse:
    started = time.perf_counter()
    enforce_current(project_id, "read")
    try:
        body = get_spatial_scene(project_id, scene_id)
    except SpatialSceneError as exc:
        _raise(exc)
    logger.info(
        "GET spatial scene",
        extra={
            "project_id": project_id,
            "scene_id": scene_id,
            "http_status": 200,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        },
    )
    return body


@router.put("/{scene_id}", response_model=SpatialSceneGetResponse)
async def put_project_spatial_scene(
    project_id: str,
    scene_id: str,
    request: SpatialSceneUpdateRequest,
) -> SpatialSceneGetResponse:
    started = time.perf_counter()
    enforce_current(project_id, "write_score")
    try:
        body = update_spatial_scene(project_id, scene_id, request)
    except SpatialSceneError as exc:
        _raise(exc)
    logger.info(
        "PUT spatial scene",
        extra={
            "project_id": project_id,
            "scene_id": scene_id,
            "document_revision": body.document_revision,
            "http_status": 200,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        },
    )
    return body


@router.delete("/{scene_id}", status_code=204)
async def delete_project_spatial_scene(project_id: str, scene_id: str) -> None:
    started = time.perf_counter()
    enforce_current(project_id, "write_score")
    try:
        delete_spatial_scene(project_id, scene_id)
    except SpatialSceneError as exc:
        _raise(exc)
    logger.info(
        "DELETE spatial scene",
        extra={
            "project_id": project_id,
            "scene_id": scene_id,
            "http_status": 204,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        },
    )


@router.post("/{scene_id}/compile", response_model=SpatialCompileResponse)
async def compile_project_spatial_scene(
    project_id: str,
    scene_id: str,
    request: SpatialCompileRequest,
) -> SpatialCompileResponse:
    started = time.perf_counter()
    enforce_current(project_id, "read")
    try:
        body = compile_spatial_scene(project_id, scene_id, request)
    except SpatialSceneError as exc:
        _raise(exc)
    logger.info(
        "POST spatial compile",
        extra={
            "project_id": project_id,
            "scene_id": scene_id,
            "source_count": body.preview.metrics.source_count,
            "stale_composition": body.preview.stale.composition,
            "stale_stem_set": body.preview.stale.stem_set,
            "http_status": 200,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        },
    )
    return body
