"""Filesystem + SQLite metadata store for neural audio stem sets/stems.

PCM lives only under ``NEURAL_AUDIO_RENDER_ROOT``. Never writes composition
snapshots or ``projects.composition_json``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.db.connection import get_project_db_path
from app.neural_audio_schemas import (
    NEURAL_AUDIO_FIDELITY_LABELS,
    NeuralAudioBarRange,
    NeuralAudioError,
    NeuralAudioStemResponse,
    NeuralAudioStemSetResponse,
    NeuralAudioTimelineSync,
)
from app.neural_audio_settings import NeuralAudioSettings
from app.services.neural_audio_render_store import (
    SHA256_PREFIX_LEN,
    AudioWriteResult,
    _utc_now_iso,
    absolute_audio_path,
    load_settings_and_root,
)


logger = logging.getLogger(__name__)


def allocate_stem_set_id() -> str:
    return str(uuid.uuid4())


def allocate_stem_id() -> str:
    return str(uuid.uuid4())


def stem_audio_relpath(
    project_id: str | None,
    stem_set_id: str,
    stem_id: str,
    *,
    ext: str = "wav",
) -> str:
    segment = project_id if project_id else "_ephemeral"
    safe_ext = ext.lstrip(".")
    return f"{segment}/{stem_set_id}/{stem_id}.{safe_ext}"


def write_stem_audio_bytes(
    settings: NeuralAudioSettings,
    *,
    project_id: str | None,
    stem_set_id: str,
    stem_id: str,
    payload: bytes,
    content_type: str = "audio/wav",
    ext: str = "wav",
) -> AudioWriteResult:
    if not payload:
        raise NeuralAudioError(
            "neural_audio_internal_error",
            "Refusing to write empty stem audio payload",
            http_status=500,
        )
    relpath = stem_audio_relpath(project_id, stem_set_id, stem_id, ext=ext)
    abs_path = absolute_audio_path(settings, relpath)
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(payload).hexdigest()
    sha_prefix = digest[:SHA256_PREFIX_LEN]
    try:
        abs_path.write_bytes(payload)
    except OSError as exc:
        logger.error(
            "Failed to write neural stem audio file",
            extra={
                "stem_id": stem_id,
                "stem_set_id": stem_set_id,
                "error_type": type(exc).__name__,
                "byte_size": len(payload),
            },
        )
        raise NeuralAudioError(
            "neural_audio_internal_error",
            "Failed to persist stem audio file",
            http_status=500,
        ) from exc
    logger.info(
        "Neural stem audio file written",
        extra={
            "stem_id": stem_id,
            "stem_set_id": stem_set_id,
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


def delete_stem_set_files(
    settings: NeuralAudioSettings,
    project_id: str | None,
    stem_set_id: str,
) -> None:
    segment = project_id if project_id else "_ephemeral"
    directory = (settings.render_root / segment / stem_set_id).resolve()
    root = settings.render_root.resolve()
    if root not in directory.parents and directory != root:
        logger.warning(
            "Refusing to delete stem set dir outside render root",
            extra={"stem_set_id": stem_set_id},
        )
        return
    if not directory.exists():
        return
    try:
        shutil.rmtree(directory)
        logger.info(
            "Neural stem set directory removed",
            extra={"stem_set_id": stem_set_id, "project_id": project_id},
        )
    except OSError as exc:
        logger.error(
            "Failed to remove stem set directory",
            extra={"stem_set_id": stem_set_id, "error_type": type(exc).__name__},
        )


def count_project_stem_assets(conn: sqlite3.Connection, project_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM neural_audio_stems WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    return int(row["n"] if row is not None else 0)


def sum_project_stem_bytes(conn: sqlite3.Connection, project_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(byte_size), 0) AS total FROM neural_audio_stems "
        "WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    return int(row["total"] if row is not None else 0)


def assert_stem_enqueue_quota(
    conn: sqlite3.Connection,
    settings: NeuralAudioSettings,
    project_id: str | None,
    *,
    upcoming_stems: int,
) -> None:
    """Quota counts mix renders + stem members against the same project caps."""
    if not project_id:
        return
    from app.services.neural_audio_render_store import (
        count_project_renders,
        sum_project_audio_bytes,
    )

    mix_count = count_project_renders(conn, project_id)
    stem_count = count_project_stem_assets(conn, project_id)
    total = mix_count + stem_count + max(0, upcoming_stems)
    if total > settings.max_renders_per_project:
        raise NeuralAudioError(
            "neural_audio_quota_exceeded",
            "Per-project neural render count quota exceeded",
            http_status=422,
            details={
                "limit": settings.max_renders_per_project,
                "current": mix_count + stem_count,
                "upcoming_stems": upcoming_stems,
            },
        )
    total_bytes = sum_project_audio_bytes(conn, project_id) + sum_project_stem_bytes(
        conn, project_id
    )
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


def insert_queued_stem_set(
    conn: sqlite3.Connection,
    *,
    stem_set_id: str,
    project_id: str | None,
    source_revision_id: str | None,
    source_fingerprint: str,
    model_id: str,
    engine: str,
    origin_tick: int,
    duration_ticks: int,
    tempo_bpm: float | None,
    sample_rate: int,
    operation_run_id: str | None = None,
) -> dict[str, Any]:
    created_at = _utc_now_iso()
    conn.execute(
        """
        INSERT INTO neural_audio_stem_sets (
            id, project_id, source_revision_id, source_fingerprint, status,
            model_id, engine, origin_tick, duration_ticks, tempo_bpm, sample_rate,
            created_at, operation_run_id
        ) VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            stem_set_id,
            project_id,
            source_revision_id,
            source_fingerprint,
            model_id,
            engine,
            origin_tick,
            duration_ticks,
            tempo_bpm,
            sample_rate,
            created_at,
            operation_run_id,
        ),
    )
    logger.info(
        "Neural stem set enqueued",
        extra={
            "stem_set_id": stem_set_id,
            "project_id": project_id,
            "model_id": model_id,
            "engine": engine,
            "fingerprint_prefix": source_fingerprint[:12],
        },
    )
    return get_stem_set_row(conn, stem_set_id)  # type: ignore[return-value]


