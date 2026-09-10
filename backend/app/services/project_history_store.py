"""SQLite persistence for composition snapshots, revisions, and branches."""

from __future__ import annotations

import json
import logging
import sqlite3
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .composition_snapshot_encoding import (
    NULL_SNAPSHOT_FINGERPRINT,
    EncodedCompositionSnapshot,
    composition_snapshot_fingerprint,
    decode_composition_snapshot,
    encode_composition_snapshot,
    snapshot_fingerprint_log_prefix,
)
from .project_composition import (
    ProjectCompositionError,
    composition_to_storage_json,
    normalize_project_composition,
)

logger = logging.getLogger(__name__)

ORIGINAL_BRANCH_NAME = "Original"
OPERATION_PROJECT_CREATE = "project-create"
OPERATION_MIGRATION = "migration"


class ProjectHistoryError(ValueError):
    """Raised when history bootstrap or lookup fails."""


class ProjectHistoryConflictError(ProjectHistoryError):
    """Raised when concurrent history bootstrap cannot be reconciled."""


@dataclass(frozen=True)
class ProjectHistoryState:
    project_id: str
    active_branch_id: str
    current_revision_id: str
    branch_name: str
    snapshot_fingerprint: str
    working_fingerprint: str
    working_version: int
    revision_sequence: int
    operation_type: str
    bootstrapped: bool


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_branch_name(name: str) -> str:
    """NFKC + casefold uniqueness key for per-project branch names."""
    return unicodedata.normalize("NFKC", name).casefold()


def assert_active_materialization(
    conn: sqlite3.Connection,
    project_id: str,
) -> None:
    """Enforce active project row matches the active branch head/draft."""
    row = conn.execute(
        """
        SELECT
            p.composition_json AS project_composition_json,
            p.current_revision_id AS project_revision_id,
            p.active_branch_id AS project_branch_id,
            b.head_revision_id AS branch_head_revision_id,
            b.working_composition_json AS branch_working_composition_json,
            b.working_fingerprint AS branch_working_fingerprint
        FROM projects p
        JOIN project_branches b
          ON b.id = p.active_branch_id AND b.project_id = p.id
        WHERE p.id = ?
        """,
        (project_id,),
    ).fetchone()
    if row is None:
        raise ProjectHistoryError(f"Active history materialization missing for {project_id}")
    if row["project_revision_id"] != row["branch_head_revision_id"]:
        raise ProjectHistoryError(
            f"Project current_revision_id diverges from active branch head for {project_id}"
        )
    if row["project_composition_json"] != row["branch_working_composition_json"]:
        raise ProjectHistoryError(
            f"Project composition_json diverges from active branch draft for {project_id}"
        )
    if row["branch_working_composition_json"] is None:
        expected_fp = NULL_SNAPSHOT_FINGERPRINT
    else:
        try:
            composition = normalize_project_composition(
                row["branch_working_composition_json"],
                project_id=project_id,
            ).composition
            expected_fp = composition_snapshot_fingerprint(composition)
        except ProjectCompositionError as exc:
            raise ProjectHistoryError(
                f"Active branch draft failed validation for {project_id}"
            ) from exc
    if row["branch_working_fingerprint"] != expected_fp:
        raise ProjectHistoryError(
            f"Active branch working_fingerprint mismatch for {project_id}"
        )


def _load_history_state(
    conn: sqlite3.Connection,
    project_id: str,
    *,
    bootstrapped: bool,
) -> ProjectHistoryState:
    row = conn.execute(
        """
        SELECT
            p.active_branch_id,
            p.current_revision_id,
            b.name AS branch_name,
            b.working_fingerprint,
            b.working_version,
            r.snapshot_fingerprint,
            r.sequence AS revision_sequence,
            r.operation_type
        FROM projects p
        JOIN project_branches b
          ON b.id = p.active_branch_id AND b.project_id = p.id
        JOIN project_revisions r
          ON r.id = p.current_revision_id AND r.project_id = p.id
        WHERE p.id = ?
        """,
        (project_id,),
    ).fetchone()
    if row is None or row["active_branch_id"] is None:
        raise ProjectHistoryError(f"Project history is not initialized: {project_id}")
    return ProjectHistoryState(
        project_id=project_id,
        active_branch_id=row["active_branch_id"],
        current_revision_id=row["current_revision_id"],
        branch_name=row["branch_name"],
        snapshot_fingerprint=row["snapshot_fingerprint"],
        working_fingerprint=row["working_fingerprint"],
        working_version=int(row["working_version"]),
        revision_sequence=int(row["revision_sequence"]),
        operation_type=row["operation_type"],
        bootstrapped=bootstrapped,
    )


