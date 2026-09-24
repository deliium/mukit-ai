"""Filesystem + SQLite metadata store for durable ``mix.analysis.v1`` reports.

Report JSON lives under ``MIX_ANALYSIS_ROOT`` as ``{project_id}/{report_id}.json``.
Never stores PCM. Never writes composition snapshots or ``DATASET_ROOT``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from app.db.connection import get_connection, get_project_db_path
from app.mix_analysis_schemas import (
    MIX_ANALYSIS_INTERNAL_ERROR,
    MIX_ANALYSIS_NOT_FOUND,
    MIX_ANALYSIS_QUOTA_EXCEEDED,
    MixAnalysisError,
    MixAnalysisReport,
    MixAnalysisReportMeta,
)
from app.mix_analysis_settings import MixAnalysisSettings, load_mix_analysis_settings

logger = logging.getLogger(__name__)

SHA256_PREFIX_LEN = 16


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def allocate_report_id() -> str:
    return str(uuid.uuid4())


def load_settings_and_root(
    env: Mapping[str, str] | None = None,
) -> MixAnalysisSettings:
    return load_mix_analysis_settings(env)


def report_relpath(project_id: str, report_id: str) -> str:
    return f"{project_id}/{report_id}.json"


def absolute_report_path(settings: MixAnalysisSettings, relpath: str) -> Path:
    root = settings.report_root.resolve()
    path = (settings.report_root / relpath).resolve()
    if root not in path.parents and path != root:
        logger.error(
            "Mix analysis path confinement rejected escape",
            extra={"relpath_basename": Path(relpath).name},
        )
        raise MixAnalysisError(
            "Path escapes mix analysis report root",
            code=MIX_ANALYSIS_INTERNAL_ERROR,
            http_status=500,
        )
    return path


def count_project_reports(conn: sqlite3.Connection, project_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM mix_analysis_reports WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    return int(row["n"] if row is not None else 0)


def assert_persist_quota(
    conn: sqlite3.Connection,
    settings: MixAnalysisSettings,
    project_id: str,
) -> None:
    current = count_project_reports(conn, project_id)
    if current >= settings.max_reports_per_project:
        raise MixAnalysisError(
            "Per-project mix analysis report quota exceeded",
            code=MIX_ANALYSIS_QUOTA_EXCEEDED,
            http_status=422,
            details={
                "limit": settings.max_reports_per_project,
                "current": current,
            },
        )


def insert_report(
    conn: sqlite3.Connection,
    settings: MixAnalysisSettings,
    report: MixAnalysisReport,
    *,
    project_id: str,
) -> MixAnalysisReportMeta:
    """Persist report JSON + SQLite row. Returns metadata."""
    assert_persist_quota(conn, settings, project_id)
    report_id = report.report_id or allocate_report_id()
    created_at = report.created_at or _utc_now_iso()
    body = report.model_copy(
        update={
            "report_id": report_id,
            "project_id": project_id,
            "created_at": created_at,
        }
    )
    payload = body.model_dump(mode="json")
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    sha_prefix = digest[:SHA256_PREFIX_LEN]
    relpath = report_relpath(project_id, report_id)
    abs_path = absolute_report_path(settings, relpath)
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        abs_path.write_bytes(raw)
    except OSError as exc:
        logger.error(
            "Failed to write mix analysis report file",
            extra={
                "report_id": report_id,
                "project_id": project_id,
                "error_type": type(exc).__name__,
                "byte_size": len(raw),
            },
        )
        raise MixAnalysisError(
            "Failed to persist mix analysis report",
            code=MIX_ANALYSIS_INTERNAL_ERROR,
            http_status=500,
        ) from exc

    conn.execute(
        """
        INSERT INTO mix_analysis_reports (
            id, project_id, stem_set_id, mix_render_id, dsp_backend,
            source_stem_set_fingerprint, source_composition_fingerprint,
            report_relpath, byte_size, sha256_prefix,
            observation_count, measurement_count, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            report_id,
            project_id,
            body.stem_set_id,
            body.mix_render_id,
            body.dsp_backend,
            body.source_stem_set_fingerprint,
            body.source_composition_fingerprint,
            relpath,
            len(raw),
            sha_prefix,
            len(body.observations),
            len(body.measurements),
            created_at,
        ),
    )
    logger.info(
        "Mix analysis report persisted",
        extra={
            "report_id": report_id,
            "project_id": project_id,
            "stem_set_id": body.stem_set_id,
            "byte_size": len(raw),
            "sha256_prefix": sha_prefix,
            "dsp_backend": body.dsp_backend,
            "observation_count": len(body.observations),
            "measurement_count": len(body.measurements),
        },
    )
    return MixAnalysisReportMeta(
        report_id=report_id,
        project_id=project_id,
        stem_set_id=body.stem_set_id,
        mix_render_id=body.mix_render_id,
        dsp_backend=body.dsp_backend,
        source_stem_set_fingerprint=body.source_stem_set_fingerprint,
        source_composition_fingerprint=body.source_composition_fingerprint,
        byte_size=len(raw),
        sha256_prefix=sha_prefix,
        created_at=created_at,
        observation_count=len(body.observations),
        measurement_count=len(body.measurements),
    )


