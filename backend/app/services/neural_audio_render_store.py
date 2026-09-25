"""Filesystem + SQLite metadata store for neural audio renders.

Audio PCM lives only under ``NEURAL_AUDIO_RENDER_ROOT``. SQLite rows hold
metadata. Never writes into ``composition_snapshots`` or ``projects.composition_json``.
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

from app.db.connection import get_connection, get_project_db_path
from app.neural_audio_schemas import (
    NEURAL_AUDIO_FIDELITY_LABELS,
    NeuralAudioError,
    NeuralAudioJobResponse,
)
from app.neural_audio_settings import NeuralAudioSettings, load_neural_audio_settings


logger = logging.getLogger(__name__)

SHA256_PREFIX_LEN = 16


@dataclass(frozen=True)
class AudioWriteResult:
    audio_relpath: str
    absolute_path: Path
    byte_size: int
    sha256_prefix: str
    content_type: str


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def allocate_render_id() -> str:
    return str(uuid.uuid4())


def project_render_dir(settings: NeuralAudioSettings, project_id: str | None) -> Path:
    segment = project_id if project_id else "_ephemeral"
    return settings.render_root / segment


def audio_relpath_for(project_id: str | None, render_id: str, *, ext: str = "wav") -> str:
    segment = project_id if project_id else "_ephemeral"
    safe_ext = ext.lstrip(".")
    return f"{segment}/{render_id}.{safe_ext}"


def absolute_audio_path(settings: NeuralAudioSettings, relpath: str) -> Path:
    root = settings.render_root.resolve()
    path = (settings.render_root / relpath).resolve()
    if root not in path.parents and path != root:
        raise NeuralAudioError(
            "neural_audio_internal_error",
            "Audio path escapes render root",
            http_status=500,
        )
    return path


def write_audio_bytes(
    settings: NeuralAudioSettings,
    *,
    project_id: str | None,
    render_id: str,
    payload: bytes,
    content_type: str = "audio/wav",
    ext: str = "wav",
) -> AudioWriteResult:
    if not payload:
        raise NeuralAudioError(
            "neural_audio_internal_error",
            "Refusing to write empty audio payload",
            http_status=500,
        )
    relpath = audio_relpath_for(project_id, render_id, ext=ext)
    abs_path = absolute_audio_path(settings, relpath)
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(payload).hexdigest()
    sha_prefix = digest[:SHA256_PREFIX_LEN]
    try:
        abs_path.write_bytes(payload)
    except OSError as exc:
        logger.error(
            "Failed to write neural audio file",
            extra={
                "render_id": render_id,
                "error_type": type(exc).__name__,
                "byte_size": len(payload),
            },
        )
        raise NeuralAudioError(
            "neural_audio_internal_error",
            "Failed to persist neural audio file",
            http_status=500,
        ) from exc
    logger.info(
        "Neural audio file written",
        extra={
            "render_id": render_id,
            "byte_size": len(payload),
            "sha256_prefix": sha_prefix,
            "content_type": content_type,
            "relpath_basename": Path(relpath).name,
        },
    )
    return AudioWriteResult(
        audio_relpath=relpath,
        absolute_path=abs_path,
        byte_size=len(payload),
        sha256_prefix=sha_prefix,
        content_type=content_type,
    )


def delete_audio_file(settings: NeuralAudioSettings, relpath: str | None) -> None:
    if not relpath:
        return
    try:
        path = absolute_audio_path(settings, relpath)
    except NeuralAudioError:
        logger.warning("Skipping delete for unsafe audio relpath", extra={"has_relpath": True})
        return
    try:
        if path.is_file():
            path.unlink()
            logger.info(
                "Neural audio file deleted",
                extra={"relpath_basename": Path(relpath).name},
            )
    except OSError as exc:
        logger.error(
            "Failed to delete neural audio file",
            extra={"error_type": type(exc).__name__, "relpath_basename": Path(relpath).name},
        )


def delete_project_render_files(settings: NeuralAudioSettings, project_id: str) -> None:
    directory = project_render_dir(settings, project_id)
    if not directory.exists():
        logger.debug("No neural audio dir for project delete", extra={"project_id": project_id})
        return
    try:
        shutil.rmtree(directory)
        logger.info("Neural audio project directory removed", extra={"project_id": project_id})
    except OSError as exc:
        logger.error(
            "Failed to remove neural audio project directory",
            extra={"project_id": project_id, "error_type": type(exc).__name__},
        )


def count_project_renders(
    conn: sqlite3.Connection,
    project_id: str,
) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM neural_audio_renders WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    return int(row["n"] if row is not None else 0)


def sum_project_audio_bytes(
    conn: sqlite3.Connection,
    project_id: str,
) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(byte_size), 0) AS total FROM neural_audio_renders WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    return int(row["total"] if row is not None else 0)


def assert_enqueue_quota(
    conn: sqlite3.Connection,
    settings: NeuralAudioSettings,
    project_id: str | None,
) -> None:
    if not project_id:
        return
    count = count_project_renders(conn, project_id)
    if count >= settings.max_renders_per_project:
        raise NeuralAudioError(
            "neural_audio_quota_exceeded",
            "Per-project neural render count quota exceeded",
            http_status=422,
            details={
                "limit": settings.max_renders_per_project,
                "current": count,
            },
        )
    total_bytes = sum_project_audio_bytes(conn, project_id)
    if total_bytes >= settings.max_total_bytes:
        raise NeuralAudioError(
            "neural_audio_quota_exceeded",
            "Per-project neural render storage quota exceeded",
            http_status=507,
            details={
                "limit_bytes": settings.max_total_bytes,
                "current_bytes": total_bytes,
            },
        )


def insert_queued_job(
    conn: sqlite3.Connection,
    *,
    render_id: str,
    project_id: str | None,
    source_revision_id: str | None,
    source_fingerprint: str,
    model_id: str,
    model_version: str | None,
    adapter_kind: str,
    fidelity_class: str,
    instructions: str,
    genre: str | None,
    mood: str | None,
    instrumentation_summary: str | None,
    tempo_bpm: float | None,
    seed: int | None,
    adapter_warnings: Sequence[str] | None = None,
    operation_run_id: str | None = None,
) -> dict[str, Any]:
    created_at = _utc_now_iso()
    warnings_json = json.dumps(list(adapter_warnings or []), separators=(",", ":"))
    conn.execute(
        """
        INSERT INTO neural_audio_renders (
            id, project_id, source_revision_id, source_fingerprint, status,
            model_id, model_version, adapter_kind, fidelity_class, instructions,
            genre, mood, instrumentation_summary, tempo_bpm, seed,
            adapter_warnings_json, created_at, operation_run_id
        ) VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            render_id,
            project_id,
            source_revision_id,
            source_fingerprint,
            model_id,
            model_version,
            adapter_kind,
            fidelity_class,
            instructions,
            genre,
            mood,
            instrumentation_summary,
            tempo_bpm,
            seed,
            warnings_json,
            created_at,
            operation_run_id,
        ),
    )
    logger.info(
        "Neural audio job enqueued",
        extra={
            "render_id": render_id,
            "project_id": project_id,
            "source_revision_id": source_revision_id,
            "model_id": model_id,
            "adapter_kind": adapter_kind,
            "fidelity_class": fidelity_class,
            "status": "queued",
            "fingerprint_prefix": source_fingerprint[:12],
        },
    )
    return get_job_row(conn, render_id)


