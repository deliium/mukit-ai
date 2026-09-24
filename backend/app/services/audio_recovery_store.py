"""Filesystem + SQLite metadata store for V4 audio recovery jobs/assets.

PCM and result JSON live only under ``AUDIO_RECOVERY_ASSET_ROOT``.
SQLite rows hold metadata. Never writes into ``composition_snapshots`` or
``projects.composition_json``. Durable source audio + result JSON are written
only via the Bind path (see pipeline/bind service).
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.audio_recovery_schemas import AudioRecoveryError
from app.audio_recovery_settings import AudioRecoverySettings, load_audio_recovery_settings
from app.db.connection import get_connection, get_project_db_path


logger = logging.getLogger(__name__)

SHA256_PREFIX_LEN = 16
JOBS_SEGMENT = "_jobs"


@dataclass(frozen=True)
class AssetWriteResult:
    asset_id: str
    relpath: str
    absolute_path: Path
    byte_size: int
    sha256_prefix: str
    content_type: str
    kind: str


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def allocate_job_id() -> str:
    return str(uuid.uuid4())


def allocate_asset_id() -> str:
    return str(uuid.uuid4())


def job_work_relpath(job_id: str) -> str:
    return f"{JOBS_SEGMENT}/{job_id}"


def project_asset_dir(settings: AudioRecoverySettings, project_id: str) -> Path:
    return settings.asset_root / project_id


def job_work_dir(settings: AudioRecoverySettings, job_id: str) -> Path:
    return settings.asset_root / JOBS_SEGMENT / job_id


def absolute_under_asset_root(settings: AudioRecoverySettings, relpath: str) -> Path:
    """Resolve ``relpath`` under asset root; reject path escapes."""
    root = settings.asset_root.resolve()
    path = (settings.asset_root / relpath).resolve()
    if root not in path.parents and path != root:
        logger.error(
            "Audio recovery path confinement rejected escape",
            extra={"relpath_basename": Path(relpath).name},
        )
        raise AudioRecoveryError(
            "audio_recovery_internal_error",
            "Path escapes audio recovery asset root",
            http_status=500,
        )
    return path


def ensure_job_workdir(settings: AudioRecoverySettings, job_id: str) -> Path:
    directory = job_work_dir(settings, job_id)
    # Confirm confinement before mkdir.
    absolute_under_asset_root(settings, job_work_relpath(job_id))
    directory.mkdir(parents=True, exist_ok=True)
    logger.debug(
        "Audio recovery job workdir ready",
        extra={"job_id": job_id, "asset_root_basename": settings.asset_root.name},
    )
    return directory


def write_job_bytes(
    settings: AudioRecoverySettings,
    *,
    job_id: str,
    relative_name: str,
    payload: bytes,
) -> tuple[str, str, int]:
    """Write bytes into the ephemeral job workdir. Returns (relpath, sha_prefix, byte_size)."""
    if not payload:
        raise AudioRecoveryError(
            "audio_recovery_internal_error",
            "Refusing to write empty recovery payload",
            http_status=500,
        )
    safe_name = Path(relative_name).name
    if not safe_name or safe_name in {".", ".."}:
        raise AudioRecoveryError(
            "audio_recovery_internal_error",
            "Invalid recovery workdir filename",
            http_status=500,
        )
    relpath = f"{job_work_relpath(job_id)}/{safe_name}"
    abs_path = absolute_under_asset_root(settings, relpath)
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(payload).hexdigest()
    sha_prefix = digest[:SHA256_PREFIX_LEN]
    try:
        abs_path.write_bytes(payload)
    except OSError as exc:
        logger.error(
            "Failed to write audio recovery job file",
            extra={
                "job_id": job_id,
                "error_type": type(exc).__name__,
                "byte_size": len(payload),
            },
        )
        raise AudioRecoveryError(
            "audio_recovery_internal_error",
            "Failed to persist recovery job file",
            http_status=500,
        ) from exc
    logger.info(
        "Audio recovery job file written",
        extra={
            "job_id": job_id,
            "byte_size": len(payload),
            "sha256_prefix": sha_prefix,
            "relpath_basename": safe_name,
        },
    )
    return relpath, sha_prefix, len(payload)


def write_durable_asset_bytes(
    settings: AudioRecoverySettings,
    *,
    project_id: str,
    job_id: str,
    kind: str,
    payload: bytes,
    content_type: str,
    ext: str,
) -> AssetWriteResult:
    """Persist a durable project asset under ``{root}/{project_id}/`` (Bind path)."""
    if not project_id:
        raise AudioRecoveryError(
            "audio_recovery_project_required",
            "Durable recovery assets require project_id",
            http_status=422,
        )
    if not payload:
        raise AudioRecoveryError(
            "audio_recovery_internal_error",
            "Refusing to write empty durable recovery asset",
            http_status=500,
        )
    if kind not in {"source_audio", "result_json", "alignment_json"}:
        raise AudioRecoveryError(
            "audio_recovery_internal_error",
            "Invalid recovery asset kind",
            http_status=500,
        )
    asset_id = allocate_asset_id()
    safe_ext = ext.lstrip(".")
    relpath = f"{project_id}/{asset_id}.{safe_ext}"
    abs_path = absolute_under_asset_root(settings, relpath)
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(payload).hexdigest()
    sha_prefix = digest[:SHA256_PREFIX_LEN]
    try:
        abs_path.write_bytes(payload)
    except OSError as exc:
        logger.error(
            "Failed to write durable audio recovery asset",
            extra={
                "job_id": job_id,
                "project_id": project_id,
                "kind": kind,
                "error_type": type(exc).__name__,
                "byte_size": len(payload),
            },
        )
        raise AudioRecoveryError(
            "audio_recovery_internal_error",
            "Failed to persist durable recovery asset",
            http_status=500,
        ) from exc
    logger.info(
        "Audio recovery durable asset written",
        extra={
            "asset_id": asset_id,
            "job_id": job_id,
            "project_id": project_id,
            "kind": kind,
            "byte_size": len(payload),
            "sha256_prefix": sha_prefix,
            "relpath_basename": Path(relpath).name,
        },
    )
    return AssetWriteResult(
        asset_id=asset_id,
        relpath=relpath,
        absolute_path=abs_path,
        byte_size=len(payload),
        sha256_prefix=sha_prefix,
        content_type=content_type,
        kind=kind,
    )


def delete_job_workdir(settings: AudioRecoverySettings, job_id: str) -> None:
    directory = job_work_dir(settings, job_id)
    try:
        absolute_under_asset_root(settings, job_work_relpath(job_id))
    except AudioRecoveryError:
        logger.warning("Skipping job workdir delete for unsafe path", extra={"job_id": job_id})
        return
    if not directory.exists():
        logger.debug("No audio recovery job workdir to delete", extra={"job_id": job_id})
        return
    try:
        shutil.rmtree(directory)
        logger.info("Audio recovery job workdir deleted", extra={"job_id": job_id})
    except OSError as exc:
        logger.warning(
            "Failed to delete audio recovery job workdir",
            extra={"job_id": job_id, "error_type": type(exc).__name__},
        )


def delete_project_audio_recovery_files(
    settings: AudioRecoverySettings, project_id: str
) -> None:
    directory = project_asset_dir(settings, project_id)
    try:
        absolute_under_asset_root(settings, project_id)
    except AudioRecoveryError:
        logger.warning(
            "Skipping project recovery FS cleanup for unsafe path",
            extra={"project_id": project_id},
        )
        return
    if not directory.exists():
        logger.debug(
            "No audio recovery project dir for delete",
            extra={"project_id": project_id},
        )
        return
    try:
        shutil.rmtree(directory)
        logger.info(
            "Audio recovery project directory removed",
            extra={"project_id": project_id},
        )
    except OSError as exc:
        logger.error(
            "Failed to remove audio recovery project directory",
            extra={"project_id": project_id, "error_type": type(exc).__name__},
        )


def cleanup_project_audio_recovery(
    project_id: str,
    *,
    settings: AudioRecoverySettings | None = None,
) -> None:
    """Delete FS tree for a project. WARN-only failures (caller continues delete)."""
    resolved = settings if settings is not None else load_audio_recovery_settings()
    logger.info(
        "Audio recovery project FS cleanup starting",
        extra={
            "project_id": project_id,
            "asset_root_basename": resolved.asset_root.name,
        },
    )
    delete_project_audio_recovery_files(resolved, project_id)


def count_project_jobs(conn: sqlite3.Connection, project_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM audio_recovery_jobs WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    return int(row["n"] if row is not None else 0)


def count_project_assets(conn: sqlite3.Connection, project_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM audio_recovery_assets WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    return int(row["n"] if row is not None else 0)


def sum_project_asset_bytes(conn: sqlite3.Connection, project_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(byte_size), 0) AS total FROM audio_recovery_assets "
        "WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    return int(row["total"] if row is not None else 0)


def assert_job_quota(
    conn: sqlite3.Connection,
    settings: AudioRecoverySettings,
    project_id: str | None,
) -> None:
    if not project_id:
        return
    count = count_project_jobs(conn, project_id)
    if count >= settings.max_jobs_per_project:
        raise AudioRecoveryError(
            "audio_recovery_quota_exceeded",
            "Per-project audio recovery job quota exceeded",
            http_status=422,
            details={
                "limit": settings.max_jobs_per_project,
                "current": count,
            },
        )


def assert_asset_quota(
    conn: sqlite3.Connection,
    settings: AudioRecoverySettings,
    project_id: str,
    *,
    additional: int = 1,
) -> None:
    count = count_project_assets(conn, project_id)
    if count + max(1, int(additional)) > settings.max_assets_per_project:
        raise AudioRecoveryError(
            "audio_recovery_quota_exceeded",
            "Per-project audio recovery asset count quota exceeded",
            http_status=422,
            details={
                "limit": settings.max_assets_per_project,
                "current": count,
                "additional": additional,
            },
        )
    total_bytes = sum_project_asset_bytes(conn, project_id)
    if total_bytes >= settings.max_total_bytes:
        raise AudioRecoveryError(
            "audio_recovery_quota_exceeded",
            "Per-project audio recovery storage quota exceeded",
            http_status=507,
            details={
                "limit_bytes": settings.max_total_bytes,
                "current_bytes": total_bytes,
            },
        )


def insert_queued_job(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    project_id: str | None,
    engine_id: str,
    separation_engine_id: str | None,
    fake: bool,
    work_relpath: str,
) -> dict[str, Any]:
    created_at = _utc_now_iso()
    conn.execute(
        """
        INSERT INTO audio_recovery_jobs (
            id, project_id, status, engine_id, separation_engine_id, fake,
            work_relpath, bound, created_at
        ) VALUES (?, ?, 'queued', ?, ?, ?, ?, 0, ?)
        """,
        (
            job_id,
            project_id,
            engine_id,
            separation_engine_id,
            1 if fake else 0,
            work_relpath,
            created_at,
        ),
    )
    logger.info(
        "Audio recovery job enqueued",
        extra={
            "job_id": job_id,
            "project_id": project_id,
            "engine_id": engine_id,
            "separation_engine_id": separation_engine_id,
            "fake": fake,
            "status": "queued",
        },
    )
    return get_job_row(conn, job_id)  # type: ignore[return-value]


def update_job_status(
    conn: sqlite3.Connection,
    job_id: str,
    *,
    status: str,
    error_code: str | None = None,
    error_message: str | None = None,
    preview_fingerprint: str | None = None,
    preview_json: str | None = None,
    source_relpath: str | None = None,
    source_sha256_prefix: str | None = None,
    source_byte_size: int | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
    bound: bool | None = None,
    source_audio_asset_id: str | None = None,
    result_asset_id: str | None = None,
    alignment_asset_id: str | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    row = get_job_row(conn, job_id)
    if row is None:
        raise AudioRecoveryError(
            "audio_recovery_job_not_found",
            "Audio recovery job not found",
            http_status=404,
            details={"job_id": job_id},
        )
    now = _utc_now_iso()
    conn.execute(
        """
        UPDATE audio_recovery_jobs SET
            status = ?,
            error_code = COALESCE(?, error_code),
            error_message = COALESCE(?, error_message),
            preview_fingerprint = COALESCE(?, preview_fingerprint),
            preview_json = COALESCE(?, preview_json),
            source_relpath = COALESCE(?, source_relpath),
            source_sha256_prefix = COALESCE(?, source_sha256_prefix),
            source_byte_size = COALESCE(?, source_byte_size),
            started_at = COALESCE(?, started_at),
            completed_at = COALESCE(?, completed_at),
            bound = COALESCE(?, bound),
            source_audio_asset_id = COALESCE(?, source_audio_asset_id),
            result_asset_id = COALESCE(?, result_asset_id),
            alignment_asset_id = COALESCE(?, alignment_asset_id),
            project_id = COALESCE(?, project_id)
        WHERE id = ?
        """,
        (
            status,
            error_code,
            error_message,
            preview_fingerprint,
            preview_json,
            source_relpath,
            source_sha256_prefix,
            source_byte_size,
            started_at or (now if status == "running" and not row["started_at"] else None),
            completed_at
            or (now if status in {"complete", "failed"} and not row["completed_at"] else None),
            None if bound is None else (1 if bound else 0),
            source_audio_asset_id,
            result_asset_id,
            alignment_asset_id,
            project_id,
            job_id,
        ),
    )
    logger.info(
        "Audio recovery job status transition",
        extra={
            "job_id": job_id,
            "status": status,
            "engine_id": row["engine_id"],
            "error_code": error_code,
            "source_byte_size": source_byte_size,
            "sha256_prefix": source_sha256_prefix,
            "bound": bound,
            "alignment_asset_id_prefix": (alignment_asset_id or "")[:8] or None,
        },
    )
    return get_job_row(conn, job_id)  # type: ignore[return-value]


def insert_asset_row(
    conn: sqlite3.Connection,
    *,
    asset_id: str,
    project_id: str,
    job_id: str,
    kind: str,
    content_type: str,
    byte_size: int,
    sha256_prefix: str,
    relpath: str,
) -> dict[str, Any]:
    created_at = _utc_now_iso()
    conn.execute(
        """
        INSERT INTO audio_recovery_assets (
            id, project_id, job_id, kind, content_type, byte_size,
            sha256_prefix, relpath, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            asset_id,
            project_id,
            job_id,
            kind,
            content_type,
            byte_size,
            sha256_prefix,
            relpath,
            created_at,
        ),
    )
    logger.info(
        "Audio recovery asset row inserted",
        extra={
            "asset_id": asset_id,
            "project_id": project_id,
            "job_id": job_id,
            "kind": kind,
            "byte_size": byte_size,
            "sha256_prefix": sha256_prefix,
        },
    )
    return get_asset_row(conn, asset_id)  # type: ignore[return-value]