def insert_queued_stem(
    conn: sqlite3.Connection,
    *,
    stem_id: str,
    stem_set_id: str,
    project_id: str | None,
    stem_role: str,
    source_track_ids: Sequence[str],
    source_revision_id: str | None,
    source_fingerprint: str,
    model_id: str,
    model_version: str | None,
    adapter_kind: str | None,
    fidelity_class: str,
    capability_used: str,
    sync_class: str,
    engine: str,
    instructions: str,
    genre: str | None,
    mood: str | None,
    tempo_bpm: float | None,
    seed: int | None,
    sample_rate: int | None,
    origin_tick: int,
    duration_ticks: int,
    bar_start: int | None,
    bar_end: int | None,
    supersedes_stem_id: str | None = None,
    adapter_warnings: Sequence[str] | None = None,
) -> dict[str, Any]:
    created_at = _utc_now_iso()
    track_json = json.dumps(list(source_track_ids), separators=(",", ":"))
    warnings_json = json.dumps(list(adapter_warnings or []), separators=(",", ":"))
    instruction_chars = len(instructions or "")
    conn.execute(
        """
        INSERT INTO neural_audio_stems (
            id, stem_set_id, project_id, stem_role, source_track_ids_json,
            source_revision_id, source_fingerprint, status, model_id, model_version,
            adapter_kind, fidelity_class, capability_used, sync_class, engine,
            instructions, instruction_chars, genre, mood, tempo_bpm, seed,
            sample_rate, origin_tick, duration_ticks, bar_start, bar_end,
            supersedes_stem_id, adapter_warnings_json, created_at
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            stem_id,
            stem_set_id,
            project_id,
            stem_role,
            track_json,
            source_revision_id,
            source_fingerprint,
            model_id,
            model_version,
            adapter_kind,
            fidelity_class,
            capability_used,
            sync_class,
            engine,
            instructions or "",
            instruction_chars,
            genre,
            mood,
            tempo_bpm,
            seed,
            sample_rate,
            origin_tick,
            duration_ticks,
            bar_start,
            bar_end,
            supersedes_stem_id,
            warnings_json,
            created_at,
        ),
    )
    logger.info(
        "Neural stem member enqueued",
        extra={
            "stem_id": stem_id,
            "stem_set_id": stem_set_id,
            "stem_role": stem_role,
            "capability_used": capability_used,
            "track_count": len(source_track_ids),
            "fingerprint_prefix": source_fingerprint[:12],
        },
    )
    return get_stem_row(conn, stem_id)  # type: ignore[return-value]


def update_stem_set_status(
    conn: sqlite3.Connection,
    stem_set_id: str,
    *,
    status: str,
    error_code: str | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    row = get_stem_set_row(conn, stem_set_id)
    if row is None:
        raise NeuralAudioError(
            "stem_set_not_found",
            "Neural audio stem set not found",
            http_status=404,
            details={"stem_set_id": stem_set_id},
        )
    now = _utc_now_iso()
    if status == "complete" and str(row.get("error_code") or "") == "operation_cancelled":
        logger.info(
            "Discarding late stem-set completion after cancel",
            extra={"stem_set_id": stem_set_id},
        )
        return row
    started = now if status == "running" and not row.get("started_at") else None
    completed = (
        now if status in {"complete", "failed"} and not row.get("completed_at") else None
    )
    conn.execute(
        """
        UPDATE neural_audio_stem_sets SET
            status = ?,
            error_code = COALESCE(?, error_code),
            error_message = COALESCE(?, error_message),
            started_at = COALESCE(?, started_at),
            completed_at = COALESCE(?, completed_at)
        WHERE id = ?
        """,
        (status, error_code, error_message, started, completed, stem_set_id),
    )
    logger.info(
        "Neural stem set status transition",
        extra={
            "stem_set_id": stem_set_id,
            "status": status,
            "error_code": error_code,
        },
    )
    return get_stem_set_row(conn, stem_set_id)  # type: ignore[return-value]


def increment_stem_set_attempt(conn: sqlite3.Connection, stem_set_id: str) -> int:
    row = get_stem_set_row(conn, stem_set_id)
    if row is None:
        raise NeuralAudioError(
            "stem_set_not_found",
            "Neural audio stem set not found",
            http_status=404,
            details={"stem_set_id": stem_set_id},
        )
    conn.execute(
        "UPDATE neural_audio_stem_sets SET attempt_count = attempt_count + 1 WHERE id = ?",
        (stem_set_id,),
    )
    updated = get_stem_set_row(conn, stem_set_id)
    return int((updated or row).get("attempt_count") or 0)


def requeue_failed_stems(conn: sqlite3.Connection, stem_set_id: str) -> None:
    conn.execute(
        """
        UPDATE neural_audio_stems
        SET status = 'queued'
        WHERE stem_set_id = ? AND status = 'failed'
        """,
        (stem_set_id,),
    )


def update_stem_status(
    conn: sqlite3.Connection,
    stem_id: str,
    *,
    status: str,
    error_code: str | None = None,
    error_message: str | None = None,
    audio_relpath: str | None = None,
    content_type: str | None = None,
    byte_size: int | None = None,
    sha256_prefix: str | None = None,
    model_version: str | None = None,
    sample_rate: int | None = None,
) -> dict[str, Any]:
    row = get_stem_row(conn, stem_id)
    if row is None:
        raise NeuralAudioError(
            "stem_not_found",
            "Neural audio stem not found",
            http_status=404,
            details={"stem_id": stem_id},
        )
    now = _utc_now_iso()
    started = now if status == "running" and not row.get("started_at") else None
    completed = (
        now if status in {"complete", "failed"} and not row.get("completed_at") else None
    )
    conn.execute(
        """
        UPDATE neural_audio_stems SET
            status = ?,
            error_code = COALESCE(?, error_code),
            error_message = COALESCE(?, error_message),
            audio_relpath = COALESCE(?, audio_relpath),
            content_type = COALESCE(?, content_type),
            byte_size = COALESCE(?, byte_size),
            sha256_prefix = COALESCE(?, sha256_prefix),
            model_version = COALESCE(?, model_version),
            sample_rate = COALESCE(?, sample_rate),
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
            model_version,
            sample_rate,
            started,
            completed,
            stem_id,
        ),
    )
    logger.info(
        "Neural stem status transition",
        extra={
            "stem_id": stem_id,
            "stem_set_id": row.get("stem_set_id"),
            "stem_role": row.get("stem_role"),
            "status": status,
            "error_code": error_code,
            "byte_size": byte_size,
            "sha256_prefix": sha256_prefix,
        },
    )
    return get_stem_row(conn, stem_id)  # type: ignore[return-value]