def update_job_status(
    conn: sqlite3.Connection,
    render_id: str,
    *,
    status: str,
    error_code: str | None = None,
    error_message: str | None = None,
    audio_relpath: str | None = None,
    content_type: str | None = None,
    byte_size: int | None = None,
    sha256_prefix: str | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
) -> dict[str, Any]:
    row = get_job_row(conn, render_id)
    if row is None:
        raise NeuralAudioError(
            "render_not_found",
            "Neural audio render not found",
            http_status=404,
            details={"render_id": render_id},
        )
    now = _utc_now_iso()
    if status == "complete" and str(row.get("error_code") or "") == "operation_cancelled":
        logger.info(
            "Discarding late neural render completion after cancel",
            extra={"render_id": render_id},
        )
        return row
    conn.execute(
        """
        UPDATE neural_audio_renders SET
            status = ?,
            error_code = COALESCE(?, error_code),
            error_message = COALESCE(?, error_message),
            audio_relpath = COALESCE(?, audio_relpath),
            content_type = COALESCE(?, content_type),
            byte_size = COALESCE(?, byte_size),
            sha256_prefix = COALESCE(?, sha256_prefix),
            started_at = COALESCE(?, started_at),
            completed_at = COALESCE(?, completed_at)
        WHERE id = ?
        """,
        (
            status,
            error_code,
            error_message,
            audio_relpath,
            content_type,
            byte_size,
            sha256_prefix,
            started_at or (now if status == "running" and not row["started_at"] else None),
            completed_at
            or (now if status in {"complete", "failed"} and not row["completed_at"] else None),
            render_id,
        ),
    )
    logger.info(
        "Neural audio job status transition",
        extra={
            "render_id": render_id,
            "status": status,
            "model_id": row["model_id"],
            "adapter_kind": row["adapter_kind"],
            "fidelity_class": row["fidelity_class"],
            "error_code": error_code,
            "byte_size": byte_size,
            "sha256_prefix": sha256_prefix,
        },
    )
    return get_job_row(conn, render_id)  # type: ignore[return-value]


