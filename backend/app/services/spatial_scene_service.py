"""Orchestration for spatial scenes: CRUD, compile, preset clone."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from app.composition_schemas import CompositionV2
from app.services.composition_snapshot_encoding import composition_snapshot_fingerprint
from app.services.spatial_compiler import compile_spatial_preview
from app.services.spatial_presets import clone_preset, list_preset_catalog
from app.services.spatial_scene_store import (
    SpatialSceneRecord,
    create_scene,
    delete_scene,
    get_scene,
    is_stale_fingerprint,
    list_scenes,
    replace_scene,
)
from app.spatial_schemas import (
    SPATIAL_ERROR_CODES,
    SpatialCompileRequest,
    SpatialCompileResponse,
    SpatialSceneCreateRequest,
    SpatialSceneError,
    SpatialSceneGetResponse,
    SpatialSceneListResponse,
    SpatialSceneUpdateRequest,
    SpatialSceneV1,
    stem_set_fingerprint,
)

logger = logging.getLogger(__name__)


def _parse_composition(payload: dict[str, Any]) -> CompositionV2:
    try:
        return CompositionV2.model_validate(payload)
    except Exception as exc:
        raise SpatialSceneError(
            "composition_invalid",
            SPATIAL_ERROR_CODES["composition_invalid"],
            http_status=422,
        ) from exc


def _record_to_get(record: SpatialSceneRecord) -> SpatialSceneGetResponse:
    return SpatialSceneGetResponse(
        scene=record.scene,
        document_revision=record.document_revision,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def list_spatial_scenes(
    project_id: str,
    *,
    db_path: Path | None = None,
) -> SpatialSceneListResponse:
    return SpatialSceneListResponse(scenes=list_scenes(project_id, db_path=db_path))


def get_spatial_scene(
    project_id: str,
    scene_id: str,
    *,
    db_path: Path | None = None,
) -> SpatialSceneGetResponse:
    return _record_to_get(get_scene(project_id, scene_id, db_path=db_path))


def create_spatial_scene(
    project_id: str,
    request: SpatialSceneCreateRequest,
    *,
    db_path: Path | None = None,
) -> SpatialSceneGetResponse:
    fingerprint = request.source_composition_fingerprint
    if request.composition is not None:
        composition = _parse_composition(request.composition)
        fingerprint = composition_snapshot_fingerprint(composition)

    stem_fp = request.source_stem_set_fingerprint
    if request.stem_sha256_prefixes:
        stem_fp = stem_set_fingerprint(list(request.stem_sha256_prefixes))

    if request.scene is not None:
        scene = request.scene
        updates: dict[str, Any] = {}
        if fingerprint and not scene.source_composition_fingerprint:
            updates["source_composition_fingerprint"] = fingerprint
        if request.source_stem_set_id and not scene.source_stem_set_id:
            updates["source_stem_set_id"] = request.source_stem_set_id
        if stem_fp and not scene.source_stem_set_fingerprint:
            updates["source_stem_set_fingerprint"] = stem_fp
        if updates:
            scene = scene.model_copy(update=updates)
    elif request.preset_id:
        if not fingerprint:
            raise SpatialSceneError(
                "spatial_scene_invalid",
                "composition or source_composition_fingerprint is required when cloning a preset",
                http_status=422,
            )
        scene = clone_preset(
            request.preset_id,
            source_composition_fingerprint=fingerprint,
            name=request.name,
            project_id=project_id,
            source_stem_set_id=request.source_stem_set_id,
            source_stem_set_fingerprint=stem_fp,
        )
    else:
        raise SpatialSceneError(
            "spatial_scene_invalid",
            "Provide scene body or preset_id to clone",
            http_status=422,
        )

    scene = scene.model_copy(update={"id": None, "project_id": project_id})
    if request.name:
        scene = scene.model_copy(update={"name": request.name.strip()})
    record = create_scene(project_id, scene, db_path=db_path)
    logger.info(
        "Service created spatial scene",
        extra={
            "scene_id": record.id,
            "project_id": project_id,
            "source_count": len(record.scene.sources),
            "preset_id": request.preset_id,
        },
    )
    return _record_to_get(record)


def update_spatial_scene(
    project_id: str,
    scene_id: str,
    request: SpatialSceneUpdateRequest,
    *,
    db_path: Path | None = None,
) -> SpatialSceneGetResponse:
    record = replace_scene(
        project_id,
        scene_id,
        request.scene,
        expected_document_revision=request.expected_document_revision,
        db_path=db_path,
    )
    return _record_to_get(record)


def delete_spatial_scene(
    project_id: str,
    scene_id: str,
    *,
    db_path: Path | None = None,
) -> None:
    delete_scene(project_id, scene_id, db_path=db_path)


def compile_spatial_scene(
    project_id: str,
    scene_id: str,
    request: SpatialCompileRequest,
    *,
    db_path: Path | None = None,
) -> SpatialCompileResponse:
    started = time.perf_counter()
    record = get_scene(project_id, scene_id, db_path=db_path)
    scene: SpatialSceneV1 = request.scene if request.scene is not None else record.scene

    has_track = any(s.source_kind == "track" for s in scene.sources)
    has_stem = any(s.source_kind == "stem" for s in scene.sources)

    composition: CompositionV2 | None = None
    request_comp_fp: str | None = None
    track_ids: set[str] | None = None
    if has_track:
        if request.composition is None:
            raise SpatialSceneError(
                "composition_required",
                SPATIAL_ERROR_CODES["composition_required"],
                http_status=422,
            )
        composition = _parse_composition(request.composition)
        request_comp_fp = composition_snapshot_fingerprint(composition)
        track_ids = {t.id for t in composition.tracks}

    request_stem_fp: str | None = None
    stem_ids: set[str] | None = None
    if has_stem:
        if not request.stem_set_id and not scene.source_stem_set_id:
            raise SpatialSceneError(
                "stem_set_required",
                SPATIAL_ERROR_CODES["stem_set_required"],
                http_status=422,
            )
        if request.stems:
            stem_ids = {s.stem_id for s in request.stems}
            prefixes = [s.sha256_prefix for s in request.stems]
            request_stem_fp = stem_set_fingerprint(prefixes)
        elif request.stem_set_fingerprint:
            request_stem_fp = request.stem_set_fingerprint
            stem_ids = None  # cannot resolve without stems list
        else:
            raise SpatialSceneError(
                "stem_set_required",
                SPATIAL_ERROR_CODES["stem_set_required"],
                http_status=422,
            )

    stale_comp = False
    if request_comp_fp is not None:
        stale_comp = is_stale_fingerprint(
            scene.source_composition_fingerprint, request_comp_fp
        )
    stale_stem = False
    if request_stem_fp is not None and scene.source_stem_set_fingerprint:
        stale_stem = is_stale_fingerprint(
            scene.source_stem_set_fingerprint, request_stem_fp
        )

    preview = compile_spatial_preview(
        scene,
        scene_revision=record.document_revision,
        at_tick=int(request.at_tick),
        at_seconds=request.at_seconds,
        track_ids=track_ids,
        stem_ids=stem_ids,
        stale_composition=stale_comp,
        stale_stem_set=stale_stem,
        request_composition_fingerprint=request_comp_fp,
        request_stem_set_fingerprint=request_stem_fp,
    )
    duration_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "Compiled spatial scene",
        extra={
            "scene_id": scene_id,
            "project_id": project_id,
            "source_count": preview.metrics.source_count,
            "track_count": preview.metrics.track_count,
            "stem_count": preview.metrics.stem_count,
            "stale_composition": stale_comp,
            "stale_stem_set": stale_stem,
            "duration_ms": duration_ms,
            "mean_distance": round(preview.metrics.mean_distance, 4),
        },
    )
    if stale_comp or stale_stem:
        logger.warning(
            "Spatial scene soft-stale on compile",
            extra={
                "code": "spatial_scene_stale",
                "scene_id": scene_id,
                "stale_composition": stale_comp,
                "stale_stem_set": stale_stem,
            },
        )
    return SpatialCompileResponse(preview=preview)


# Re-export catalog helper for router convenience.
__all__ = [
    "list_spatial_scenes",
    "get_spatial_scene",
    "create_spatial_scene",
    "update_spatial_scene",
    "delete_spatial_scene",
    "compile_spatial_scene",
    "list_preset_catalog",
]
