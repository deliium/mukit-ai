"""HTTP routes for local project / composition persistence."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from ..project_schemas import (
    ProjectCreateRequest,
    ProjectDetailResponse,
    ProjectDuplicateResponse,
    ProjectListItem,
    ProjectListResponse,
    ProjectPatchRequest,
)
from ..services.project_composition import (
    ProjectCompositionError,
    composition_to_storage_json,
    normalize_project_composition,
)
from ..services.project_store import (
    ProjectNotFoundError,
    ProjectRecord,
    create_project,
    delete_project,
    duplicate_project,
    get_project,
    list_projects,
    update_project,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["projects"])


def _prompt_dict(record: ProjectRecord) -> dict[str, Any] | None:
    if not record.generation_prompt_json:
        return None
    try:
        parsed = json.loads(record.generation_prompt_json)
    except json.JSONDecodeError:
        logger.warning(
            "Stored generation prompt JSON is not parseable",
            extra={"project_id": record.id},
        )
        return None
    return parsed if isinstance(parsed, dict) else None


def _generation_kwargs(generation) -> dict[str, Any]:
    if generation is None:
        return {
            "generation_provider": None,
            "generation_model": None,
            "generation_prompt": None,
        }
    prompt = generation.prompt
    if hasattr(prompt, "model_dump"):
        prompt = prompt.model_dump(mode="json")
    return {
        "generation_provider": generation.provider,
        "generation_model": generation.model,
        "generation_prompt": prompt,
    }


def _record_to_detail(
    record: ProjectRecord,
    *,
    composition=None,
    composition_migrated: bool = False,
    migration_path: str | None = None,
) -> ProjectDetailResponse:
    return ProjectDetailResponse(
        id=record.id,
        name=record.name,
        created_at=record.created_at,
        updated_at=record.updated_at,
        composition=composition,
        generation_provider=record.generation_provider,
        generation_model=record.generation_model,
        generation_prompt=_prompt_dict(record),
        composition_migrated=composition_migrated,
        migration_path=migration_path,
    )


def _open_composition(record: ProjectRecord, *, rewrite: bool = True):
    if not record.composition_json:
        return None, False, None, record

    normalized = normalize_project_composition(
        record.composition_json,
        project_id=record.id,
        persist_canonical=rewrite,
    )
    migrated = normalized.migration_path == "legacy"
    updated_record = record
    if normalized.rewritten:
        canonical_json = composition_to_storage_json(normalized.composition)
        updated_record = update_project(
            record.id,
            composition=canonical_json,
        )
        logger.info(
            "Rewrote migrated composition to database",
            extra={
                "project_id": record.id,
                "previous_schema_version": normalized.previous_schema_version,
                "schema_version": normalized.composition.schema_version,
            },
        )
    return normalized.composition, migrated, normalized.migration_path, updated_record


@router.get("", response_model=ProjectListResponse)
async def list_project_summaries() -> ProjectListResponse:
    logger.info("Project list requested")
    records = list_projects()
    items = []
    for record in records:
        summary = record.composition_summary()
        items.append(
            ProjectListItem(
                id=record.id,
                name=record.name,
                created_at=record.created_at,
                updated_at=record.updated_at,
                has_composition=summary["has_composition"],
                track_count=summary["track_count"],
                event_count=summary["event_count"],
                bar_count=summary["bar_count"],
            )
        )
    logger.debug("Project list response ready", extra={"count": len(items)})
    return ProjectListResponse(projects=items)


@router.post("", response_model=ProjectDetailResponse, status_code=201)
async def create_project_route(request: ProjectCreateRequest) -> ProjectDetailResponse:
    logger.info(
        "Project create requested",
        extra={
            "name_length": len(request.name),
            "has_composition": request.composition is not None,
            "has_generation": request.generation is not None,
        },
    )
    composition_json = None
    migration_path = None
    composition = None
    if request.composition is not None:
        try:
            normalized = normalize_project_composition(
                request.composition,
                persist_canonical=True,
            )
        except ProjectCompositionError as exc:
            logger.warning(
                "Project create composition validation failed",
                extra={"detail": str(exc)[:300]},
            )
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        composition = normalized.composition
        composition_json = composition_to_storage_json(composition)
        migration_path = normalized.migration_path
        logger.debug(
            "Project create composition accepted",
            extra={
                "migration_path": migration_path,
                "json_length": len(composition_json),
                "track_count": len(composition.tracks),
                "event_count": sum(len(track.events) for track in composition.tracks),
            },
        )

    generation_kwargs = _generation_kwargs(request.generation)
    record = create_project(
        request.name,
        composition=composition_json,
        **generation_kwargs,
    )
    logger.info("Project create completed", extra={"project_id": record.id})
    return _record_to_detail(
        record,
        composition=composition,
        composition_migrated=migration_path == "legacy",
        migration_path=migration_path,
    )


@router.get("/{project_id}", response_model=ProjectDetailResponse)
async def get_project_route(project_id: str) -> ProjectDetailResponse:
    logger.info("Project open requested", extra={"project_id": project_id})
    try:
        record = get_project(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    try:
        composition, migrated, migration_path, record = _open_composition(record, rewrite=True)
    except ProjectCompositionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    logger.info(
        "Project open completed",
        extra={
            "project_id": project_id,
            "composition_migrated": migrated,
            "migration_path": migration_path,
            "has_composition": composition is not None,
        },
    )
    return _record_to_detail(
        record,
        composition=composition,
        composition_migrated=migrated,
        migration_path=migration_path,
    )


@router.patch("/{project_id}", response_model=ProjectDetailResponse)
async def patch_project_route(project_id: str, request: ProjectPatchRequest) -> ProjectDetailResponse:
    logger.info(
        "Project patch requested",
        extra={
            "project_id": project_id,
            "has_name": request.name is not None,
            "has_composition": request.composition is not None,
            "has_generation": request.generation is not None,
            "clear_composition": request.clear_composition,
            "clear_generation": request.clear_generation,
        },
    )
    composition_json = None
    composition = None
    migration_path = None
    migrated = False
    if request.composition is not None:
        try:
            normalized = normalize_project_composition(
                request.composition,
                project_id=project_id,
                persist_canonical=True,
            )
        except ProjectCompositionError as exc:
            logger.warning(
                "Project patch composition validation failed",
                extra={"project_id": project_id, "detail": str(exc)[:300]},
            )
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        composition = normalized.composition
        composition_json = composition_to_storage_json(composition)
        migration_path = normalized.migration_path
        logger.debug(
            "Project patch composition accepted",
            extra={
                "project_id": project_id,
                "migration_path": migration_path,
                "json_length": len(composition_json),
                "track_count": len(composition.tracks),
                "event_count": sum(len(track.events) for track in composition.tracks),
            },
        )

    generation_kwargs: dict[str, Any] = {}
    if request.clear_generation:
        generation_kwargs["clear_generation_meta"] = True
    elif request.generation is not None:
        generation_kwargs.update(_generation_kwargs(request.generation))

    try:
        record = update_project(
            project_id,
            name=request.name,
            composition=composition_json,
            clear_composition=request.clear_composition,
            **generation_kwargs,
        )
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if composition is None and not request.clear_composition and record.composition_json:
        try:
            composition, migrated, migration_path, record = _open_composition(record, rewrite=False)
        except ProjectCompositionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    else:
        migrated = migration_path == "legacy"

    logger.info(
        "Project patch completed",
        extra={
            "project_id": project_id,
            "composition_migrated": migrated if request.composition is not None else False,
        },
    )
    return _record_to_detail(
        record,
        composition=composition,
        composition_migrated=bool(migration_path == "legacy"),
        migration_path=migration_path,
    )


@router.post("/{project_id}/duplicate", response_model=ProjectDuplicateResponse, status_code=201)
async def duplicate_project_route(project_id: str) -> ProjectDuplicateResponse:
    logger.info("Project duplicate requested", extra={"project_id": project_id})
    try:
        record = duplicate_project(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    try:
        composition, migrated, migration_path, record = _open_composition(record, rewrite=True)
    except ProjectCompositionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    logger.info(
        "Project duplicate completed",
        extra={"source_project_id": project_id, "project_id": record.id},
    )
    return ProjectDuplicateResponse(
        **_record_to_detail(
            record,
            composition=composition,
            composition_migrated=migrated,
            migration_path=migration_path,
        ).model_dump()
    )


@router.delete("/{project_id}", status_code=204)
async def delete_project_route(project_id: str) -> None:
    logger.info("Project delete requested", extra={"project_id": project_id})
    try:
        delete_project(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    logger.info("Project delete completed", extra={"project_id": project_id})
