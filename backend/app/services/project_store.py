"""SQLite-backed repository for local project / composition persistence."""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..db.connection import get_connection, get_project_db_path
from .project_composition import ProjectCompositionError
from .project_history_store import (
    ensure_project_history,
    rename_project_fields,
    save_branch_draft,
)

logger = logging.getLogger(__name__)


class ProjectNotFoundError(LookupError):
    """Raised when a project id does not exist."""


@dataclass(frozen=True)
class ProjectRecord:
    id: str
    name: str
    composition_json: str | None
    generation_provider: str | None
    generation_model: str | None
    generation_prompt_json: str | None
    created_at: str
    updated_at: str
    active_branch_id: str | None = None
    current_revision_id: str | None = None

    @property
    def has_composition(self) -> bool:
        return bool(self.composition_json)

    def composition_summary(self) -> dict[str, Any]:
        """Return cheap composition counts without requiring full validation."""
        if not self.composition_json:
            return {
                "has_composition": False,
                "track_count": 0,
                "event_count": 0,
                "bar_count": 0,
            }
        try:
            payload = json.loads(self.composition_json)
        except json.JSONDecodeError:
            logger.warning(
                "Stored composition JSON is not parseable for summary",
                extra={"project_id": self.id},
            )
            return {
                "has_composition": True,
                "track_count": 0,
                "event_count": 0,
                "bar_count": 0,
            }

        tracks = payload.get("tracks") if isinstance(payload, dict) else None
        track_count = len(tracks) if isinstance(tracks, list) else 0
        event_count = 0
        if isinstance(tracks, list):
            for track in tracks:
                events = track.get("events") if isinstance(track, dict) else None
                if isinstance(events, list):
                    event_count += len(events)

        sections = payload.get("sections") if isinstance(payload, dict) else None
        bar_count = 0
        if isinstance(payload, dict) and isinstance(payload.get("bar_count"), int):
            bar_count = payload["bar_count"]
        elif isinstance(sections, list):
            for section in sections:
                if isinstance(section, dict):
                    bars = section.get("bars")
                    if not isinstance(bars, int):
                        bars = section.get("bar_count")
                    if isinstance(bars, int):
                        bar_count += bars

        return {
            "has_composition": True,
            "track_count": track_count,
            "event_count": event_count,
            "bar_count": bar_count,
        }


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


_PROJECT_COLUMNS = """
    id, name, composition_json, generation_provider, generation_model,
    generation_prompt_json, created_at, updated_at,
    active_branch_id, current_revision_id
"""