def upsert_composition_snapshot(
    conn: sqlite3.Connection,
    encoded: EncodedCompositionSnapshot,
    *,
    created_at: str | None = None,
) -> tuple[str, bool]:
    """Insert a snapshot if missing; return (fingerprint, inserted)."""
    existing = conn.execute(
        "SELECT fingerprint FROM composition_snapshots WHERE fingerprint = ?",
        (encoded.fingerprint,),
    ).fetchone()
    if existing is not None:
        logger.debug(
            "Snapshot already present; deduplicated",
            extra={
                "fingerprint_prefix": snapshot_fingerprint_log_prefix(encoded.fingerprint),
                "deduped": True,
            },
        )
        return encoded.fingerprint, False

    now = created_at or _utc_now_iso()
    conn.execute(
        """
        INSERT INTO composition_snapshots (
            fingerprint, encoding_profile, compression_profile, payload_zlib,
            uncompressed_byte_size, compressed_byte_size, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            encoded.fingerprint,
            encoded.encoding_profile,
            encoded.compression_profile,
            encoded.payload_zlib,
            encoded.uncompressed_byte_size,
            encoded.compressed_byte_size,
            now,
        ),
    )
    logger.debug(
        "Inserted composition snapshot",
        extra={
            "fingerprint_prefix": snapshot_fingerprint_log_prefix(encoded.fingerprint),
            "uncompressed_byte_size": encoded.uncompressed_byte_size,
            "compressed_byte_size": encoded.compressed_byte_size,
            "deduped": False,
        },
    )
    return encoded.fingerprint, True


def get_snapshot_composition(
    conn: sqlite3.Connection,
    fingerprint: str,
) -> Any:
    """Load and decode a snapshot body (CompositionV2 | None)."""
    row = conn.execute(
        """
        SELECT fingerprint, encoding_profile, compression_profile, payload_zlib,
               uncompressed_byte_size
        FROM composition_snapshots
        WHERE fingerprint = ?
        """,
        (fingerprint,),
    ).fetchone()
    if row is None:
        raise ProjectHistoryError(f"Snapshot not found: {fingerprint[:12]}")
    return decode_composition_snapshot(
        fingerprint=row["fingerprint"],
        encoding_profile=row["encoding_profile"],
        compression_profile=row["compression_profile"],
        payload_zlib=row["payload_zlib"],
        uncompressed_byte_size=row["uncompressed_byte_size"],
    )


def _prepare_root_composition(
    conn: sqlite3.Connection,
    project_id: str,
    composition_json: str | None,
) -> tuple[Any, str | None, str]:
    """Normalize stored composition for root snapshot; may rewrite project JSON.

    Returns (composition_or_none, storage_json, inferred_operation_hint).
    """
    if composition_json is None or composition_json == "":
        return None, None, OPERATION_PROJECT_CREATE

    normalized = normalize_project_composition(
        composition_json,
        project_id=project_id,
        persist_canonical=True,
    )
    storage_json = composition_to_storage_json(normalized.composition)
    if storage_json != composition_json:
        conn.execute(
            """
            UPDATE projects
            SET composition_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (storage_json, _utc_now_iso(), project_id),
        )
        logger.debug(
            "Rewrote project composition during history bootstrap",
            extra={
                "project_id": project_id,
                "migration_path": normalized.migration_path,
                "previous_schema_version": normalized.previous_schema_version,
            },
        )
    operation_hint = (
        OPERATION_MIGRATION
        if normalized.migration_path in {"legacy", "v1_to_v2"}
        else OPERATION_PROJECT_CREATE
    )
    return normalized.composition, storage_json, operation_hint