def row_to_meta(row: Mapping[str, Any]) -> MixAnalysisReportMeta:
    return MixAnalysisReportMeta(
        report_id=str(row["id"]),
        project_id=str(row["project_id"]),
        stem_set_id=str(row["stem_set_id"]),
        mix_render_id=row["mix_render_id"],
        dsp_backend=row["dsp_backend"],
        source_stem_set_fingerprint=str(row["source_stem_set_fingerprint"]),
        source_composition_fingerprint=row["source_composition_fingerprint"],
        byte_size=int(row["byte_size"] or 0),
        sha256_prefix=str(row["sha256_prefix"]),
        created_at=str(row["created_at"]),
        observation_count=int(row["observation_count"] or 0),
        measurement_count=int(row["measurement_count"] or 0),
    )


def _row_to_meta(row: Mapping[str, Any]) -> MixAnalysisReportMeta:
    return row_to_meta(row)


def get_report_row(conn: sqlite3.Connection, report_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM mix_analysis_reports WHERE id = ?",
        (report_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def list_project_reports(
    conn: sqlite3.Connection,
    project_id: str,
    *,
    limit: int = 50,
) -> list[MixAnalysisReportMeta]:
    rows = conn.execute(
        """
        SELECT * FROM mix_analysis_reports
        WHERE project_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (project_id, max(1, min(limit, 200))),
    ).fetchall()
    return [_row_to_meta(dict(row)) for row in rows]


def load_report_body(
    settings: MixAnalysisSettings,
    row: Mapping[str, Any],
) -> MixAnalysisReport:
    relpath = str(row["report_relpath"])
    abs_path = absolute_report_path(settings, relpath)
    try:
        raw = abs_path.read_bytes()
    except OSError as exc:
        logger.error(
            "Failed to read mix analysis report file",
            extra={"report_id": row.get("id"), "error_type": type(exc).__name__},
        )
        raise MixAnalysisError(
            "Mix analysis report file missing",
            code=MIX_ANALYSIS_NOT_FOUND,
            http_status=404,
        ) from exc
    try:
        data = json.loads(raw.decode("utf-8"))
        return MixAnalysisReport.model_validate(data)
    except Exception as exc:
        logger.error(
            "Corrupt mix analysis report JSON",
            extra={"report_id": row.get("id"), "error_type": type(exc).__name__},
        )
        raise MixAnalysisError(
            "Mix analysis report is corrupt",
            code=MIX_ANALYSIS_INTERNAL_ERROR,
            http_status=500,
        ) from exc


def delete_report(
    conn: sqlite3.Connection,
    settings: MixAnalysisSettings,
    report_id: str,
) -> None:
    row = get_report_row(conn, report_id)
    if row is None:
        raise MixAnalysisError(
            "Mix analysis report not found",
            code=MIX_ANALYSIS_NOT_FOUND,
            http_status=404,
        )
    relpath = str(row["report_relpath"])
    abs_path = absolute_report_path(settings, relpath)
    conn.execute("DELETE FROM mix_analysis_reports WHERE id = ?", (report_id,))
    try:
        if abs_path.is_file():
            abs_path.unlink()
    except OSError as exc:
        logger.warning(
            "Failed to delete mix analysis report file",
            extra={"report_id": report_id, "error_type": type(exc).__name__},
        )
    logger.info(
        "Mix analysis report deleted",
        extra={"report_id": report_id, "project_id": row.get("project_id")},
    )


def cleanup_project_mix_analysis(
    project_id: str,
    *,
    db_path: Path | str | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, int]:
    """Best-effort GC of mix analysis reports for a deleted project."""
    settings = load_settings_and_root(env)
    path = Path(db_path) if db_path is not None else get_project_db_path()
    deleted_rows = 0
    try:
        with get_connection(path) as conn:
            rows = conn.execute(
                "SELECT id, report_relpath FROM mix_analysis_reports WHERE project_id = ?",
                (project_id,),
            ).fetchall()
            for row in rows:
                relpath = str(row["report_relpath"])
                try:
                    abs_path = absolute_report_path(settings, relpath)
                    if abs_path.is_file():
                        abs_path.unlink()
                except Exception as file_exc:  # noqa: BLE001
                    logger.warning(
                        "Mix analysis file cleanup failed",
                        extra={
                            "project_id": project_id,
                            "report_id": row["id"],
                            "error_type": type(file_exc).__name__,
                        },
                    )
            cur = conn.execute(
                "DELETE FROM mix_analysis_reports WHERE project_id = ?",
                (project_id,),
            )
            deleted_rows = int(cur.rowcount or 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Mix analysis DB cleanup failed",
            extra={"project_id": project_id, "error_type": type(exc).__name__},
        )

    project_dir = (settings.report_root / project_id).resolve()
    root = settings.report_root.resolve()
    removed_dirs = 0
    if root in project_dir.parents or project_dir == root:
        if project_dir.is_dir():
            try:
                shutil.rmtree(project_dir)
                removed_dirs = 1
            except OSError as exc:
                logger.warning(
                    "Mix analysis project dir cleanup failed",
                    extra={
                        "project_id": project_id,
                        "error_type": type(exc).__name__,
                    },
                )
    logger.info(
        "Mix analysis project cleanup complete",
        extra={
            "project_id": project_id,
            "deleted_rows": deleted_rows,
            "removed_dirs": removed_dirs,
        },
    )
    return {"deleted_rows": deleted_rows, "removed_dirs": removed_dirs}