def get_stem_set_row(conn: sqlite3.Connection, stem_set_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM neural_audio_stem_sets WHERE id = ?",
        (stem_set_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def get_stem_row(conn: sqlite3.Connection, stem_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM neural_audio_stems WHERE id = ?",
        (stem_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def list_stem_rows_for_set(
    conn: sqlite3.Connection,
    stem_set_id: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM neural_audio_stems
        WHERE stem_set_id = ?
        ORDER BY created_at ASC
        """,
        (stem_set_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def list_stem_set_rows(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM neural_audio_stem_sets
        WHERE project_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (project_id, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def delete_stem_set(
    conn: sqlite3.Connection,
    settings: NeuralAudioSettings,
    stem_set_id: str,
) -> None:
    row = get_stem_set_row(conn, stem_set_id)
    if row is None:
        raise NeuralAudioError(
            "stem_set_not_found",
            "Neural audio stem set not found",
            http_status=404,
            details={"stem_set_id": stem_set_id},
        )
    delete_stem_set_files(settings, row.get("project_id"), stem_set_id)
    conn.execute("DELETE FROM neural_audio_stems WHERE stem_set_id = ?", (stem_set_id,))
    conn.execute("DELETE FROM neural_audio_stem_sets WHERE id = ?", (stem_set_id,))
    logger.info(
        "Neural stem set deleted",
        extra={"stem_set_id": stem_set_id, "project_id": row.get("project_id")},
    )


def parse_stem_track_ids(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(item) for item in raw]
    if not raw:
        return []
    try:
        parsed = json.loads(str(raw))
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    except json.JSONDecodeError:
        return []
    return []


# Back-compat alias used by orchestration.
_parse_track_ids = parse_stem_track_ids


def _parse_warnings(raw: Any) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(str(raw))
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    except json.JSONDecodeError:
        return []
    return []


def stem_row_to_response(row: Mapping[str, Any]) -> NeuralAudioStemResponse:
    fidelity = str(row["fidelity_class"])
    bar_range = None
    if row.get("bar_start") is not None and row.get("bar_end") is not None:
        bar_range = NeuralAudioBarRange(
            start_bar=int(row["bar_start"]),
            end_bar=int(row["bar_end"]),
        )
    return NeuralAudioStemResponse(
        id=str(row["id"]),
        stem_set_id=str(row["stem_set_id"]),
        project_id=row.get("project_id"),
        stem_role=row["stem_role"],  # type: ignore[arg-type]
        source_track_ids=_parse_track_ids(row.get("source_track_ids_json")),
        source_revision_id=row.get("source_revision_id"),
        source_fingerprint=str(row["source_fingerprint"]),
        status=row["status"],  # type: ignore[arg-type]
        model_id=str(row["model_id"]),
        model_version=row.get("model_version"),
        adapter_kind=row.get("adapter_kind"),  # type: ignore[arg-type]
        fidelity_class=fidelity,  # type: ignore[arg-type]
        fidelity_label=NEURAL_AUDIO_FIDELITY_LABELS.get(fidelity, fidelity),
        capability_used=row["capability_used"],  # type: ignore[arg-type]
        sync_class=row["sync_class"],  # type: ignore[arg-type]
        engine=row.get("engine") or "neural",  # type: ignore[arg-type]
        instructions=str(row.get("instructions") or ""),
        instruction_chars=int(row.get("instruction_chars") or 0),
        genre=row.get("genre"),
        mood=row.get("mood"),
        tempo_bpm=row.get("tempo_bpm"),
        seed=row.get("seed"),
        sample_rate=row.get("sample_rate"),
        origin_tick=int(row.get("origin_tick") or 0),
        duration_ticks=int(row.get("duration_ticks") or 0),
        bar_range=bar_range,
        supersedes_stem_id=row.get("supersedes_stem_id"),
        error_code=row.get("error_code"),
        error_message=row.get("error_message"),
        audio_relpath=row.get("audio_relpath"),
        content_type=row.get("content_type"),
        byte_size=row.get("byte_size"),
        sha256_prefix=row.get("sha256_prefix"),
        created_at=str(row["created_at"]),
        started_at=row.get("started_at"),
        completed_at=row.get("completed_at"),
        adapter_warnings=_parse_warnings(row.get("adapter_warnings_json")),
        mutates_composition=False,
    )


def stem_set_row_to_response(
    set_row: Mapping[str, Any],
    stem_rows: Sequence[Mapping[str, Any]],
) -> NeuralAudioStemSetResponse:
    sync = NeuralAudioTimelineSync(
        origin_tick=int(set_row.get("origin_tick") or 0),
        duration_ticks=int(set_row.get("duration_ticks") or 0),
        tempo_bpm=set_row.get("tempo_bpm"),
        sample_rate=int(set_row.get("sample_rate") or 22050),
        source_fingerprint=str(set_row["source_fingerprint"]),
    )
    return NeuralAudioStemSetResponse(
        id=str(set_row["id"]),
        project_id=set_row.get("project_id"),
        source_revision_id=set_row.get("source_revision_id"),
        source_fingerprint=str(set_row["source_fingerprint"]),
        status=set_row["status"],  # type: ignore[arg-type]
        model_id=str(set_row["model_id"]),
        engine=set_row.get("engine") or "neural",  # type: ignore[arg-type]
        timeline_sync=sync,
        stems=[stem_row_to_response(row) for row in stem_rows],
        error_code=set_row.get("error_code"),
        error_message=set_row.get("error_message"),
        created_at=str(set_row["created_at"]),
        started_at=set_row.get("started_at"),
        completed_at=set_row.get("completed_at"),
        operation_run_id=set_row.get("operation_run_id"),
        attempt_count=int(set_row.get("attempt_count") or 0),
        mutates_composition=False,
    )


def open_stem_store_settings(
    env: Mapping[str, str] | None = None,
) -> NeuralAudioSettings:
    return load_settings_and_root(env)


def default_db_path(db_path: Path | str | None = None) -> Path:
    return Path(db_path) if db_path is not None else get_project_db_path()