def ensure_project_history(
    conn: sqlite3.Connection,
    project_id: str,
    *,
    operation_type: str | None = None,
    created_at: str | None = None,
) -> ProjectHistoryState:
    """Idempotently bootstrap Original branch + root revision for a project.

    Safe to call from create/open paths. Concurrent callers either complete the
    bootstrap or observe the peer's finished pointers.
    """
    logger.debug(
        "Ensuring project history",
        extra={
            "project_id": project_id,
            "requested_operation_type": operation_type,
            "stage": "start",
        },
    )
    row = conn.execute(
        """
        SELECT id, composition_json, active_branch_id, current_revision_id
        FROM projects
        WHERE id = ?
        """,
        (project_id,),
    ).fetchone()
    if row is None:
        raise ProjectHistoryError(f"Project not found for history bootstrap: {project_id}")

    if row["active_branch_id"] is not None and row["current_revision_id"] is not None:
        state = _load_history_state(conn, project_id, bootstrapped=False)
        logger.debug(
            "Project history already present",
            extra={
                "project_id": project_id,
                "branch_id": state.active_branch_id,
                "revision_id": state.current_revision_id,
                "fingerprint_prefix": snapshot_fingerprint_log_prefix(
                    state.snapshot_fingerprint
                ),
            },
        )
        return state

    if row["active_branch_id"] is not None or row["current_revision_id"] is not None:
        logger.warning(
            "Partial project history pointers detected",
            extra={
                "project_id": project_id,
                "code": "partial_history_pointers",
                "has_active_branch": row["active_branch_id"] is not None,
                "has_current_revision": row["current_revision_id"] is not None,
            },
        )
        raise ProjectHistoryError(
            f"Project has partial history pointers and cannot be bootstrapped: {project_id}"
        )

    now = created_at or _utc_now_iso()
    try:
        composition, storage_json, inferred_operation = _prepare_root_composition(
            conn,
            project_id,
            row["composition_json"],
        )
    except ProjectCompositionError:
        logger.warning(
            "History bootstrap aborted; composition migration failed",
            extra={
                "project_id": project_id,
                "code": "history_bootstrap_composition_failed",
            },
        )
        raise

    resolved_operation = operation_type or inferred_operation
    encoded = encode_composition_snapshot(composition)
    fingerprint, inserted = upsert_composition_snapshot(conn, encoded, created_at=now)

    revision_id = str(uuid.uuid4())
    branch_id = str(uuid.uuid4())
    normalized_name = normalize_branch_name(ORIGINAL_BRANCH_NAME)

    try:
        conn.execute(
            """
            INSERT INTO project_revisions (
                id, project_id, parent_revision_id, snapshot_fingerprint, sequence,
                name, operation_type, ai_provider, ai_model, user_instruction,
                affected_ranges_json, affected_track_ids_json, summary_json, created_at
            ) VALUES (?, ?, NULL, ?, 1, NULL, ?, NULL, NULL, NULL, '[]', '[]', '{}', ?)
            """,
            (revision_id, project_id, fingerprint, resolved_operation, now),
        )
        conn.execute(
            """
            INSERT INTO project_branches (
                id, project_id, name, normalized_name, head_revision_id,
                created_from_revision_id, working_composition_json, working_fingerprint,
                working_version, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, 0, ?, ?)
            """,
            (
                branch_id,
                project_id,
                ORIGINAL_BRANCH_NAME,
                normalized_name,
                revision_id,
                storage_json,
                fingerprint,
                now,
                now,
            ),
        )
        conn.execute(
            """
            UPDATE projects
            SET active_branch_id = ?,
                current_revision_id = ?,
                composition_json = ?,
                updated_at = ?
            WHERE id = ?
              AND active_branch_id IS NULL
              AND current_revision_id IS NULL
            """,
            (branch_id, revision_id, storage_json, now, project_id),
        )
        updated = conn.execute("SELECT changes()").fetchone()[0]
        if updated != 1:
            raise ProjectHistoryConflictError(
                f"Concurrent history bootstrap won for project {project_id}"
            )
    except sqlite3.IntegrityError as exc:
        logger.warning(
            "History bootstrap conflict; reconciling",
            extra={
                "project_id": project_id,
                "code": "history_bootstrap_conflict",
                "error_type": type(exc).__name__,
            },
        )
        peer = conn.execute(
            """
            SELECT active_branch_id, current_revision_id
            FROM projects
            WHERE id = ?
            """,
            (project_id,),
        ).fetchone()
        if peer is not None and peer["active_branch_id"] and peer["current_revision_id"]:
            return _load_history_state(conn, project_id, bootstrapped=False)
        raise ProjectHistoryConflictError(
            f"Unable to bootstrap project history for {project_id}"
        ) from exc

    assert_active_materialization(conn, project_id)
    state = _load_history_state(conn, project_id, bootstrapped=True)
    logger.info(
        "Project history bootstrapped",
        extra={
            "project_id": project_id,
            "branch_id": state.active_branch_id,
            "revision_id": state.current_revision_id,
            "operation_type": resolved_operation,
            "fingerprint_prefix": snapshot_fingerprint_log_prefix(fingerprint),
            "snapshot_inserted": inserted,
            "uncompressed_byte_size": encoded.uncompressed_byte_size,
            "compressed_byte_size": encoded.compressed_byte_size,
        },
    )
    return state

# ---------------------------------------------------------------------------
# Atomic revision-aware commands (Task 2)
# ---------------------------------------------------------------------------


class ProjectRevisionConflictError(Exception):
    """Compare-and-swap conflict for branch/head/working preconditions."""

    code = "project_revision_conflict"

    def __init__(
        self,
        message: str,
        *,
        project_id: str,
        expected_active_branch_id: str | None = None,
        current_active_branch_id: str | None = None,
        expected_working_version: int | None = None,
        current_working_version: int | None = None,
        expected_head_revision_id: str | None = None,
        current_head_revision_id: str | None = None,
        expected_source_fingerprint: str | None = None,
        current_working_fingerprint: str | None = None,
        current_sequence: int | None = None,
    ) -> None:
        super().__init__(message)
        self.project_id = project_id
        self.expected_active_branch_id = expected_active_branch_id
        self.current_active_branch_id = current_active_branch_id
        self.expected_working_version = expected_working_version
        self.current_working_version = current_working_version
        self.expected_head_revision_id = expected_head_revision_id
        self.current_head_revision_id = current_head_revision_id
        self.expected_source_fingerprint = expected_source_fingerprint
        self.current_working_fingerprint = current_working_fingerprint
        self.current_sequence = current_sequence

    def bounded_detail(self) -> dict[str, Any]:
        """Sanitized conflict payload — IDs/sequences only, never compositions."""
        return {
            "code": self.code,
            "project_id": self.project_id,
            "expected_active_branch_id": self.expected_active_branch_id,
            "current_active_branch_id": self.current_active_branch_id,
            "expected_working_version": self.expected_working_version,
            "current_working_version": self.current_working_version,
            "expected_head_revision_id": self.expected_head_revision_id,
            "current_head_revision_id": self.current_head_revision_id,
            "expected_source_fingerprint_prefix": (
                snapshot_fingerprint_log_prefix(self.expected_source_fingerprint)
                if self.expected_source_fingerprint
                else None
            ),
            "current_working_fingerprint_prefix": (
                snapshot_fingerprint_log_prefix(self.current_working_fingerprint)
                if self.current_working_fingerprint
                else None
            ),
            "current_sequence": self.current_sequence,
        }


@dataclass(frozen=True)
class BranchCommandState:
    project_id: str
    active_branch_id: str
    branch_id: str
    branch_name: str
    head_revision_id: str
    working_version: int
    working_fingerprint: str
    working_composition_json: str | None
    head_sequence: int
    head_snapshot_fingerprint: str


@dataclass(frozen=True)
class DraftSaveResult:
    project_id: str
    branch_id: str
    working_version: int
    working_fingerprint: str
    composition_json: str | None
    head_revision_id: str
    revision_created: bool = False


