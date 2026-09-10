"""Domain orchestration for project revision and branch commands."""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.db.connection import get_connection, get_project_db_path
from app.services.project_composition import (
    composition_to_storage_json,
    normalize_project_composition,
)
from app.project_history_schemas import (
    DEFAULT_REVISION_PAGE_LIMIT,
    MAX_REVISION_PAGE_LIMIT,
    AiProvenance,
    ApplyAsBranchRequest,
    BranchCheckoutRequest,
    BranchCreateRequest,
    BranchListItem,
    BranchListResponse,
    BranchRenameRequest,
    DeclaredScope,
    DurableCommitRequest,
    DurableCommandResponse,
    RestoreRevisionRequest,
    RevisionDetailResponse,
    RevisionListItem,
    RevisionListResponse,
    RevisionNameRequest,
    RevisionOperationType,
)
from app.services.composition_change_summary import (
    CompositionScopeError,
    enforce_declared_scope,
    summarize_composition_changes,
)
from app.services.composition_snapshot_encoding import (
    snapshot_fingerprint_log_prefix,
)
from app.services.persistence_secret_guard import (
    PersistenceSecretError,
    assert_no_secret_values,
    assert_payload_has_no_secret_values,
)
from app.services.project_history_store import (
    ORIGINAL_BRANCH_NAME,
    ProjectHistoryError,
    ProjectRevisionConflictError,
    apply_as_new_branch,
    checkout_branch,
    commit_durable_revision,
    ensure_project_history,
    get_snapshot_composition,
    normalize_branch_name,
    restore_revision,
    save_branch_draft,
)
from app.services.project_store import ProjectNotFoundError, get_project

logger = logging.getLogger(__name__)


class ProjectHistoryNotFoundError(LookupError):
    """Revision or branch not found for the given project."""


class ProjectHistoryValidationError(ValueError):
    """Domain validation failure for history commands."""


def _parse_json_list(raw: str | None) -> list[Any]:
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return payload if isinstance(payload, list) else []


