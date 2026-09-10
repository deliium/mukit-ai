"""HTTP routes for local project / composition persistence."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ..project_history_schemas import (
    DEFAULT_REVISION_PAGE_LIMIT,
    MAX_REVISION_PAGE_LIMIT,
    ApplyAsBranchRequest,
    BranchCheckoutRequest,
    BranchCreateRequest,
    BranchListItem,
    BranchListResponse,
    BranchRenameRequest,
    DurableCommitRequest,
    DurableCommandResponse,
    ProjectRevisionConflictBody,
    RestoreRevisionRequest,
    RevisionDetailResponse,
    RevisionListItem,
    RevisionListResponse,
    RevisionNameRequest,
)
from ..project_schemas import (
    ProjectCreateRequest,
    ProjectDetailResponse,
    ProjectDuplicateResponse,
    ProjectListItem,
    ProjectListResponse,
    ProjectPatchRequest,
)
from ..services.composition_change_summary import CompositionScopeError
from ..services.persistence_secret_guard import PersistenceSecretError
from ..services.project_composition import (
    ProjectCompositionError,
    composition_to_storage_json,
    normalize_project_composition,
)
from ..services.project_history import (
    ProjectHistoryError,
    ProjectHistoryNotFoundError,
    ProjectHistoryValidationError,
    apply_as_branch_command,
    checkout_branch_command,
    commit_revision,
    create_branch,
    get_revision_detail,
    list_branches,
    list_revisions,
    name_revision,
    project_history_detail_fields,
    rename_branch,
    restore_revision_command,
)
from ..services.project_history_store import ProjectRevisionConflictError
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

_CONFLICT_RESPONSE = {
    409: {
        "model": ProjectRevisionConflictBody,
        "description": "Stale branch/head/working preconditions",
    }
}


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


def _history_fields(project_id: str) -> dict[str, Any]:
    try:
        return project_history_detail_fields(project_id)
    except ProjectNotFoundError:
        return {
            "active_branch_id": None,
            "active_branch_name": None,
            "current_revision_id": None,
            "current_revision_sequence": None,
            "working_version": None,
            "working_fingerprint": None,
        }


def _record_to_detail(
    record: ProjectRecord,
    *,
    composition=None,
    composition_migrated: bool = False,
    migration_path: str | None = None,
    history_fields: dict[str, Any] | None = None,
) -> ProjectDetailResponse:
    fields = history_fields if history_fields is not None else _history_fields(record.id)
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
        **fields,
    )


def _durable_to_detail(project_id: str, result: DurableCommandResponse) -> ProjectDetailResponse:
    record = get_project(project_id)
    return _record_to_detail(
        record,
        composition=result.composition,
        history_fields={
            "active_branch_id": result.active_branch_id,
            "active_branch_name": result.active_branch_name,
            "current_revision_id": result.current_revision_id,
            "current_revision_sequence": result.current_revision_sequence,
            "working_version": result.working_version,
            "working_fingerprint": result.working_fingerprint,
        },
    )


def _map_history_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ProjectNotFoundError):
        logger.warning(
            "Project history not found",
            extra={"code": "project_not_found", "detail": str(exc)[:200]},
        )
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ProjectHistoryNotFoundError):
        logger.warning(
            "History entity not found",
            extra={"code": "history_not_found", "detail": str(exc)[:200]},
        )
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ProjectRevisionConflictError):
        body = ProjectRevisionConflictBody.model_validate(exc.bounded_detail())
        logger.warning(
            "Project revision conflict",
            extra={
                "code": body.code,
                "project_id": body.project_id,
                "expected_working_version": body.expected_working_version,
                "current_working_version": body.current_working_version,
            },
        )
        return HTTPException(status_code=409, detail=body.model_dump(mode="json"))
    if isinstance(
        exc,
        (
            ProjectHistoryValidationError,
            ProjectHistoryError,
            CompositionScopeError,
            PersistenceSecretError,
            ProjectCompositionError,
        ),
    ):
        code = getattr(exc, "code", type(exc).__name__)
        logger.warning(
            "Project history validation rejected",
            extra={"code": code, "detail": str(exc)[:200]},
        )
        return HTTPException(status_code=422, detail=str(exc))
    logger.error(
        "Unexpected project history error",
        extra={"error_type": type(exc).__name__},
    )
    return HTTPException(status_code=500, detail="Internal server error")


def _open_composition(record: ProjectRecord, *, rewrite: bool = True):
    if not record.composition_json:
        return None, False, None, record

    normalized = normalize_project_composition(
        record.composition_json,
        project_id=record.id,
        persist_canonical=rewrite,
    )
    migrated = normalized.migration_path in {"legacy", "v1_to_v2"}
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
                "migration_path": normalized.migration_path,
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
    logger.info(
        "Project create completed",
        extra={
            "project_id": record.id,
            "active_branch_id": record.active_branch_id,
            "current_revision_id": record.current_revision_id,
        },
    )
    return _record_to_detail(
        record,
        composition=composition,
        composition_migrated=migration_path == "legacy",
        migration_path=migration_path,
    )


@router.get("/{project_id}/revisions", response_model=RevisionListResponse)
async def list_project_revisions(
    project_id: str,
    branch_id: str | None = None,
    limit: int = Query(default=DEFAULT_REVISION_PAGE_LIMIT, ge=1, le=MAX_REVISION_PAGE_LIMIT),
    before_sequence: int | None = Query(default=None, ge=1),
) -> RevisionListResponse:
    logger.info(
        "Revision list requested",
        extra={
            "project_id": project_id,
            "branch_id": branch_id,
            "limit": limit,
            "before_sequence": before_sequence,
        },
    )
    try:
        response = list_revisions(
            project_id,
            branch_id=branch_id,
            limit=limit,
            before_sequence=before_sequence,
        )
    except (ProjectNotFoundError, ProjectHistoryNotFoundError) as exc:
        raise _map_history_error(exc) from exc
    logger.debug(
        "Revision list ready",
        extra={
            "project_id": project_id,
            "count": len(response.revisions),
            "has_more": response.next_before_sequence is not None,
        },
    )
    return response


@router.get("/{project_id}/revisions/{revision_id}", response_model=RevisionDetailResponse)
async def get_project_revision(project_id: str, revision_id: str) -> RevisionDetailResponse:
    logger.info(
        "Revision detail requested",
        extra={"project_id": project_id, "revision_id": revision_id},
    )
    try:
        detail = get_revision_detail(project_id, revision_id)
    except (ProjectNotFoundError, ProjectHistoryNotFoundError, ProjectHistoryError) as exc:
        raise _map_history_error(exc) from exc
    logger.info(
        "Revision detail ready",
        extra={
            "project_id": project_id,
            "revision_id": revision_id,
            "has_composition": detail.composition is not None,
        },
    )
    return detail


@router.post(
    "/{project_id}/revisions",
    response_model=DurableCommandResponse,
    responses=_CONFLICT_RESPONSE,
)
async def commit_project_revision(
    project_id: str,
    request: DurableCommitRequest,
) -> DurableCommandResponse:
    logger.info(
        "Durable revision commit requested",
        extra={
            "project_id": project_id,
            "branch_id": request.branch_id,
            "operation_type": request.operation_type.value,
            "checkpoint_dirty_draft": request.checkpoint_dirty_draft,
        },
    )
    try:
        result = commit_revision(project_id, request)
    except (
        ProjectNotFoundError,
        ProjectHistoryNotFoundError,
        ProjectHistoryValidationError,
        ProjectHistoryError,
        ProjectRevisionConflictError,
        CompositionScopeError,
        PersistenceSecretError,
        ProjectCompositionError,
    ) as exc:
        raise _map_history_error(exc) from exc
    logger.info(
        "Durable revision commit completed",
        extra={
            "project_id": project_id,
            "branch_id": result.active_branch_id,
            "revision_id": result.current_revision_id,
            "revision_created": result.revision_created,
            "created_count": len(result.created_revision_ids),
        },
    )
    return result


@router.patch("/{project_id}/revisions/{revision_id}", response_model=RevisionListItem)
async def name_project_revision(
    project_id: str,
    revision_id: str,
    request: RevisionNameRequest,
) -> RevisionListItem:
    logger.info(
        "Revision name requested",
        extra={
            "project_id": project_id,
            "revision_id": revision_id,
            "name_length": len(request.name or ""),
        },
    )
    try:
        item = name_revision(project_id, revision_id, request)
    except (
        ProjectNotFoundError,
        ProjectHistoryNotFoundError,
        PersistenceSecretError,
    ) as exc:
        raise _map_history_error(exc) from exc
    logger.info(
        "Revision name completed",
        extra={"project_id": project_id, "revision_id": revision_id},
    )
    return item


@router.post(
    "/{project_id}/revisions/{revision_id}/restore",
    response_model=DurableCommandResponse,
    responses=_CONFLICT_RESPONSE,
)
async def restore_project_revision(
    project_id: str,
    revision_id: str,
    request: RestoreRevisionRequest,
) -> DurableCommandResponse:
    logger.info(
        "Revision restore requested",
        extra={
            "project_id": project_id,
            "revision_id": revision_id,
            "branch_id": request.branch_id,
        },
    )
    try:
        result = restore_revision_command(project_id, revision_id, request)
    except (
        ProjectNotFoundError,
        ProjectHistoryNotFoundError,
        ProjectHistoryValidationError,
        ProjectHistoryError,
        ProjectRevisionConflictError,
        ProjectCompositionError,
    ) as exc:
        raise _map_history_error(exc) from exc
    logger.info(
        "Revision restore completed",
        extra={
            "project_id": project_id,
            "revision_id": result.current_revision_id,
            "created_count": len(result.created_revision_ids),
        },
    )
    return result


@router.get("/{project_id}/branches", response_model=BranchListResponse)
async def list_project_branches(project_id: str) -> BranchListResponse:
    logger.info("Branch list requested", extra={"project_id": project_id})
    try:
        response = list_branches(project_id)
    except ProjectNotFoundError as exc:
        raise _map_history_error(exc) from exc
    logger.debug(
        "Branch list ready",
        extra={"project_id": project_id, "count": len(response.branches)},
    )
    return response


@router.post(
    "/{project_id}/branches",
    response_model=BranchListItem,
    status_code=201,
    responses=_CONFLICT_RESPONSE,
)
async def create_project_branch(
    project_id: str,
    request: BranchCreateRequest,
) -> BranchListItem:
    logger.info(
        "Branch create requested",
        extra={
            "project_id": project_id,
            "name_length": len(request.name),
            "checkout": request.checkout,
        },
    )
    try:
        item = create_branch(project_id, request)
    except (
        ProjectNotFoundError,
        ProjectHistoryNotFoundError,
        ProjectHistoryValidationError,
        ProjectHistoryError,
        ProjectRevisionConflictError,
        PersistenceSecretError,
        ProjectCompositionError,
    ) as exc:
        raise _map_history_error(exc) from exc
    logger.info(
        "Branch create completed",
        extra={
            "project_id": project_id,
            "branch_id": item.id,
            "is_active": item.is_active,
        },
    )
    return item


@router.post(
    "/{project_id}/branches/apply-as-branch",
    response_model=DurableCommandResponse,
    responses=_CONFLICT_RESPONSE,
)
async def apply_as_branch_route(
    project_id: str,
    request: ApplyAsBranchRequest,
) -> DurableCommandResponse:
    logger.info(
        "Apply-as-branch requested",
        extra={
            "project_id": project_id,
            "source_branch_id": request.source_branch_id,
            "operation_type": request.operation_type.value,
            "name_length": len(request.name),
        },
    )
    try:
        result = apply_as_branch_command(project_id, request)
    except (
        ProjectNotFoundError,
        ProjectHistoryNotFoundError,
        ProjectHistoryValidationError,
        ProjectHistoryError,
        ProjectRevisionConflictError,
        CompositionScopeError,
        PersistenceSecretError,
        ProjectCompositionError,
    ) as exc:
        raise _map_history_error(exc) from exc
    logger.info(
        "Apply-as-branch completed",
        extra={
            "project_id": project_id,
            "branch_id": result.active_branch_id,
            "revision_id": result.current_revision_id,
        },
    )
    return result


@router.patch("/{project_id}/branches/{branch_id}", response_model=BranchListItem)
async def rename_project_branch(
    project_id: str,
    branch_id: str,
    request: BranchRenameRequest,
) -> BranchListItem:
    logger.info(
        "Branch rename requested",
        extra={
            "project_id": project_id,
            "branch_id": branch_id,
            "name_length": len(request.name),
        },
    )
    try:
        item = rename_branch(project_id, branch_id, request)
    except (
        ProjectNotFoundError,
        ProjectHistoryNotFoundError,
        ProjectHistoryValidationError,
        PersistenceSecretError,
    ) as exc:
        raise _map_history_error(exc) from exc
    logger.info(
        "Branch rename completed",
        extra={"project_id": project_id, "branch_id": branch_id},
    )
    return item


@router.post(
    "/{project_id}/branches/{branch_id}/checkout",
    response_model=ProjectDetailResponse,
    responses=_CONFLICT_RESPONSE,
)
async def checkout_project_branch(
    project_id: str,
    branch_id: str,
    request: BranchCheckoutRequest,
) -> ProjectDetailResponse:
    logger.info(
        "Branch checkout requested",
        extra={
            "project_id": project_id,
            "branch_id": branch_id,
            "expected_active_branch_id": request.expected_active_branch_id,
        },
    )
    try:
        result = checkout_branch_command(project_id, branch_id, request)
        detail = _durable_to_detail(project_id, result)
    except (
        ProjectNotFoundError,
        ProjectHistoryNotFoundError,
        ProjectHistoryValidationError,
        ProjectHistoryError,
        ProjectRevisionConflictError,
        ProjectCompositionError,
    ) as exc:
        raise _map_history_error(exc) from exc
    logger.info(
        "Branch checkout completed",
        extra={
            "project_id": project_id,
            "active_branch_id": detail.active_branch_id,
            "current_revision_id": detail.current_revision_id,
        },
    )
    return detail


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
            "active_branch_id": record.active_branch_id,
            "current_revision_id": record.current_revision_id,
        },
    )
    return _record_to_detail(
        record,
        composition=composition,
        composition_migrated=migrated,
        migration_path=migration_path,
    )


@router.patch(
    "/{project_id}",
    response_model=ProjectDetailResponse,
    responses=_CONFLICT_RESPONSE,
)
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
            "has_working_preconditions": request.expected_working_version is not None,
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
            branch_id=request.branch_id,
            expected_active_branch_id=request.expected_active_branch_id,
            expected_working_version=request.expected_working_version,
            expected_source_fingerprint=request.expected_source_fingerprint,
            **generation_kwargs,
        )
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProjectRevisionConflictError as exc:
        raise _map_history_error(exc) from exc
    except ProjectCompositionError as exc:
        raise _map_history_error(exc) from exc

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
            "active_branch_id": record.active_branch_id,
            "current_revision_id": record.current_revision_id,
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
        extra={
            "source_project_id": project_id,
            "project_id": record.id,
            "active_branch_id": record.active_branch_id,
        },
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