@dataclass(frozen=True)
class DurableCommitResult:
    project_id: str
    branch_id: str
    head_revision_id: str
    working_version: int
    working_fingerprint: str
    composition_json: str | None
    revision_created: bool
    created_revision_ids: tuple[str, ...]
    sequence: int
    operation_type: str


@dataclass(frozen=True)
class BranchCheckoutResult:
    project_id: str
    active_branch_id: str
    head_revision_id: str
    working_version: int
    working_fingerprint: str
    composition_json: str | None


@dataclass(frozen=True)
class ApplyAsBranchResult:
    project_id: str
    source_branch_id: str
    source_head_revision_id: str
    new_branch_id: str
    new_branch_name: str
    head_revision_id: str
    working_version: int
    working_fingerprint: str
    composition_json: str | None
    sequence: int


def _next_revision_sequence(conn: sqlite3.Connection, project_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(sequence), 0) AS max_seq FROM project_revisions WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    return int(row["max_seq"]) + 1


def _load_branch_command_state(
    conn: sqlite3.Connection,
    project_id: str,
    branch_id: str,
) -> BranchCommandState:
    row = conn.execute(
        """
        SELECT
            p.active_branch_id,
            b.id AS branch_id,
            b.name AS branch_name,
            b.head_revision_id,
            b.working_version,
            b.working_fingerprint,
            b.working_composition_json,
            r.sequence AS head_sequence,
            r.snapshot_fingerprint AS head_snapshot_fingerprint
        FROM projects p
        JOIN project_branches b
          ON b.project_id = p.id AND b.id = ?
        JOIN project_revisions r
          ON r.project_id = p.id AND r.id = b.head_revision_id
        WHERE p.id = ?
        """,
        (branch_id, project_id),
    ).fetchone()
    if row is None:
        raise ProjectHistoryError(
            f"Branch {branch_id} not found for project {project_id}"
        )
    return BranchCommandState(
        project_id=project_id,
        active_branch_id=row["active_branch_id"],
        branch_id=row["branch_id"],
        branch_name=row["branch_name"],
        head_revision_id=row["head_revision_id"],
        working_version=int(row["working_version"]),
        working_fingerprint=row["working_fingerprint"],
        working_composition_json=row["working_composition_json"],
        head_sequence=int(row["head_sequence"]),
        head_snapshot_fingerprint=row["head_snapshot_fingerprint"],
    )


def _raise_conflict(
    message: str,
    *,
    project_id: str,
    state: BranchCommandState | None = None,
    expected_active_branch_id: str | None = None,
    expected_working_version: int | None = None,
    expected_head_revision_id: str | None = None,
    expected_source_fingerprint: str | None = None,
) -> None:
    current_active = state.active_branch_id if state else None
    current_version = state.working_version if state else None
    current_head = state.head_revision_id if state else None
    current_fp = state.working_fingerprint if state else None
    current_seq = state.head_sequence if state else None
    logger.warning(
        "Project revision conflict",
        extra={
            "project_id": project_id,
            "code": ProjectRevisionConflictError.code,
            "expected_active_branch_id": expected_active_branch_id,
            "current_active_branch_id": current_active,
            "expected_working_version": expected_working_version,
            "current_working_version": current_version,
            "expected_head_revision_id": expected_head_revision_id,
            "current_head_revision_id": current_head,
            "expected_source_fingerprint_prefix": (
                snapshot_fingerprint_log_prefix(expected_source_fingerprint)
                if expected_source_fingerprint
                else None
            ),
            "current_working_fingerprint_prefix": (
                snapshot_fingerprint_log_prefix(current_fp) if current_fp else None
            ),
            "current_sequence": current_seq,
        },
    )
    raise ProjectRevisionConflictError(
        message,
        project_id=project_id,
        expected_active_branch_id=expected_active_branch_id,
        current_active_branch_id=current_active,
        expected_working_version=expected_working_version,
        current_working_version=current_version,
        expected_head_revision_id=expected_head_revision_id,
        current_head_revision_id=current_head,
        expected_source_fingerprint=expected_source_fingerprint,
        current_working_fingerprint=current_fp,
        current_sequence=current_seq,
    )


def _normalize_command_composition(
    composition: Any | None,
    *,
    project_id: str,
    clear: bool = False,
) -> tuple[Any, str | None, str]:
    """Return (composition_or_none, storage_json, fingerprint)."""
    if clear or composition is None:
        encoded = encode_composition_snapshot(None)
        return None, None, encoded.fingerprint
    if isinstance(composition, str) and composition == "":
        encoded = encode_composition_snapshot(None)
        return None, None, encoded.fingerprint
    normalized = normalize_project_composition(composition, project_id=project_id)
    storage_json = composition_to_storage_json(normalized.composition)
    fingerprint = composition_snapshot_fingerprint(normalized.composition)
    return normalized.composition, storage_json, fingerprint