def get_job_row(conn: sqlite3.Connection, job_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM audio_recovery_jobs WHERE id = ?",
        (job_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def get_asset_row(conn: sqlite3.Connection, asset_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM audio_recovery_assets WHERE id = ?",
        (asset_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def delete_job_row(conn: sqlite3.Connection, job_id: str) -> None:
    conn.execute("DELETE FROM audio_recovery_assets WHERE job_id = ?", (job_id,))
    conn.execute("DELETE FROM audio_recovery_jobs WHERE id = ?", (job_id,))
    logger.info("Audio recovery job row deleted", extra={"job_id": job_id})


def list_project_jobs(
    conn: sqlite3.Connection,
    project_id: str,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM audio_recovery_jobs
        WHERE project_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (project_id, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def list_bound_project_jobs(
    conn: sqlite3.Connection,
    project_id: str,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Bound jobs only (durable source + result + optional alignment)."""
    rows = conn.execute(
        """
        SELECT * FROM audio_recovery_jobs
        WHERE project_id = ? AND bound = 1
          AND source_audio_asset_id IS NOT NULL
          AND result_asset_id IS NOT NULL
        ORDER BY completed_at DESC, created_at DESC
        LIMIT ?
        """,
        (project_id, limit),
    ).fetchall()
    return [dict(row) for row in rows]
