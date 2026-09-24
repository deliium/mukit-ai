"""Audio recovery pipeline orchestration (run_inline jobs + Bind).

Separation → scaffolding → per-stem/combined transcription inside a job workdir.
Durable source audio + result overlay are written only via Bind with project_id.
Never mutates composition.v2; never writes DATASET_ROOT.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Mapping

from app.audio_format_policy import sniff_audio_format
from app.audio_recovery_schemas import (
    AudioRecoveryBindRequestV1,
    AudioRecoveryBindResponseV1,
    AudioRecoveryError,
    AudioRecoveryJobV1,
    AudioRecoveryOverlayEntry,
    AudioRecoveryPreviewEngine,
    AudioRecoveryPreviewSummary,
    AudioRecoveryPreviewV1,
    AudioRecoveryResultV1,
    AudioRecoveryStemV1,
)
from app.audio_recovery_settings import AudioRecoverySettings, load_audio_recovery_settings
from app.db.connection import get_connection, get_project_db_path
from app.services import audio_recovery_store as store
from app.services.audio_recovery.scaffolding import estimate_scaffolding
from app.services.audio_recovery.separation import run_separation
from app.services.audio_recovery.transcribe import transcribe_stems_or_combined


logger = logging.getLogger(__name__)

SHA256_PREFIX_LEN = 16


def _preview_fingerprint(preview: AudioRecoveryPreviewV1) -> str:
    payload = preview.model_dump(mode="json", exclude={"preview_fingerprint"})
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


def row_to_job_response(row: Mapping[str, Any]) -> AudioRecoveryJobV1:
    preview = None
    if row.get("preview_json"):
        try:
            preview = AudioRecoveryPreviewV1.model_validate_json(row["preview_json"])
        except Exception:  # noqa: BLE001
            logger.warning(
                "Failed to parse stored recovery preview JSON",
                extra={"job_id": row.get("id")},
            )
    return AudioRecoveryJobV1(
        id=str(row["id"]),
        project_id=row.get("project_id"),
        status=row["status"],  # type: ignore[arg-type]
        engine_id=str(row["engine_id"]),
        separation_engine_id=row.get("separation_engine_id"),
        fake=bool(row.get("fake")),
        preview_fingerprint=row.get("preview_fingerprint"),
        preview=preview,
        error_code=row.get("error_code"),
        error_message=row.get("error_message"),
        source_sha256_prefix=row.get("source_sha256_prefix"),
        source_byte_size=row.get("source_byte_size"),
        bound=bool(row.get("bound")),
        source_audio_asset_id=row.get("source_audio_asset_id"),
        result_asset_id=row.get("result_asset_id"),
        created_at=str(row["created_at"]),
        started_at=row.get("started_at"),
        completed_at=row.get("completed_at"),
    )


def enqueue_audio_recovery_job(
    payload: bytes,
    *,
    display_filename: str | None = None,
    project_id: str | None = None,
    disable_separation: bool = False,
    db_path: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    run_inline: bool = True,
) -> AudioRecoveryJobV1:
    """Create a job, optionally run the pipeline inline (neural-audio pattern)."""
    settings = load_audio_recovery_settings(env)
    path = Path(db_path) if db_path is not None else get_project_db_path()
    basename = Path(display_filename or "upload.wav").name

    if not payload:
        raise AudioRecoveryError(
            "audio_empty_upload",
            "Audio upload is empty.",
            http_status=422,
        )
    if len(payload) > settings.max_upload_bytes:
        raise AudioRecoveryError(
            "audio_payload_too_large",
            "Upload exceeded configured audio byte limit.",
            http_status=413,
            details={"limit": settings.max_upload_bytes, "byte_size": len(payload)},
        )
    fmt = sniff_audio_format(payload)
    if fmt is None:
        raise AudioRecoveryError(
            "audio_format_unsupported",
            "Content signature is not an accepted audio format.",
            http_status=422,
        )

    engine_id = "local:estimators"
    if settings.fake_mode or settings.engine == "fake:audio-recovery":
        engine_id = "fake:audio-recovery"
    elif settings.engine != "auto":
        engine_id = settings.engine

    sep_id: str | None = None if disable_separation else settings.separation_engine
    if disable_separation or settings.separation_engine == "off":
        sep_id = "off"
    elif settings.fake_mode and settings.separation_engine in {"auto", "fake:stems"}:
        sep_id = "fake:stems"

    job_id = store.allocate_job_id()
    work_relpath = store.job_work_relpath(job_id)

    with get_connection(path) as conn:
        store.assert_job_quota(conn, settings, project_id)
        store.insert_queued_job(
            conn,
            job_id=job_id,
            project_id=project_id,
            engine_id=engine_id,
            separation_engine_id=sep_id,
            fake=bool(settings.fake_mode or engine_id.startswith("fake:")),
            work_relpath=work_relpath,
        )

    logger.info(
        "Audio recovery job enqueued",
        extra={
            "job_id": job_id,
            "project_id": project_id,
            "engine_id": engine_id,
            "separation_engine_id": sep_id,
            "byte_size": len(payload),
            "format": fmt,
            "basename": basename,
            "run_inline": run_inline,
        },
    )

    # Persist source into workdir before run.
    store.ensure_job_workdir(settings, job_id)
    source_relpath, sha_prefix, byte_size = store.write_job_bytes(
        settings,
        job_id=job_id,
        relative_name="source.wav" if fmt == "wav" else f"source.{fmt}",
        payload=payload,
    )
    with get_connection(path) as conn:
        store.update_job_status(
            conn,
            job_id,
            status="queued",
            source_relpath=source_relpath,
            source_sha256_prefix=sha_prefix,
            source_byte_size=byte_size,
        )

    if run_inline:
        return run_audio_recovery_job(
            job_id,
            settings=settings,
            db_path=path,
            disable_separation=disable_separation,
        )
    with get_connection(path) as conn:
        row = store.get_job_row(conn, job_id)
    return row_to_job_response(row)  # type: ignore[arg-type]


def run_audio_recovery_job(
    job_id: str,
    *,
    settings: AudioRecoverySettings | None = None,
    db_path: Path | str | None = None,
    disable_separation: bool = False,
) -> AudioRecoveryJobV1:
    resolved = settings if settings is not None else load_audio_recovery_settings()
    path = Path(db_path) if db_path is not None else get_project_db_path()
    started = time.perf_counter()

    with get_connection(path) as conn:
        row = store.get_job_row(conn, job_id)
        if row is None:
            raise AudioRecoveryError(
                "audio_recovery_job_not_found",
                "Audio recovery job not found",
                http_status=404,
                details={"job_id": job_id},
            )
        store.update_job_status(conn, job_id, status="running")
        source_relpath = row.get("source_relpath")

    if not source_relpath:
        return _fail_job(
            path,
            job_id,
            code="audio_recovery_internal_error",
            message="Job is missing source audio",
        )

    try:
        source_path = store.absolute_under_asset_root(resolved, source_relpath)
    except AudioRecoveryError as exc:
        return _fail_job(path, job_id, code=exc.code, message=exc.message)

    work_dir = store.job_work_dir(resolved, job_id)
    stems_dir = work_dir / "stems"
    stems_dir.mkdir(parents=True, exist_ok=True)

    try:
        separation = run_separation(
            source_path,
            work_dir=stems_dir,
            settings=resolved,
            user_disable=disable_separation,
        )
        scaffolding_est = estimate_scaffolding(
            source_path,
            ticks_per_quarter=resolved.default_target_ppq,
            default_tempo_bpm=resolved.default_tempo_bpm,
            fake_mode=resolved.fake_mode or str(row.get("engine_id", "")).startswith("fake:"),
        )
        tempo_bpm = scaffolding_est.scaffolding.tempo_bpm
        ticks_per_quarter = scaffolding_est.scaffolding.beat_grid.ticks_per_quarter

        combined_degraded = separation.status in {"unavailable", "skipped_mono", "off"}
        stem_results, notes, tx_issues = transcribe_stems_or_combined(
            source_path=source_path,
            stems=separation.stems,
            settings=resolved,
            tempo_bpm=tempo_bpm,
            ticks_per_quarter=ticks_per_quarter,
            combined_degraded=combined_degraded and separation.status == "unavailable",
        )

        stem_docs: list[AudioRecoveryStemV1] = []
        for result in stem_results:
            stem_docs.append(
                AudioRecoveryStemV1(
                    stem=result.stem,
                    engine_id=result.engine_id,
                    notes=result.notes,
                    issues=result.issues,
                )
            )

        all_issues = list(separation.issues) + list(scaffolding_est.issues) + list(tx_issues)
        threshold = resolved.confidence_include_threshold
        low_count = sum(1 for n in notes if n.confidence < threshold)
        excluded = low_count  # default gate excludes them on Apply

        sep_status = separation.status
        preview_engine = AudioRecoveryPreviewEngine(
            id=str(row["engine_id"]),
            separation_engine_id=separation.engine_id if separation.engine_id != "off" else None,
            version="1",
            fake=bool(row.get("fake")),
        )
        preview = AudioRecoveryPreviewV1(
            preview_fingerprint="pending_fingerprint",
            stems=stem_docs,
            notes=notes,
            scaffolding=scaffolding_est.scaffolding,
            issues=all_issues,
            summary=AudioRecoveryPreviewSummary(
                note_count=len(notes),
                low_confidence_count=low_count,
                excluded_low_confidence_count=excluded,
                include_threshold=threshold,
                stem_count=len(stem_docs),
                separation_status=sep_status,  # type: ignore[arg-type]
            ),
            engine=preview_engine,
        )
        fingerprint = _preview_fingerprint(preview)
        preview = preview.model_copy(update={"preview_fingerprint": fingerprint})

        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "Audio recovery pipeline complete",
            extra={
                "job_id": job_id,
                "duration_ms": duration_ms,
                "stem_count": len(stem_docs),
                "note_count": len(notes),
                "separation_status": sep_status,
                "low_confidence_count": low_count,
            },
        )

        with get_connection(path) as conn:
            store.update_job_status(
                conn,
                job_id,
                status="complete",
                preview_fingerprint=fingerprint,
                preview_json=preview.model_dump_json(),
            )
            conn.execute(
                "UPDATE audio_recovery_jobs SET separation_engine_id = ? WHERE id = ?",
                (
                    separation.engine_id
                    if separation.engine_id not in {"off", "none", "policy:mono"}
                    else row.get("separation_engine_id"),
                    job_id,
                ),
            )
            final = store.get_job_row(conn, job_id)
        return row_to_job_response(final)  # type: ignore[arg-type]

    except AudioRecoveryError as exc:
        logger.error(
            "Audio recovery job domain failure",
            extra={"job_id": job_id, "error_code": exc.code},
        )
        return _fail_job(path, job_id, code=exc.code, message=exc.message)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Audio recovery job failed",
            extra={"job_id": job_id, "error_type": type(exc).__name__},
        )
        return _fail_job(
            path,
            job_id,
            code="audio_recovery_internal_error",
            message="Unexpected audio recovery failure",
        )


def _fail_job(
    db_path: Path,
    job_id: str,
    *,
    code: str,
    message: str,
) -> AudioRecoveryJobV1:
    with get_connection(db_path) as conn:
        store.update_job_status(
            conn,
            job_id,
            status="failed",
            error_code=code,
            error_message=message[:400],
        )
        row = store.get_job_row(conn, job_id)
    return row_to_job_response(row)  # type: ignore[arg-type]


def get_audio_recovery_job(
    job_id: str,
    *,
    db_path: Path | str | None = None,
) -> AudioRecoveryJobV1:
    path = Path(db_path) if db_path is not None else get_project_db_path()
    with get_connection(path) as conn:
        row = store.get_job_row(conn, job_id)
    if row is None:
        raise AudioRecoveryError(
            "audio_recovery_job_not_found",
            "Audio recovery job not found",
            http_status=404,
            details={"job_id": job_id},
        )
    return row_to_job_response(row)


def delete_audio_recovery_job(
    job_id: str,
    *,
    db_path: Path | str | None = None,
    settings: AudioRecoverySettings | None = None,
) -> None:
    path = Path(db_path) if db_path is not None else get_project_db_path()
    resolved = settings if settings is not None else load_audio_recovery_settings()
    with get_connection(path) as conn:
        row = store.get_job_row(conn, job_id)
        if row is None:
            raise AudioRecoveryError(
                "audio_recovery_job_not_found",
                "Audio recovery job not found",
                http_status=404,
                details={"job_id": job_id},
            )
        if row.get("bound"):
            # Bound jobs keep durable assets; only drop ephemeral workdir.
            store.delete_job_workdir(resolved, job_id)
            logger.info(
                "Bound recovery job workdir GC only",
                extra={"job_id": job_id},
            )
            return
        store.delete_job_row(conn, job_id)
    store.delete_job_workdir(resolved, job_id)
    logger.info("Audio recovery job deleted", extra={"job_id": job_id})


def bind_audio_recovery_job(
    job_id: str,
    body: AudioRecoveryBindRequestV1,
    *,
    db_path: Path | str | None = None,
    settings: AudioRecoverySettings | None = None,
) -> AudioRecoveryBindResponseV1:
    """Persist source audio + result overlay after client V2 Apply."""
    if not body.project_id:
        raise AudioRecoveryError(
            "audio_recovery_project_required",
            "Bind requires a project_id.",
            http_status=422,
        )
    path = Path(db_path) if db_path is not None else get_project_db_path()
    resolved = settings if settings is not None else load_audio_recovery_settings()

    with get_connection(path) as conn:
        row = store.get_job_row(conn, job_id)
        if row is None:
            raise AudioRecoveryError(
                "audio_recovery_job_not_found",
                "Audio recovery job not found",
                http_status=404,
                details={"job_id": job_id},
            )
        if row["status"] != "complete":
            raise AudioRecoveryError(
                "audio_recovery_job_not_ready",
                "Bind requires a complete recovery job.",
                http_status=409,
                details={"status": row["status"]},
            )
        if row.get("preview_fingerprint") != body.preview_fingerprint:
            raise AudioRecoveryError(
                "audio_recovery_bind_fingerprint_mismatch",
                "Preview fingerprint does not match the job.",
                http_status=409,
                details={"job_id": job_id},
            )
        if row.get("bound"):
            logger.info(
                "Audio recovery bind idempotent hit",
                extra={"job_id": job_id, "project_id": body.project_id},
            )
            return AudioRecoveryBindResponseV1(
                job_id=job_id,
                project_id=body.project_id,
                source_audio_asset_id=str(row["source_audio_asset_id"]),
                result_asset_id=str(row["result_asset_id"]),
                overlay_entry_count=len(body.event_map),
            )

        store.assert_asset_quota(conn, resolved, body.project_id)

        preview = AudioRecoveryPreviewV1.model_validate_json(row["preview_json"])
        note_by_prov = {n.provisional_id: n for n in preview.notes}
        overlay: list[AudioRecoveryOverlayEntry] = []
        for entry in body.event_map:
            note = note_by_prov.get(entry.provisional_id)
            if note is None:
                continue
            overlay.append(
                AudioRecoveryOverlayEntry(
                    event_id=entry.event_id,
                    track_id=entry.track_id,
                    confidence=note.confidence,
                    stem=note.stem,
                    provisional_id=note.provisional_id,
                    status="recovered",
                )
            )

        source_relpath = row.get("source_relpath")
        if not source_relpath:
            raise AudioRecoveryError(
                "audio_recovery_internal_error",
                "Job is missing source audio for bind",
                http_status=500,
            )
        source_path = store.absolute_under_asset_root(resolved, source_relpath)
        source_bytes = source_path.read_bytes()
        source_write = store.write_durable_asset_bytes(
            resolved,
            project_id=body.project_id,
            job_id=job_id,
            kind="source_audio",
            payload=source_bytes,
            content_type="audio/wav",
            ext="wav",
        )
        store.insert_asset_row(
            conn,
            asset_id=source_write.asset_id,
            project_id=body.project_id,
            job_id=job_id,
            kind=source_write.kind,
            content_type=source_write.content_type,
            byte_size=source_write.byte_size,
            sha256_prefix=source_write.sha256_prefix,
            relpath=source_write.relpath,
        )

        result = AudioRecoveryResultV1(
            job_id=job_id,
            project_id=body.project_id,
            preview_fingerprint=body.preview_fingerprint,
            source_audio_asset_id=source_write.asset_id,
            scaffolding=preview.scaffolding,
            overlay=overlay,
            issues=preview.issues,
            engine=preview.engine,
        )
        result_bytes = result.model_dump_json().encode("utf-8")
        result_write = store.write_durable_asset_bytes(
            resolved,
            project_id=body.project_id,
            job_id=job_id,
            kind="result_json",
            payload=result_bytes,
            content_type="application/json",
            ext="json",
        )
        store.insert_asset_row(
            conn,
            asset_id=result_write.asset_id,
            project_id=body.project_id,
            job_id=job_id,
            kind=result_write.kind,
            content_type=result_write.content_type,
            byte_size=result_write.byte_size,
            sha256_prefix=result_write.sha256_prefix,
            relpath=result_write.relpath,
        )
        store.update_job_status(
            conn,
            job_id,
            status="complete",
            bound=True,
            project_id=body.project_id,
            source_audio_asset_id=source_write.asset_id,
            result_asset_id=result_write.asset_id,
        )

    # Ephemeral workdir can be GC'd after successful bind.
    store.delete_job_workdir(resolved, job_id)
    logger.info(
        "Audio recovery bind complete",
        extra={
            "job_id": job_id,
            "project_id": body.project_id,
            "source_audio_asset_id": source_write.asset_id,
            "result_asset_id": result_write.asset_id,
            "overlay_entry_count": len(overlay),
        },
    )
    return AudioRecoveryBindResponseV1(
        job_id=job_id,
        project_id=body.project_id,
        source_audio_asset_id=source_write.asset_id,
        result_asset_id=result_write.asset_id,
        overlay_entry_count=len(overlay),
    )


def resolve_asset_file_path(
    asset_id: str,
    *,
    db_path: Path | str | None = None,
    settings: AudioRecoverySettings | None = None,
) -> tuple[Path, str, str]:
    """Return (absolute_path, content_type, basename) for a durable asset."""
    path = Path(db_path) if db_path is not None else get_project_db_path()
    resolved = settings if settings is not None else load_audio_recovery_settings()
    with get_connection(path) as conn:
        row = store.get_asset_row(conn, asset_id)
    if row is None:
        raise AudioRecoveryError(
            "audio_recovery_asset_not_found",
            "Recovery asset was not found.",
            http_status=404,
            details={"asset_id": asset_id},
        )
    abs_path = store.absolute_under_asset_root(resolved, row["relpath"])
    if not abs_path.is_file():
        raise AudioRecoveryError(
            "audio_recovery_asset_not_found",
            "Recovery asset file missing on disk.",
            http_status=404,
            details={"asset_id": asset_id},
        )
    return abs_path, str(row["content_type"]), Path(row["relpath"]).name