def _verify_draft_preconditions(
    state: BranchCommandState,
    *,
    project_id: str,
    branch_id: str,
    expected_active_branch_id: str,
    expected_working_version: int,
    expected_source_fingerprint: str | None = None,
) -> None:
    if state.active_branch_id != expected_active_branch_id:
        _raise_conflict(
            "Active branch mismatch",
            project_id=project_id,
            state=state,
            expected_active_branch_id=expected_active_branch_id,
            expected_working_version=expected_working_version,
            expected_source_fingerprint=expected_source_fingerprint,
        )
    if state.branch_id != branch_id or branch_id != expected_active_branch_id:
        _raise_conflict(
            "Cannot mutate inactive branch draft",
            project_id=project_id,
            state=state,
            expected_active_branch_id=expected_active_branch_id,
            expected_working_version=expected_working_version,
            expected_source_fingerprint=expected_source_fingerprint,
        )
    if state.working_version != expected_working_version:
        _raise_conflict(
            "Working version mismatch",
            project_id=project_id,
            state=state,
            expected_active_branch_id=expected_active_branch_id,
            expected_working_version=expected_working_version,
            expected_source_fingerprint=expected_source_fingerprint,
        )
    if (
        expected_source_fingerprint is not None
        and state.working_fingerprint != expected_source_fingerprint
    ):
        _raise_conflict(
            "Source fingerprint mismatch",
            project_id=project_id,
            state=state,
            expected_active_branch_id=expected_active_branch_id,
            expected_working_version=expected_working_version,
            expected_source_fingerprint=expected_source_fingerprint,
        )


def _verify_durable_preconditions(
    state: BranchCommandState,
    *,
    project_id: str,
    branch_id: str,
    expected_active_branch_id: str,
    expected_working_version: int,
    expected_head_revision_id: str,
    expected_source_fingerprint: str,
) -> None:
    _verify_draft_preconditions(
        state,
        project_id=project_id,
        branch_id=branch_id,
        expected_active_branch_id=expected_active_branch_id,
        expected_working_version=expected_working_version,
        expected_source_fingerprint=expected_source_fingerprint,
    )
    if state.head_revision_id != expected_head_revision_id:
        _raise_conflict(
            "Head revision mismatch",
            project_id=project_id,
            state=state,
            expected_active_branch_id=expected_active_branch_id,
            expected_working_version=expected_working_version,
            expected_head_revision_id=expected_head_revision_id,
            expected_source_fingerprint=expected_source_fingerprint,
        )