def get_job_row(conn: sqlite3.Connection, render_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM neural_audio_renders WHERE id = ?",
        (render_id,),
    ).fetchone()
    if row is None:
        return None
    return dict(row)


def list_job_rows(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM neural_audio_renders
        WHERE project_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (project_id, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def delete_job(
    conn: sqlite3.Connection,
    settings: NeuralAudioSettings,
    render_id: str,
) -> None:
    row = get_job_row(conn, render_id)
    if row is None:
        raise NeuralAudioError(
            "render_not_found",
            "Neural audio render not found",
            http_status=404,
            details={"render_id": render_id},
        )
    delete_audio_file(settings, row.get("audio_relpath"))
    conn.execute("DELETE FROM neural_audio_renders WHERE id = ?", (render_id,))
    logger.info(
        "Neural audio job deleted",
        extra={"render_id": render_id, "project_id": row.get("project_id")},
    )


def row_to_response(row: Mapping[str, Any]) -> NeuralAudioJobResponse:
    warnings_raw = row.get("adapter_warnings_json") or "[]"
    try:
        warnings = json.loads(warnings_raw)
        if not isinstance(warnings, list):
            warnings = []
    except json.JSONDecodeError:
        warnings = []
    fidelity = str(row["fidelity_class"])
    return NeuralAudioJobResponse(
        id=str(row["id"]),
        project_id=row.get("project_id"),
        source_revision_id=row.get("source_revision_id"),
        source_fingerprint=str(row["source_fingerprint"]),
        status=row["status"],  # type: ignore[arg-type]
        model_id=str(row["model_id"]),
        model_version=row.get("model_version"),
        adapter_kind=row["adapter_kind"],  # type: ignore[arg-type]
        fidelity_class=fidelity,  # type: ignore[arg-type]
        fidelity_label=NEURAL_AUDIO_FIDELITY_LABELS.get(fidelity, fidelity),
        instructions=str(row.get("instructions") or ""),
        genre=row.get("genre"),
        mood=row.get("mood"),
        instrumentation_summary=row.get("instrumentation_summary"),
        tempo_bpm=row.get("tempo_bpm"),
        seed=row.get("seed"),
        error_code=row.get("error_code"),
        error_message=row.get("error_message"),
        audio_relpath=row.get("audio_relpath"),
        content_type=row.get("content_type"),
        byte_size=row.get("byte_size"),
        sha256_prefix=row.get("sha256_prefix"),
        created_at=str(row["created_at"]),
        started_at=row.get("started_at"),
        completed_at=row.get("completed_at"),
        adapter_warnings=[str(item) for item in warnings],
        operation_run_id=row.get("operation_run_id"),
        attempt_count=int(row.get("attempt_count") or 0),
        mutates_composition=False,
    )


def increment_attempt_count(conn: sqlite3.Connection, render_id: str) -> int:
    row = get_job_row(conn, render_id)
    if row is None:
        raise NeuralAudioError(
            "render_not_found",
            "Neural audio render not found",
            http_status=404,
            details={"render_id": render_id},
        )
    conn.execute(
        "UPDATE neural_audio_renders SET attempt_count = attempt_count + 1 WHERE id = ?",
        (render_id,),
    )
    updated = get_job_row(conn, render_id)
    return int((updated or row).get("attempt_count") or 0)


def open_store_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path is not None else get_project_db_path()
    return get_connection(path)


def load_settings_and_root(
    env: Mapping[str, str] | None = None,
) -> NeuralAudioSettings:
    settings = load_neural_audio_settings(env)
    settings.render_root.mkdir(parents=True, exist_ok=True)
    return settings
