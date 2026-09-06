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


def _row_to_record(row: sqlite3.Row) -> ProjectRecord:
    return ProjectRecord(
        id=row["id"],
        name=row["name"],
        composition_json=row["composition_json"],
        generation_provider=row["generation_provider"],
        generation_model=row["generation_model"],
        generation_prompt_json=row["generation_prompt_json"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
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
                """
                SELECT id, name, composition_json, generation_provider, generation_model,
                       generation_prompt_json, created_at, updated_at
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
                    generation_prompt_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
        record = get_project(new_id, db_path=path)
        summary = record.composition_summary()
        logger.info(
            "Project created",
            extra={
                "project_id": record.id,
                "name_length": len(record.name),
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
                """
                SELECT id, name, composition_json, generation_provider, generation_model,
                       generation_prompt_json, created_at, updated_at
                FROM projects
                WHERE id = ?
                """,
                (project_id,),
            ).fetchone()
        if row is None:
            logger.warning("Project not found", extra={"project_id": project_id})
            raise ProjectNotFoundError(f"Project not found: {project_id}")
        record = _row_to_record(row)
        logger.debug(
            "Project fetched",
            extra={"project_id": record.id, **record.composition_summary()},
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
    db_path: Path | str | None = None,
) -> ProjectRecord:
    """Update rename and/or composition/generation fields; always bumps updated_at."""
    path = Path(db_path) if db_path is not None else get_project_db_path()
    existing = get_project(project_id, db_path=path)

    next_name = name if name is not None else existing.name
    if clear_composition:
        next_composition = None
    elif composition is not None:
        next_composition = _composition_payload_to_text(composition)
    else:
        next_composition = existing.composition_json

    if clear_generation_meta:
        next_provider = None
        next_model = None
        next_prompt = None
    else:
        next_provider = (
            generation_provider
            if generation_provider is not None
            else existing.generation_provider
        )
        next_model = (
            generation_model if generation_model is not None else existing.generation_model
        )
        if generation_prompt is not None:
            next_prompt = _serialize_prompt_json(generation_prompt)
        else:
            next_prompt = existing.generation_prompt_json

    now = _utc_now_iso()
    logger.info(
        "Updating project",
        extra={
            "project_id": project_id,
            "name_length": len(next_name),
            "has_composition": next_composition is not None,
            "generation_provider": next_provider,
            "generation_model": next_model,
            "renamed": name is not None and name != existing.name,
        },
    )
    try:
        with get_connection(path) as conn:
            conn.execute(
                """
                UPDATE projects
                SET name = ?,
                    composition_json = ?,
                    generation_provider = ?,
                    generation_model = ?,
                    generation_prompt_json = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    next_name,
                    next_composition,
                    next_provider,
                    next_model,
                    next_prompt,
                    now,
                    project_id,
                ),
            )
        record = get_project(project_id, db_path=path)
        logger.info(
            "Project updated",
            extra={
                "project_id": record.id,
                "name_length": len(record.name),
                **record.composition_summary(),
            },
        )
        return record
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