def _parse_json_object(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _revision_row_to_item(row: Any) -> RevisionListItem:
    ranges_raw = _parse_json_list(row["affected_ranges_json"])
    track_ids_raw = _parse_json_list(row["affected_track_ids_json"])
    summary = _parse_json_object(row["summary_json"])
    # Strip any accidental large payloads from summary.
    safe_summary = {
        key: value
        for key, value in summary.items()
        if key
        in {
            "warning_codes",
            "candidate_id",
            "candidate_fingerprint",
            "restored_from_revision_id",
            "identical",
            "source_event_count",
            "target_event_count",
        }
    }
    return RevisionListItem(
        id=row["id"],
        project_id=row["project_id"],
        parent_revision_id=row["parent_revision_id"],
        sequence=int(row["sequence"]),
        name=row["name"],
        operation_type=RevisionOperationType(row["operation_type"]),
        ai_provider=row["ai_provider"],
        ai_model=row["ai_model"],
        has_user_instruction=bool(row["user_instruction"]),
        affected_ranges=ranges_raw,
        affected_track_ids=[str(item) for item in track_ids_raw],
        snapshot_fingerprint=row["snapshot_fingerprint"],
        created_at=row["created_at"],
        summary=safe_summary,
    )


def _load_active_branch_name(conn: Any, project_id: str, branch_id: str) -> str:
    row = conn.execute(
        "SELECT name FROM project_branches WHERE project_id = ? AND id = ?",
        (project_id, branch_id),
    ).fetchone()
    return row["name"] if row is not None else ORIGINAL_BRANCH_NAME


def project_history_detail_fields(
    project_id: str,
    *,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """Bounded history fields for project open/create/patch/duplicate responses."""
    path = Path(db_path) if db_path is not None else get_project_db_path()
    record = get_project(project_id, db_path=path)
    if record.active_branch_id is None or record.current_revision_id is None:
        return {
            "active_branch_id": None,
            "active_branch_name": None,
            "current_revision_id": None,
            "current_revision_sequence": None,
            "working_version": None,
            "working_fingerprint": None,
        }
    with get_connection(path) as conn:
        branch = conn.execute(
            """
            SELECT id, name, working_version, working_fingerprint
            FROM project_branches
            WHERE project_id = ? AND id = ?
            """,
            (project_id, record.active_branch_id),
        ).fetchone()
        revision = conn.execute(
            """
            SELECT id, sequence
            FROM project_revisions
            WHERE project_id = ? AND id = ?
            """,
            (project_id, record.current_revision_id),
        ).fetchone()
    return {
        "active_branch_id": record.active_branch_id,
        "active_branch_name": branch["name"] if branch is not None else None,
        "current_revision_id": record.current_revision_id,
        "current_revision_sequence": int(revision["sequence"]) if revision is not None else None,
        "working_version": int(branch["working_version"]) if branch is not None else None,
        "working_fingerprint": branch["working_fingerprint"] if branch is not None else None,
    }


def list_revisions(
    project_id: str,
    *,
    branch_id: str | None = None,
    limit: int = DEFAULT_REVISION_PAGE_LIMIT,
    before_sequence: int | None = None,
    db_path: Path | str | None = None,
) -> RevisionListResponse:
    path = Path(db_path) if db_path is not None else get_project_db_path()
    page_limit = max(1, min(limit, MAX_REVISION_PAGE_LIMIT))
    # Ensure project exists / history bootstrapped.
    get_project(project_id, db_path=path)

    query = """
        SELECT r.*
        FROM project_revisions r
    """
    params: list[Any] = []
    clauses = ["r.project_id = ?"]
    params.append(project_id)
    if branch_id is not None:
        query += """
            JOIN project_branches b
              ON b.project_id = r.project_id
             AND b.id = ?
        """
        params.append(branch_id)
        # Ancestors of the branch head: sequences <= head sequence on this project.
        query += """
            JOIN project_revisions head
              ON head.project_id = b.project_id
             AND head.id = b.head_revision_id
        """
        clauses.append("r.sequence <= head.sequence")
    if before_sequence is not None:
        clauses.append("r.sequence < ?")
        params.append(before_sequence)
    query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY r.sequence DESC LIMIT ?"
    params.append(page_limit + 1)

    with get_connection(path) as conn:
        if branch_id is not None:
            branch = conn.execute(
                "SELECT id FROM project_branches WHERE project_id = ? AND id = ?",
                (project_id, branch_id),
            ).fetchone()
            if branch is None:
                raise ProjectHistoryNotFoundError(
                    f"Branch not found: {branch_id}"
                )
        rows = conn.execute(query, params).fetchall()

    has_more = len(rows) > page_limit
    page_rows = rows[:page_limit]
    items = [_revision_row_to_item(row) for row in page_rows]
    next_before = items[-1].sequence if has_more and items else None
    logger.debug(
        "Listed project revisions",
        extra={
            "project_id": project_id,
            "branch_id": branch_id,
            "count": len(items),
            "has_more": has_more,
        },
    )
    return RevisionListResponse(revisions=items, next_before_sequence=next_before)


def get_revision_detail(
    project_id: str,
    revision_id: str,
    *,
    db_path: Path | str | None = None,
) -> RevisionDetailResponse:
    path = Path(db_path) if db_path is not None else get_project_db_path()
    get_project(project_id, db_path=path)
    with get_connection(path) as conn:
        row = conn.execute(
            """
            SELECT *
            FROM project_revisions
            WHERE project_id = ? AND id = ?
            """,
            (project_id, revision_id),
        ).fetchone()
        if row is None:
            raise ProjectHistoryNotFoundError(f"Revision not found: {revision_id}")
        composition = get_snapshot_composition(conn, row["snapshot_fingerprint"])
    item = _revision_row_to_item(row)
    logger.debug(
        "Loaded revision detail",
        extra={
            "project_id": project_id,
            "revision_id": revision_id,
            "has_composition": composition is not None,
            "fingerprint_prefix": snapshot_fingerprint_log_prefix(item.snapshot_fingerprint),
        },
    )
    return RevisionDetailResponse(revision=item, composition=composition)


def name_revision(
    project_id: str,
    revision_id: str,
    request: RevisionNameRequest,
    *,
    db_path: Path | str | None = None,
) -> RevisionListItem:
    path = Path(db_path) if db_path is not None else get_project_db_path()
    get_project(project_id, db_path=path)
    with get_connection(path) as conn:
        cursor = conn.execute(
            """
            UPDATE project_revisions
            SET name = ?
            WHERE project_id = ? AND id = ?
            """,
            (request.name, project_id, revision_id),
        )
        if cursor.rowcount != 1:
            raise ProjectHistoryNotFoundError(f"Revision not found: {revision_id}")
        row = conn.execute(
            "SELECT * FROM project_revisions WHERE project_id = ? AND id = ?",
            (project_id, revision_id),
        ).fetchone()
    logger.info(
        "Named project revision",
        extra={
            "project_id": project_id,
            "revision_id": revision_id,
            "name_length": len(request.name or ""),
        },
    )
    return _revision_row_to_item(row)


def _ai_fields(ai: AiProvenance | None) -> dict[str, Any]:
    if ai is None:
        return {
            "ai_provider": None,
            "ai_model": None,
            "user_instruction": None,
            "summary_json": "{}",
        }
    summary = {
        "warning_codes": list(ai.warning_codes),
        "candidate_id": ai.candidate_id,
        "candidate_fingerprint": ai.candidate_fingerprint,
    }
    summary = {key: value for key, value in summary.items() if value not in (None, [])}
    return {
        "ai_provider": ai.provider,
        "ai_model": ai.model,
        "user_instruction": ai.user_instruction,
        "summary_json": json.dumps(summary, ensure_ascii=False, separators=(",", ":")),
    }


def _resolve_source_composition(
    conn: Any,
    *,
    project_id: str,
    branch_id: str,
    source_fingerprint: str,
) -> Any:
    """Load source composition from snapshot table or active branch draft."""
    row = conn.execute(
        """
        SELECT fingerprint
        FROM composition_snapshots
        WHERE fingerprint = ?
        """,
        (source_fingerprint,),
    ).fetchone()
    if row is not None:
        return get_snapshot_composition(conn, source_fingerprint)

    branch = conn.execute(
        """
        SELECT working_composition_json, working_fingerprint
        FROM project_branches
        WHERE project_id = ? AND id = ?
        """,
        (project_id, branch_id),
    ).fetchone()
    if branch is None:
        raise ProjectHistoryNotFoundError(f"Branch not found: {branch_id}")
    if branch["working_fingerprint"] != source_fingerprint:
        raise ProjectHistoryValidationError(
            "Source fingerprint is not a stored snapshot or current branch draft"
        )
    if branch["working_composition_json"] is None:
        return None
    return normalize_project_composition(
        branch["working_composition_json"],
        project_id=project_id,
    ).composition


def _scope_payloads(
    *,
    project_id: str,
    branch_id: str,
    source_fingerprint: str,
    target_composition: Any,
    declared_scope: DeclaredScope | None,
    conn: Any,
) -> tuple[str, str, dict[str, Any]]:
    source = _resolve_source_composition(
        conn,
        project_id=project_id,
        branch_id=branch_id,
        source_fingerprint=source_fingerprint,
    )
    summary = summarize_composition_changes(source, target_composition)
    enforce_declared_scope(summary, declared_scope)
    ranges_json = json.dumps(
        [item.model_dump(mode="json") for item in summary.affected_ranges],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    tracks_json = json.dumps(
        list(summary.affected_track_ids),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    meta = {
        "identical": summary.identical,
        "source_event_count": summary.source_event_count,
        "target_event_count": summary.target_event_count,
    }
    logger.debug(
        "Derived commit scope",
        extra={
            "project_id": project_id,
            "affected_range_count": len(summary.affected_ranges),
            "affected_track_count": len(summary.affected_track_ids),
            "identical": summary.identical,
        },
    )
    return ranges_json, tracks_json, meta


def commit_revision(
    project_id: str,
    request: DurableCommitRequest,
    *,
    db_path: Path | str | None = None,
) -> DurableCommandResponse:
    path = Path(db_path) if db_path is not None else get_project_db_path()
    get_project(project_id, db_path=path)
    if request.ai is not None and request.ai.user_instruction:
        assert_no_secret_values(request.ai.user_instruction, field_name="user_instruction")

    with get_connection(path) as conn:
        ensure_project_history(conn, project_id)
        source_state = conn.execute(
            """
            SELECT working_fingerprint, head_revision_id
            FROM project_branches
            WHERE project_id = ? AND id = ?
            """,
            (project_id, request.branch_id),
        ).fetchone()
        if source_state is None:
            raise ProjectHistoryNotFoundError(f"Branch not found: {request.branch_id}")

        target_composition = None if request.clear_composition else request.composition
        ranges_json, tracks_json, scope_meta = _scope_payloads(
            project_id=project_id,
            branch_id=request.branch_id,
            source_fingerprint=request.expected_source_fingerprint,
            target_composition=target_composition,
            declared_scope=request.declared_scope,
            conn=conn,
        )
        ai_fields = _ai_fields(request.ai)
        summary = _parse_json_object(ai_fields["summary_json"])
        summary.update(scope_meta)
        result = commit_durable_revision(
            conn,
            project_id,
            branch_id=request.branch_id,
            expected_active_branch_id=request.expected_active_branch_id,
            expected_working_version=request.expected_working_version,
            expected_head_revision_id=request.expected_head_revision_id,
            expected_source_fingerprint=request.expected_source_fingerprint,
            composition=target_composition,
            clear_composition=request.clear_composition,
            operation_type=request.operation_type.value,
            checkpoint_dirty_draft=request.checkpoint_dirty_draft,
            name=request.name,
            ai_provider=ai_fields["ai_provider"],
            ai_model=ai_fields["ai_model"],
            user_instruction=ai_fields["user_instruction"],
            affected_ranges_json=ranges_json,
            affected_track_ids_json=tracks_json,
            summary_json=json.dumps(summary, ensure_ascii=False, separators=(",", ":")),
        )
        branch_name = _load_active_branch_name(conn, project_id, result.branch_id)
        composition = (
            None
            if result.composition_json is None
            else get_snapshot_composition(conn, result.working_fingerprint)
        )

    return DurableCommandResponse(
        project_id=project_id,
        active_branch_id=result.branch_id,
        active_branch_name=branch_name,
        current_revision_id=result.head_revision_id,
        current_revision_sequence=result.sequence,
        working_version=result.working_version,
        working_fingerprint=result.working_fingerprint,
        composition=composition,
        revision_created=result.revision_created,
        created_revision_ids=list(result.created_revision_ids),
        operation_type=RevisionOperationType(result.operation_type)
        if result.operation_type in RevisionOperationType._value2member_map_
        else request.operation_type,
    )


def restore_revision_command(
    project_id: str,
    revision_id: str,
    request: RestoreRevisionRequest,
    *,
    db_path: Path | str | None = None,
) -> DurableCommandResponse:
    path = Path(db_path) if db_path is not None else get_project_db_path()
    get_project(project_id, db_path=path)
    with get_connection(path) as conn:
        ensure_project_history(conn, project_id)
        owned = conn.execute(
            "SELECT id FROM project_revisions WHERE project_id = ? AND id = ?",
            (project_id, revision_id),
        ).fetchone()
        if owned is None:
            raise ProjectHistoryNotFoundError(f"Revision not found: {revision_id}")
        result = restore_revision(
            conn,
            project_id,
            revision_id=revision_id,
            branch_id=request.branch_id,
            expected_active_branch_id=request.expected_active_branch_id,
            expected_working_version=request.expected_working_version,
            expected_head_revision_id=request.expected_head_revision_id,
        )
        branch_name = _load_active_branch_name(conn, project_id, result.branch_id)
        composition = (
            None
            if result.composition_json is None
            else get_snapshot_composition(conn, result.working_fingerprint)
        )
    return DurableCommandResponse(
        project_id=project_id,
        active_branch_id=result.branch_id,
        active_branch_name=branch_name,
        current_revision_id=result.head_revision_id,
        current_revision_sequence=result.sequence,
        working_version=result.working_version,
        working_fingerprint=result.working_fingerprint,
        composition=composition,
        revision_created=result.revision_created,
        created_revision_ids=list(result.created_revision_ids),
        operation_type=RevisionOperationType.REVISION_RESTORE,
    )


def list_branches(
    project_id: str,
    *,
    db_path: Path | str | None = None,
) -> BranchListResponse:
    path = Path(db_path) if db_path is not None else get_project_db_path()
    record = get_project(project_id, db_path=path)
    with get_connection(path) as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM project_branches
            WHERE project_id = ?
            ORDER BY created_at ASC
            """,
            (project_id,),
        ).fetchall()
    items = [
        BranchListItem(
            id=row["id"],
            project_id=row["project_id"],
            name=row["name"],
            head_revision_id=row["head_revision_id"],
            created_from_revision_id=row["created_from_revision_id"],
            working_version=int(row["working_version"]),
            working_fingerprint=row["working_fingerprint"],
            is_active=row["id"] == record.active_branch_id,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
        for row in rows
    ]
    logger.debug(
        "Listed project branches",
        extra={"project_id": project_id, "count": len(items)},
    )
    return BranchListResponse(branches=items)


def create_branch(
    project_id: str,
    request: BranchCreateRequest,
    *,
    db_path: Path | str | None = None,
) -> BranchListItem:
    """Create an empty-named pointer branch from an existing revision (no new commit)."""
    path = Path(db_path) if db_path is not None else get_project_db_path()
    record = get_project(project_id, db_path=path)
    with get_connection(path) as conn:
        ensure_project_history(conn, project_id)
        source = conn.execute(
            """
            SELECT id, snapshot_fingerprint
            FROM project_revisions
            WHERE project_id = ? AND id = ?
            """,
            (project_id, request.from_revision_id),
        ).fetchone()
        if source is None:
            raise ProjectHistoryNotFoundError(
                f"Revision not found: {request.from_revision_id}"
            )
        composition = get_snapshot_composition(conn, source["snapshot_fingerprint"])
        storage_json = None
        if composition is not None:
            storage_json = composition_to_storage_json(composition)

        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        branch_id = str(uuid.uuid4())
        try:
            conn.execute(
                """
                INSERT INTO project_branches (
                    id, project_id, name, normalized_name, head_revision_id,
                    created_from_revision_id, working_composition_json, working_fingerprint,
                    working_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    branch_id,
                    project_id,
                    request.name,
                    normalize_branch_name(request.name),
                    request.from_revision_id,
                    request.from_revision_id,
                    storage_json,
                    source["snapshot_fingerprint"],
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ProjectHistoryValidationError(
                f"Branch name already exists for project {project_id}"
            ) from exc

        if request.checkout:
            if record.active_branch_id is None:
                raise ProjectHistoryValidationError("Project has no active branch")
            current = conn.execute(
                """
                SELECT working_version, head_revision_id, working_fingerprint
                FROM project_branches WHERE id = ?
                """,
                (record.active_branch_id,),
            ).fetchone()
            checkout_branch(
                conn,
                project_id,
                branch_id=branch_id,
                expected_active_branch_id=record.active_branch_id,
                expected_working_version=int(current["working_version"]),
                expected_head_revision_id=current["head_revision_id"],
            )

        row = conn.execute(
            "SELECT * FROM project_branches WHERE id = ?",
            (branch_id,),
        ).fetchone()
        active = conn.execute(
            "SELECT active_branch_id FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()

    logger.info(
        "Created project branch",
        extra={
            "project_id": project_id,
            "branch_id": branch_id,
            "name_length": len(request.name),
            "checkout": request.checkout,
        },
    )
    return BranchListItem(
        id=row["id"],
        project_id=row["project_id"],
        name=row["name"],
        head_revision_id=row["head_revision_id"],
        created_from_revision_id=row["created_from_revision_id"],
        working_version=int(row["working_version"]),
        working_fingerprint=row["working_fingerprint"],
        is_active=row["id"] == active["active_branch_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def rename_branch(
    project_id: str,
    branch_id: str,
    request: BranchRenameRequest,
    *,
    db_path: Path | str | None = None,
) -> BranchListItem:
    path = Path(db_path) if db_path is not None else get_project_db_path()
    record = get_project(project_id, db_path=path)
    with get_connection(path) as conn:
        try:
            cursor = conn.execute(
                """
                UPDATE project_branches
                SET name = ?, normalized_name = ?, updated_at = datetime('now')
                WHERE project_id = ? AND id = ?
                """,
                (
                    request.name,
                    normalize_branch_name(request.name),
                    project_id,
                    branch_id,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ProjectHistoryValidationError(
                f"Branch name already exists for project {project_id}"
            ) from exc
        if cursor.rowcount != 1:
            raise ProjectHistoryNotFoundError(f"Branch not found: {branch_id}")
        row = conn.execute(
            "SELECT * FROM project_branches WHERE id = ?",
            (branch_id,),
        ).fetchone()
    logger.info(
        "Renamed project branch",
        extra={
            "project_id": project_id,
            "branch_id": branch_id,
            "name_length": len(request.name),
        },
    )
    return BranchListItem(
        id=row["id"],
        project_id=row["project_id"],
        name=row["name"],
        head_revision_id=row["head_revision_id"],
        created_from_revision_id=row["created_from_revision_id"],
        working_version=int(row["working_version"]),
        working_fingerprint=row["working_fingerprint"],
        is_active=row["id"] == record.active_branch_id,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def checkout_branch_command(
    project_id: str,
    branch_id: str,
    request: BranchCheckoutRequest,
    *,
    db_path: Path | str | None = None,
) -> DurableCommandResponse:
    path = Path(db_path) if db_path is not None else get_project_db_path()
    get_project(project_id, db_path=path)
    with get_connection(path) as conn:
        ensure_project_history(conn, project_id)
        result = checkout_branch(
            conn,
            project_id,
            branch_id=branch_id,
            expected_active_branch_id=request.expected_active_branch_id,
            expected_working_version=request.expected_working_version,
            expected_head_revision_id=request.expected_head_revision_id,
        )
        sequence = conn.execute(
            "SELECT sequence FROM project_revisions WHERE id = ?",
            (result.head_revision_id,),
        ).fetchone()["sequence"]
        branch_name = _load_active_branch_name(conn, project_id, result.active_branch_id)
        composition = (
            None
            if result.composition_json is None
            else get_snapshot_composition(conn, result.working_fingerprint)
        )
    return DurableCommandResponse(
        project_id=project_id,
        active_branch_id=result.active_branch_id,
        active_branch_name=branch_name,
        current_revision_id=result.head_revision_id,
        current_revision_sequence=int(sequence),
        working_version=result.working_version,
        working_fingerprint=result.working_fingerprint,
        composition=composition,
        revision_created=False,
        created_revision_ids=[],
        operation_type=None,
    )


def apply_as_branch_command(
    project_id: str,
    request: ApplyAsBranchRequest,
    *,
    db_path: Path | str | None = None,
) -> DurableCommandResponse:
    path = Path(db_path) if db_path is not None else get_project_db_path()
    get_project(project_id, db_path=path)
    if request.ai is not None and request.ai.user_instruction:
        assert_no_secret_values(request.ai.user_instruction, field_name="user_instruction")

    with get_connection(path) as conn:
        ensure_project_history(conn, project_id)
        target_composition = None if request.clear_composition else request.composition
        ranges_json, tracks_json, scope_meta = _scope_payloads(
            project_id=project_id,
            branch_id=request.source_branch_id,
            source_fingerprint=request.expected_source_fingerprint,
            target_composition=target_composition,
            declared_scope=request.declared_scope,
            conn=conn,
        )
        ai_fields = _ai_fields(request.ai)
        summary = _parse_json_object(ai_fields["summary_json"])
        summary.update(scope_meta)
        result = apply_as_new_branch(
            conn,
            project_id,
            source_branch_id=request.source_branch_id,
            expected_active_branch_id=request.expected_active_branch_id,
            expected_working_version=request.expected_working_version,
            expected_head_revision_id=request.expected_head_revision_id,
            expected_source_fingerprint=request.expected_source_fingerprint,
            branch_name=request.name,
            composition=target_composition,
            clear_composition=request.clear_composition,
            operation_type=request.operation_type.value,
            ai_provider=ai_fields["ai_provider"],
            ai_model=ai_fields["ai_model"],
            user_instruction=ai_fields["user_instruction"],
            affected_ranges_json=ranges_json,
            affected_track_ids_json=tracks_json,
            summary_json=json.dumps(summary, ensure_ascii=False, separators=(",", ":")),
        )
        composition = (
            None
            if result.composition_json is None
            else get_snapshot_composition(conn, result.working_fingerprint)
        )
    return DurableCommandResponse(
        project_id=project_id,
        active_branch_id=result.new_branch_id,
        active_branch_name=result.new_branch_name,
        current_revision_id=result.head_revision_id,
        current_revision_sequence=result.sequence,
        working_version=result.working_version,
        working_fingerprint=result.working_fingerprint,
        composition=composition,
        revision_created=True,
        created_revision_ids=[result.head_revision_id],
        operation_type=request.operation_type,
    )


# Re-export conflict/error types for routers.
__all__ = [
    "CompositionScopeError",
    "PersistenceSecretError",
    "ProjectHistoryError",
    "ProjectHistoryNotFoundError",
    "ProjectHistoryValidationError",
    "ProjectNotFoundError",
    "ProjectRevisionConflictError",
    "apply_as_branch_command",
    "checkout_branch_command",
    "commit_revision",
    "create_branch",
    "get_revision_detail",
    "list_branches",
    "list_revisions",
    "name_revision",
    "project_history_detail_fields",
    "rename_branch",
    "restore_revision_command",
    "save_branch_draft",
    "assert_payload_has_no_secret_values",
]