def _row_to_record(row: sqlite3.Row) -> ProjectRecord:
    keys = set(row.keys())
    return ProjectRecord(
        id=row["id"],
        name=row["name"],
        composition_json=row["composition_json"],
        generation_provider=row["generation_provider"],
        generation_model=row["generation_model"],
        generation_prompt_json=row["generation_prompt_json"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        active_branch_id=row["active_branch_id"] if "active_branch_id" in keys else None,
        current_revision_id=(
            row["current_revision_id"] if "current_revision_id" in keys else None
        ),
    )


def _serialize_prompt_json(prompt: Any | None) -> str | None:
    if prompt is None:
        return None
    if isinstance(prompt, str):
        return prompt
    return json.dumps(prompt, ensure_ascii=False, separators=(",", ":"))


def _composition_payload_to_text(composition: Any | None) -> str | None:
    if composition is None:
        return None
    if isinstance(composition, str):
        return composition
    if hasattr(composition, "model_dump"):
        return json.dumps(
            composition.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
        )
    return json.dumps(composition, ensure_ascii=False, separators=(",", ":"))


def list_projects(db_path: Path | str | None = None) -> list[ProjectRecord]:
    """List projects ordered by most recently updated."""
    path = Path(db_path) if db_path is not None else get_project_db_path()
    logger.debug("Listing projects", extra={"project_db_path": str(path)})
    try:
        with get_connection(path) as conn:
            rows = conn.execute(
                f"""
                SELECT {_PROJECT_COLUMNS}
                FROM projects
                ORDER BY updated_at DESC
                """
            ).fetchall()
        records = [_row_to_record(row) for row in rows]
        logger.debug("Listed projects", extra={"count": len(records)})
        return records
    except Exception as exc:
        logger.error(
            "Failed to list projects",
            extra={"error_type": type(exc).__name__, "error_detail": str(exc)[:300]},
        )
        raise


def create_project(
    name: str,
    *,
    composition: Any | None = None,
    generation_provider: str | None = None,
    generation_model: str | None = None,
    generation_prompt: Any | None = None,
    project_id: str | None = None,
    db_path: Path | str | None = None,
) -> ProjectRecord:
    """Create a new project row and return it."""
    path = Path(db_path) if db_path is not None else get_project_db_path()
    new_id = project_id or str(uuid.uuid4())
    now = _utc_now_iso()
    composition_json = _composition_payload_to_text(composition)
    prompt_json = _serialize_prompt_json(generation_prompt)
    logger.info(
        "Creating project",
        extra={
            "project_id": new_id,
            "name_length": len(name),
            "has_composition": composition_json is not None,
            "generation_provider": generation_provider,
            "generation_model": generation_model,
        },
    )
    try:
        with get_connection(path) as conn:
            conn.execute(
                """
                INSERT INTO projects (
                    id, name, composition_json, generation_provider, generation_model,
                    generation_prompt_json, created_at, updated_at,
                    active_branch_id, current_revision_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
                """,
                (
                    new_id,
                    name,
                    composition_json,
                    generation_provider,
                    generation_model,
                    prompt_json,
                    now,
                    now,
                ),
            )
            history = None
            try:
                history = ensure_project_history(
                    conn,
                    new_id,
                    operation_type="project-create",
                    created_at=now,
                )
            except ProjectCompositionError:
                logger.warning(
                    "Project created without history; composition is not yet canonical",
                    extra={
                        "project_id": new_id,
                        "code": "history_bootstrap_deferred",
                    },
                )
            if history is not None:
                logger.debug(
                    "Project create history ready",
                    extra={
                        "project_id": new_id,
                        "branch_id": history.active_branch_id,
                        "revision_id": history.current_revision_id,
                    },
                )
        record = get_project(new_id, db_path=path)
        summary = record.composition_summary()
        logger.info(
            "Project created",
            extra={
                "project_id": record.id,
                "name_length": len(record.name),
                "active_branch_id": record.active_branch_id,
                "current_revision_id": record.current_revision_id,
                **summary,
            },
        )
        return record
    except Exception as exc:
        logger.error(
            "Failed to create project",
            extra={
                "project_id": new_id,
                "error_type": type(exc).__name__,
                "error_detail": str(exc)[:300],
            },
        )
        raise


def get_project(project_id: str, *, db_path: Path | str | None = None) -> ProjectRecord:
    """Fetch a project by id or raise ProjectNotFoundError."""
    path = Path(db_path) if db_path is not None else get_project_db_path()
    logger.debug(
        "Fetching project",
        extra={"project_id": project_id, "project_db_path": str(path)},
    )
    try:
        with get_connection(path) as conn:
            row = conn.execute(
                f"""
                SELECT {_PROJECT_COLUMNS}
                FROM projects
                WHERE id = ?
                """,
                (project_id,),
            ).fetchone()
            if row is None:
                logger.warning("Project not found", extra={"project_id": project_id})
                raise ProjectNotFoundError(f"Project not found: {project_id}")
            if row["active_branch_id"] is None or row["current_revision_id"] is None:
                try:
                    ensure_project_history(conn, project_id, operation_type="migration")
                except ProjectCompositionError:
                    logger.warning(
                        "Project open deferred history bootstrap; composition invalid",
                        extra={
                            "project_id": project_id,
                            "code": "history_bootstrap_deferred",
                        },
                    )
                else:
                    row = conn.execute(
                        f"""
                        SELECT {_PROJECT_COLUMNS}
                        FROM projects
                        WHERE id = ?
                        """,
                        (project_id,),
                    ).fetchone()
                    if row is None:
                        raise ProjectNotFoundError(f"Project not found: {project_id}")
        record = _row_to_record(row)
        logger.debug(
            "Project fetched",
            extra={
                "project_id": record.id,
                "active_branch_id": record.active_branch_id,
                "current_revision_id": record.current_revision_id,
                **record.composition_summary(),
            },
        )
        return record
    except ProjectNotFoundError:
        raise
    except Exception as exc:
        logger.error(
            "Failed to fetch project",
            extra={
                "project_id": project_id,
                "error_type": type(exc).__name__,
                "error_detail": str(exc)[:300],
            },
        )
        raise


def update_project(
    project_id: str,
    *,
    name: str | None = None,
    composition: Any | None = None,
    clear_composition: bool = False,
    generation_provider: str | None = None,
    generation_model: str | None = None,
    generation_prompt: Any | None = None,
    clear_generation_meta: bool = False,
    branch_id: str | None = None,
    expected_active_branch_id: str | None = None,
    expected_working_version: int | None = None,
    expected_source_fingerprint: str | None = None,
    db_path: Path | str | None = None,
) -> ProjectRecord:
    """Atomically rename and/or autosave the active branch draft.

    Rename updates only ``name``. Composition changes update the active branch
    draft + materialized ``projects.composition_json`` without creating an
    immutable revision. Optional CAS preconditions protect concurrent writers.
    """
    path = Path(db_path) if db_path is not None else get_project_db_path()
    composition_touched = clear_composition or composition is not None
    generation_touched = (
        clear_generation_meta
        or generation_provider is not None
        or generation_model is not None
        or generation_prompt is not None
    )
    rename_only = name is not None and not composition_touched and not generation_touched

    logger.info(
        "Updating project",
        extra={
            "project_id": project_id,
            "rename_only": rename_only,
            "composition_touched": composition_touched,
            "generation_touched": generation_touched,
            "name_provided": name is not None,
        },
    )
    try:
        with get_connection(path) as conn:
            row = conn.execute(
                f"""
                SELECT {_PROJECT_COLUMNS}, generation_provider, generation_model,
                       generation_prompt_json
                FROM projects
                WHERE id = ?
                """,
                (project_id,),
            ).fetchone()
            if row is None:
                raise ProjectNotFoundError(f"Project not found: {project_id}")

            if row["active_branch_id"] is None or row["current_revision_id"] is None:
                try:
                    ensure_project_history(conn, project_id, operation_type="migration")
                except ProjectCompositionError:
                    if composition_touched:
                        raise
                    logger.warning(
                        "Project update without history; composition invalid",
                        extra={
                            "project_id": project_id,
                            "code": "history_bootstrap_deferred",
                        },
                    )
                row = conn.execute(
                    f"""
                    SELECT {_PROJECT_COLUMNS}, generation_provider, generation_model,
                           generation_prompt_json
                    FROM projects
                    WHERE id = ?
                    """,
                    (project_id,),
                ).fetchone()
                if row is None:
                    raise ProjectNotFoundError(f"Project not found: {project_id}")

            if name is not None:
                rename_project_fields(conn, project_id, name=name)

            if composition_touched:
                if row["active_branch_id"] is None:
                    raise ProjectCompositionError(
                        "Cannot autosave composition before history bootstrap succeeds"
                    )
                active_branch = row["active_branch_id"]
                target_branch = branch_id or active_branch
                expected_active = expected_active_branch_id or active_branch
                if expected_working_version is None:
                    version_row = conn.execute(
                        "SELECT working_version FROM project_branches WHERE id = ?",
                        (target_branch,),
                    ).fetchone()
                    if version_row is None:
                        raise ProjectNotFoundError(
                            f"Active branch not found for project: {project_id}"
                        )
                    expected_version = int(version_row["working_version"])
                else:
                    expected_version = expected_working_version
                save_branch_draft(
                    conn,
                    project_id,
                    branch_id=target_branch,
                    expected_active_branch_id=expected_active,
                    expected_working_version=expected_version,
                    composition=None if clear_composition else composition,
                    expected_source_fingerprint=expected_source_fingerprint,
                    clear_composition=clear_composition,
                )

            if generation_touched:
                if clear_generation_meta:
                    next_provider = None
                    next_model = None
                    next_prompt = None
                else:
                    next_provider = (
                        generation_provider
                        if generation_provider is not None
                        else row["generation_provider"]
                    )
                    next_model = (
                        generation_model
                        if generation_model is not None
                        else row["generation_model"]
                    )
                    if generation_prompt is not None:
                        next_prompt = _serialize_prompt_json(generation_prompt)
                    else:
                        next_prompt = row["generation_prompt_json"]
                conn.execute(
                    """
                    UPDATE projects
                    SET generation_provider = ?,
                        generation_model = ?,
                        generation_prompt_json = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        next_provider,
                        next_model,
                        next_prompt,
                        _utc_now_iso(),
                        project_id,
                    ),
                )
            elif name is not None and not composition_touched:
                # rename_project_fields already bumped updated_at
                pass

        record = get_project(project_id, db_path=path)
        logger.info(
            "Project updated",
            extra={
                "project_id": record.id,
                "name_length": len(record.name),
                "active_branch_id": record.active_branch_id,
                "current_revision_id": record.current_revision_id,
                **record.composition_summary(),
            },
        )
        return record
    except ProjectNotFoundError:
        raise
    except Exception as exc:
        logger.error(
            "Failed to update project",
            extra={
                "project_id": project_id,
                "error_type": type(exc).__name__,
                "error_detail": str(exc)[:300],
            },
        )
        raise


def duplicate_project(
    project_id: str,
    *,
    name_suffix: str = " (copy)",
    db_path: Path | str | None = None,
) -> ProjectRecord:
    """Clone a project with a new id, renamed title, and fresh timestamps."""
    path = Path(db_path) if db_path is not None else get_project_db_path()
    source = get_project(project_id, db_path=path)
    new_name = f"{source.name}{name_suffix}"
    logger.info(
        "Duplicating project",
        extra={
            "source_project_id": project_id,
            "source_name_length": len(source.name),
            "new_name_length": len(new_name),
        },
    )
    prompt_payload = None
    if source.generation_prompt_json:
        try:
            prompt_payload = json.loads(source.generation_prompt_json)
        except json.JSONDecodeError:
            prompt_payload = source.generation_prompt_json

    record = create_project(
        new_name,
        composition=source.composition_json,
        generation_provider=source.generation_provider,
        generation_model=source.generation_model,
        generation_prompt=prompt_payload,
        db_path=path,
    )
    logger.info(
        "Project duplicated",
        extra={
            "source_project_id": project_id,
            "project_id": record.id,
            **record.composition_summary(),
        },
    )
    return record


def delete_project(project_id: str, *, db_path: Path | str | None = None) -> None:
    """Hard-delete a project or raise ProjectNotFoundError."""
    path = Path(db_path) if db_path is not None else get_project_db_path()
    # Ensure missing ids raise before attempting delete.
    get_project(project_id, db_path=path)
    logger.info("Deleting project", extra={"project_id": project_id})
    try:
        with get_connection(path) as conn:
            conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        logger.info("Project deleted", extra={"project_id": project_id})
    except Exception as exc:
        logger.error(
            "Failed to delete project",
            extra={
                "project_id": project_id,
                "error_type": type(exc).__name__,
                "error_detail": str(exc)[:300],
            },
        )
        raise