def _materialize_active_branch(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    branch_id: str,
    head_revision_id: str,
    composition_json: str | None,
    now: str,
) -> None:
    conn.execute(
        """
        UPDATE projects
        SET active_branch_id = ?,
            current_revision_id = ?,
            composition_json = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (branch_id, head_revision_id, composition_json, now, project_id),
    )


def _update_branch_draft(
    conn: sqlite3.Connection,
    *,
    branch_id: str,
    composition_json: str | None,
    fingerprint: str,
    working_version: int,
    now: str,
    head_revision_id: str | None = None,
) -> None:
    if head_revision_id is None:
        conn.execute(
            """
            UPDATE project_branches
            SET working_composition_json = ?,
                working_fingerprint = ?,
                working_version = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (composition_json, fingerprint, working_version, now, branch_id),
        )
        return
    conn.execute(
        """
        UPDATE project_branches
        SET working_composition_json = ?,
            working_fingerprint = ?,
            working_version = ?,
            head_revision_id = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            composition_json,
            fingerprint,
            working_version,
            head_revision_id,
            now,
            branch_id,
        ),
    )


def _insert_revision(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    parent_revision_id: str | None,
    snapshot_fingerprint: str,
    sequence: int,
    operation_type: str,
    now: str,
    name: str | None = None,
    ai_provider: str | None = None,
    ai_model: str | None = None,
    user_instruction: str | None = None,
    affected_ranges_json: str = "[]",
    affected_track_ids_json: str = "[]",
    summary_json: str = "{}",
) -> str:
    revision_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO project_revisions (
            id, project_id, parent_revision_id, snapshot_fingerprint, sequence,
            name, operation_type, ai_provider, ai_model, user_instruction,
            affected_ranges_json, affected_track_ids_json, summary_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            revision_id,
            project_id,
            parent_revision_id,
            snapshot_fingerprint,
            sequence,
            name,
            operation_type,
            ai_provider,
            ai_model,
            user_instruction,
            affected_ranges_json,
            affected_track_ids_json,
            summary_json,
            now,
        ),
    )
    logger.info(
        "Created project revision",
        extra={
            "project_id": project_id,
            "revision_id": revision_id,
            "parent_revision_id": parent_revision_id,
            "sequence": sequence,
            "operation_type": operation_type,
            "fingerprint_prefix": snapshot_fingerprint_log_prefix(snapshot_fingerprint),
        },
    )
    return revision_id


def rename_project_fields(
    conn: sqlite3.Connection,
    project_id: str,
    *,
    name: str,
) -> None:
    """Update only the project name; does not create a revision or touch composition."""
    now = _utc_now_iso()
    logger.debug(
        "Renaming project",
        extra={"project_id": project_id, "name_length": len(name)},
    )
    cursor = conn.execute(
        """
        UPDATE projects
        SET name = ?, updated_at = ?
        WHERE id = ?
        """,
        (name, now, project_id),
    )
    if cursor.rowcount != 1:
        raise ProjectHistoryError(f"Project not found for rename: {project_id}")
    logger.info(
        "Project renamed",
        extra={"project_id": project_id, "name_length": len(name)},
    )


def save_branch_draft(
    conn: sqlite3.Connection,
    project_id: str,
    *,
    branch_id: str,
    expected_active_branch_id: str,
    expected_working_version: int,
    composition: Any | None,
    expected_source_fingerprint: str | None = None,
    clear_composition: bool = False,
) -> DraftSaveResult:
    """Autosave mutable branch draft with compare-and-swap; no immutable revision."""
    logger.debug(
        "Saving branch draft",
        extra={
            "project_id": project_id,
            "branch_id": branch_id,
            "expected_active_branch_id": expected_active_branch_id,
            "expected_working_version": expected_working_version,
            "stage": "start",
        },
    )
    ensure_project_history(conn, project_id)
    state = _load_branch_command_state(conn, project_id, branch_id)
    _verify_draft_preconditions(
        state,
        project_id=project_id,
        branch_id=branch_id,
        expected_active_branch_id=expected_active_branch_id,
        expected_working_version=expected_working_version,
        expected_source_fingerprint=expected_source_fingerprint,
    )
    _composition, storage_json, fingerprint = _normalize_command_composition(
        None if clear_composition else composition,
        project_id=project_id,
        clear=clear_composition,
    )
    now = _utc_now_iso()
    next_version = state.working_version + 1
    _update_branch_draft(
        conn,
        branch_id=branch_id,
        composition_json=storage_json,
        fingerprint=fingerprint,
        working_version=next_version,
        now=now,
    )
    _materialize_active_branch(
        conn,
        project_id=project_id,
        branch_id=branch_id,
        head_revision_id=state.head_revision_id,
        composition_json=storage_json,
        now=now,
    )
    assert_active_materialization(conn, project_id)
    logger.debug(
        "Branch draft saved",
        extra={
            "project_id": project_id,
            "branch_id": branch_id,
            "working_version": next_version,
            "fingerprint_prefix": snapshot_fingerprint_log_prefix(fingerprint),
            "revision_created": False,
        },
    )
    return DraftSaveResult(
        project_id=project_id,
        branch_id=branch_id,
        working_version=next_version,
        working_fingerprint=fingerprint,
        composition_json=storage_json,
        head_revision_id=state.head_revision_id,
        revision_created=False,
    )


def commit_durable_revision(
    conn: sqlite3.Connection,
    project_id: str,
    *,
    branch_id: str,
    expected_active_branch_id: str,
    expected_working_version: int,
    expected_head_revision_id: str,
    expected_source_fingerprint: str,
    composition: Any | None,
    operation_type: str,
    clear_composition: bool = False,
    checkpoint_dirty_draft: bool = False,
    pre_ai_operation_type: str = "pre-ai-checkpoint",
    name: str | None = None,
    ai_provider: str | None = None,
    ai_model: str | None = None,
    user_instruction: str | None = None,
    affected_ranges_json: str = "[]",
    affected_track_ids_json: str = "[]",
    summary_json: str = "{}",
) -> DurableCommitResult:
    """Create durable revision(s) and advance branch head/draft atomically."""
    logger.debug(
        "Committing durable revision",
        extra={
            "project_id": project_id,
            "branch_id": branch_id,
            "operation_type": operation_type,
            "checkpoint_dirty_draft": checkpoint_dirty_draft,
            "stage": "start",
        },
    )
    ensure_project_history(conn, project_id)
    state = _load_branch_command_state(conn, project_id, branch_id)
    _verify_durable_preconditions(
        state,
        project_id=project_id,
        branch_id=branch_id,
        expected_active_branch_id=expected_active_branch_id,
        expected_working_version=expected_working_version,
        expected_head_revision_id=expected_head_revision_id,
        expected_source_fingerprint=expected_source_fingerprint,
    )

    created_ids: list[str] = []
    now = _utc_now_iso()
    parent_id = state.head_revision_id
    sequence = state.head_sequence
    working_version = state.working_version
    draft_differs_from_head = state.working_fingerprint != state.head_snapshot_fingerprint

    if checkpoint_dirty_draft and draft_differs_from_head:
        if state.working_composition_json is None:
            draft_composition = None
        else:
            draft_composition = normalize_project_composition(
                state.working_composition_json,
                project_id=project_id,
            ).composition
        draft_encoded = encode_composition_snapshot(draft_composition)
        upsert_composition_snapshot(conn, draft_encoded, created_at=now)
        sequence = _next_revision_sequence(conn, project_id)
        pre_id = _insert_revision(
            conn,
            project_id=project_id,
            parent_revision_id=parent_id,
            snapshot_fingerprint=draft_encoded.fingerprint,
            sequence=sequence,
            operation_type=pre_ai_operation_type,
            now=now,
        )
        created_ids.append(pre_id)
        parent_id = pre_id
        working_version += 1
        # Promote draft to head before applying the AI child.
        _update_branch_draft(
            conn,
            branch_id=branch_id,
            composition_json=state.working_composition_json,
            fingerprint=state.working_fingerprint,
            working_version=working_version,
            now=now,
            head_revision_id=pre_id,
        )
        _materialize_active_branch(
            conn,
            project_id=project_id,
            branch_id=branch_id,
            head_revision_id=pre_id,
            composition_json=state.working_composition_json,
            now=now,
        )
        state = _load_branch_command_state(conn, project_id, branch_id)

    _composition, storage_json, fingerprint = _normalize_command_composition(
        None if clear_composition else composition,
        project_id=project_id,
        clear=clear_composition,
    )

    if fingerprint == state.head_snapshot_fingerprint:
        logger.info(
            "Durable commit no-op; head unchanged",
            extra={
                "project_id": project_id,
                "branch_id": branch_id,
                "revision_id": state.head_revision_id,
                "operation_type": operation_type,
                "revision_created": bool(created_ids),
                "created_count": len(created_ids),
            },
        )
        assert_active_materialization(conn, project_id)
        return DurableCommitResult(
            project_id=project_id,
            branch_id=branch_id,
            head_revision_id=state.head_revision_id,
            working_version=state.working_version,
            working_fingerprint=state.working_fingerprint,
            composition_json=state.working_composition_json,
            revision_created=bool(created_ids),
            created_revision_ids=tuple(created_ids),
            sequence=state.head_sequence,
            operation_type=pre_ai_operation_type if created_ids else operation_type,
        )

    encoded = encode_composition_snapshot(_composition)
    upsert_composition_snapshot(conn, encoded, created_at=now)
    sequence = _next_revision_sequence(conn, project_id)
    revision_id = _insert_revision(
        conn,
        project_id=project_id,
        parent_revision_id=parent_id,
        snapshot_fingerprint=fingerprint,
        sequence=sequence,
        operation_type=operation_type,
        now=now,
        name=name,
        ai_provider=ai_provider,
        ai_model=ai_model,
        user_instruction=user_instruction,
        affected_ranges_json=affected_ranges_json,
        affected_track_ids_json=affected_track_ids_json,
        summary_json=summary_json,
    )
    created_ids.append(revision_id)
    working_version = state.working_version + 1
    _update_branch_draft(
        conn,
        branch_id=branch_id,
        composition_json=storage_json,
        fingerprint=fingerprint,
        working_version=working_version,
        now=now,
        head_revision_id=revision_id,
    )
    _materialize_active_branch(
        conn,
        project_id=project_id,
        branch_id=branch_id,
        head_revision_id=revision_id,
        composition_json=storage_json,
        now=now,
    )
    assert_active_materialization(conn, project_id)
    logger.info(
        "Durable revision commit completed",
        extra={
            "project_id": project_id,
            "branch_id": branch_id,
            "revision_id": revision_id,
            "created_count": len(created_ids),
            "operation_type": operation_type,
            "sequence": sequence,
            "working_version": working_version,
            "fingerprint_prefix": snapshot_fingerprint_log_prefix(fingerprint),
        },
    )
    return DurableCommitResult(
        project_id=project_id,
        branch_id=branch_id,
        head_revision_id=revision_id,
        working_version=working_version,
        working_fingerprint=fingerprint,
        composition_json=storage_json,
        revision_created=True,
        created_revision_ids=tuple(created_ids),
        sequence=sequence,
        operation_type=operation_type,
    )


def restore_revision(
    conn: sqlite3.Connection,
    project_id: str,
    *,
    revision_id: str,
    branch_id: str,
    expected_active_branch_id: str,
    expected_working_version: int,
    expected_head_revision_id: str,
) -> DurableCommitResult:
    """Create a revision-restore child equal to the selected historical snapshot."""
    ensure_project_history(conn, project_id)
    state = _load_branch_command_state(conn, project_id, branch_id)
    if state.working_fingerprint != state.head_snapshot_fingerprint:
        raise ProjectHistoryError(
            "Restore requires a clean branch draft; save a checkpoint first"
        )
    _verify_durable_preconditions(
        state,
        project_id=project_id,
        branch_id=branch_id,
        expected_active_branch_id=expected_active_branch_id,
        expected_working_version=expected_working_version,
        expected_head_revision_id=expected_head_revision_id,
        expected_source_fingerprint=state.working_fingerprint,
    )
    source = conn.execute(
        """
        SELECT id, snapshot_fingerprint
        FROM project_revisions
        WHERE project_id = ? AND id = ?
        """,
        (project_id, revision_id),
    ).fetchone()
    if source is None:
        raise ProjectHistoryError(f"Revision not found: {revision_id}")

    composition = get_snapshot_composition(conn, source["snapshot_fingerprint"])
    storage_json = (
        None if composition is None else composition_to_storage_json(composition)
    )
    fingerprint = source["snapshot_fingerprint"]
    now = _utc_now_iso()
    sequence = _next_revision_sequence(conn, project_id)
    new_id = _insert_revision(
        conn,
        project_id=project_id,
        parent_revision_id=state.head_revision_id,
        snapshot_fingerprint=fingerprint,
        sequence=sequence,
        operation_type="revision-restore",
        now=now,
        summary_json=json.dumps(
            {"restored_from_revision_id": revision_id},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    )
    working_version = state.working_version + 1
    _update_branch_draft(
        conn,
        branch_id=branch_id,
        composition_json=storage_json,
        fingerprint=fingerprint,
        working_version=working_version,
        now=now,
        head_revision_id=new_id,
    )
    _materialize_active_branch(
        conn,
        project_id=project_id,
        branch_id=branch_id,
        head_revision_id=new_id,
        composition_json=storage_json,
        now=now,
    )
    assert_active_materialization(conn, project_id)
    logger.info(
        "Restored revision as new child",
        extra={
            "project_id": project_id,
            "branch_id": branch_id,
            "restored_from_revision_id": revision_id,
            "revision_id": new_id,
            "sequence": sequence,
        },
    )
    return DurableCommitResult(
        project_id=project_id,
        branch_id=branch_id,
        head_revision_id=new_id,
        working_version=working_version,
        working_fingerprint=fingerprint,
        composition_json=storage_json,
        revision_created=True,
        created_revision_ids=(new_id,),
        sequence=sequence,
        operation_type="revision-restore",
    )


def checkout_branch(
    conn: sqlite3.Connection,
    project_id: str,
    *,
    branch_id: str,
    expected_active_branch_id: str,
    expected_working_version: int,
    expected_head_revision_id: str,
) -> BranchCheckoutResult:
    """Switch the project's active branch without creating a revision."""
    ensure_project_history(conn, project_id)
    current = _load_branch_command_state(conn, project_id, expected_active_branch_id)
    _verify_durable_preconditions(
        current,
        project_id=project_id,
        branch_id=expected_active_branch_id,
        expected_active_branch_id=expected_active_branch_id,
        expected_working_version=expected_working_version,
        expected_head_revision_id=expected_head_revision_id,
        expected_source_fingerprint=current.working_fingerprint,
    )
    if current.working_fingerprint != current.head_snapshot_fingerprint:
        raise ProjectHistoryError(
            "Checkout requires a flushed branch draft; save or discard first"
        )
    target = _load_branch_command_state(conn, project_id, branch_id)
    now = _utc_now_iso()
    _materialize_active_branch(
        conn,
        project_id=project_id,
        branch_id=target.branch_id,
        head_revision_id=target.head_revision_id,
        composition_json=target.working_composition_json,
        now=now,
    )
    assert_active_materialization(conn, project_id)
    logger.info(
        "Checked out project branch",
        extra={
            "project_id": project_id,
            "from_branch_id": expected_active_branch_id,
            "branch_id": target.branch_id,
            "revision_id": target.head_revision_id,
        },
    )
    return BranchCheckoutResult(
        project_id=project_id,
        active_branch_id=target.branch_id,
        head_revision_id=target.head_revision_id,
        working_version=target.working_version,
        working_fingerprint=target.working_fingerprint,
        composition_json=target.working_composition_json,
    )


def apply_as_new_branch(
    conn: sqlite3.Connection,
    project_id: str,
    *,
    source_branch_id: str,
    expected_active_branch_id: str,
    expected_working_version: int,
    expected_head_revision_id: str,
    expected_source_fingerprint: str,
    branch_name: str,
    composition: Any | None,
    operation_type: str,
    clear_composition: bool = False,
    ai_provider: str | None = None,
    ai_model: str | None = None,
    user_instruction: str | None = None,
    affected_ranges_json: str = "[]",
    affected_track_ids_json: str = "[]",
    summary_json: str = "{}",
) -> ApplyAsBranchResult:
    """Create a named branch from the preview base and commit the candidate as its head."""
    ensure_project_history(conn, project_id)
    source = _load_branch_command_state(conn, project_id, source_branch_id)
    _verify_durable_preconditions(
        source,
        project_id=project_id,
        branch_id=source_branch_id,
        expected_active_branch_id=expected_active_branch_id,
        expected_working_version=expected_working_version,
        expected_head_revision_id=expected_head_revision_id,
        expected_source_fingerprint=expected_source_fingerprint,
    )
    display_name = branch_name.strip()
    if not display_name:
        raise ProjectHistoryError("Branch name must not be empty")
    normalized = normalize_branch_name(display_name)
    _composition, storage_json, fingerprint = _normalize_command_composition(
        None if clear_composition else composition,
        project_id=project_id,
        clear=clear_composition,
    )
    encoded = encode_composition_snapshot(_composition)
    now = _utc_now_iso()
    upsert_composition_snapshot(conn, encoded, created_at=now)
    sequence = _next_revision_sequence(conn, project_id)
    revision_id = _insert_revision(
        conn,
        project_id=project_id,
        parent_revision_id=source.head_revision_id,
        snapshot_fingerprint=fingerprint,
        sequence=sequence,
        operation_type=operation_type,
        now=now,
        ai_provider=ai_provider,
        ai_model=ai_model,
        user_instruction=user_instruction,
        affected_ranges_json=affected_ranges_json,
        affected_track_ids_json=affected_track_ids_json,
        summary_json=summary_json,
    )
    new_branch_id = str(uuid.uuid4())
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
                new_branch_id,
                project_id,
                display_name,
                normalized,
                revision_id,
                source.head_revision_id,
                storage_json,
                fingerprint,
                now,
                now,
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise ProjectHistoryError(
            f"Branch name already exists for project {project_id}"
        ) from exc

    _materialize_active_branch(
        conn,
        project_id=project_id,
        branch_id=new_branch_id,
        head_revision_id=revision_id,
        composition_json=storage_json,
        now=now,
    )
    assert_active_materialization(conn, project_id)

    # Source branch head must remain unchanged.
    source_after = _load_branch_command_state(conn, project_id, source_branch_id)
    if source_after.head_revision_id != source.head_revision_id:
        raise ProjectHistoryError("Apply-as-branch mutated source branch head")

    logger.info(
        "Applied candidate as new branch",
        extra={
            "project_id": project_id,
            "source_branch_id": source_branch_id,
            "branch_id": new_branch_id,
            "revision_id": revision_id,
            "name_length": len(display_name),
            "sequence": sequence,
            "operation_type": operation_type,
        },
    )
    return ApplyAsBranchResult(
        project_id=project_id,
        source_branch_id=source_branch_id,
        source_head_revision_id=source.head_revision_id,
        new_branch_id=new_branch_id,
        new_branch_name=display_name,
        head_revision_id=revision_id,
        working_version=0,
        working_fingerprint=fingerprint,
        composition_json=storage_json,
        sequence=sequence,
    )
