"""Filesystem + SQLite store for mix-plan previews and applied revisions.

Previews live under ``{project_id}/previews/{preview_id}/``.
Applied mixes live under ``{project_id}/{revision_id}/``.
Never writes neural stem paths or ``DATASET_ROOT``.
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
from app.mix_plan_schemas import (
    MIX_PLAN_INTERNAL_ERROR,
    MIX_PLAN_NOT_FOUND,
    MIX_PLAN_QUOTA_EXCEEDED,
    MixPlanError,
    MixPlanRevisionMeta,
    MixPlanV1,
)
from app.mix_plan_settings import MixPlanSettings, load_mix_plan_settings

logger = logging.getLogger(__name__)

SHA256_PREFIX_LEN = 16


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def allocate_id() -> str:
    return str(uuid.uuid4())


def load_settings_and_root(env: Mapping[str, str] | None = None) -> MixPlanSettings:
    return load_mix_plan_settings(env)


def absolute_under_root(settings: MixPlanSettings, relpath: str) -> Path:
    root = settings.plan_root.resolve()
    path = (settings.plan_root / relpath).resolve()
    if root != path and root not in path.parents:
        logger.error(
            "Mix plan path confinement rejected escape",
            extra={"relpath_basename": Path(relpath).name},
        )
        raise MixPlanError(
            "Path escapes mix plan root",
            code=MIX_PLAN_INTERNAL_ERROR,
            http_status=500,
        )
    return path


def preview_dir_rel(project_id: str, preview_id: str) -> str:
    return f"{project_id}/previews/{preview_id}"


def revision_mix_rel(project_id: str, revision_id: str) -> str:
    return f"{project_id}/{revision_id}/mix.wav"


def revision_plan_rel(project_id: str, revision_id: str) -> str:
    return f"{project_id}/{revision_id}/plan.json"


def write_preview_bundle(
    settings: MixPlanSettings,
    *,
    project_id: str,
    preview_id: str,
    meta: Mapping[str, Any],
    dry_wav: bytes | None,
    processed_wav: bytes | None,
) -> None:
    rel = preview_dir_rel(project_id, preview_id)
    folder = absolute_under_root(settings, rel)
    folder.mkdir(parents=True, exist_ok=True)
    _write_json(folder / "meta.json", meta)
    if dry_wav is not None:
        (folder / "dry.wav").write_bytes(dry_wav)
    if processed_wav is not None:
        (folder / "processed.wav").write_bytes(processed_wav)
    logger.info(
        "Mix plan preview stored",
        extra={
            "preview_id": preview_id,
            "project_id": project_id,
            "byte_size": len(processed_wav or b""),
            "dry_bytes": len(dry_wav or b""),
        },
    )


def read_preview_meta(settings: MixPlanSettings, project_id: str, preview_id: str) -> dict[str, Any]:
    folder = absolute_under_root(settings, preview_dir_rel(project_id, preview_id))
    meta_path = folder / "meta.json"
    if not meta_path.is_file():
        raise MixPlanError(
            "Mix plan preview not found",
            code=MIX_PLAN_NOT_FOUND,
            details={"preview_id": preview_id},
        )
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.error(
            "Mix plan preview meta unreadable",
            extra={"preview_id": preview_id, "error_type": type(exc).__name__},
        )
        raise MixPlanError(
            "Mix plan preview is unreadable",
            code=MIX_PLAN_INTERNAL_ERROR,
        ) from exc
    if not isinstance(data, dict):
        raise MixPlanError("Mix plan preview is unreadable", code=MIX_PLAN_INTERNAL_ERROR)
    return data


def preview_audio_path(
    settings: MixPlanSettings, project_id: str, preview_id: str, which: str
) -> Path:
    if which not in {"dry", "processed"}:
        raise MixPlanError(
            "Unknown preview audio selector",
            code=MIX_PLAN_NOT_FOUND,
            details={"which": which},
        )
    folder = absolute_under_root(settings, preview_dir_rel(project_id, preview_id))
    path = folder / f"{which}.wav"
    if not path.is_file():
        raise MixPlanError(
            "Mix plan preview audio not found",
            code=MIX_PLAN_NOT_FOUND,
            details={"preview_id": preview_id},
        )
    return path


def delete_preview_files(settings: MixPlanSettings, project_id: str, preview_id: str) -> None:
    folder = absolute_under_root(settings, preview_dir_rel(project_id, preview_id))
    if not folder.is_dir():
        raise MixPlanError(
            "Mix plan preview not found",
            code=MIX_PLAN_NOT_FOUND,
            details={"preview_id": preview_id},
        )
    shutil.rmtree(folder)
    logger.info(
        "Mix plan preview rejected",
        extra={"preview_id": preview_id, "project_id": project_id},
    )


def find_preview_project(settings: MixPlanSettings, preview_id: str) -> str | None:
    root = settings.plan_root
    if not root.is_dir():
        return None
    for child in root.iterdir():
        if not child.is_dir():
            continue
        meta = child / "previews" / preview_id / "meta.json"
        if meta.is_file():
            return child.name
    return None


def count_project_revisions(conn: sqlite3.Connection, project_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM mix_plan_revisions WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    return int(row["n"] if row is not None else 0)


def head_revision_id(conn: sqlite3.Connection, project_id: str, stem_set_id: str) -> str | None:
    row = conn.execute(
        """
        SELECT id FROM mix_plan_revisions
        WHERE project_id = ? AND stem_set_id = ? AND is_head = 1
        """,
        (project_id, stem_set_id),
    ).fetchone()
    return str(row["id"]) if row else None


def insert_revision(
    conn: sqlite3.Connection,
    settings: MixPlanSettings,
    *,
    project_id: str,
    stem_set_id: str,
    parent_revision_id: str | None,
    master_target: str,
    plan: MixPlanV1,
    mix_bytes: bytes,
    stem_pins_json: str,
    dsp_backend: str,
    fingerprint: str,
) -> MixPlanRevisionMeta:
    current = count_project_revisions(conn, project_id)
    if current >= settings.max_revisions_per_project:
        raise MixPlanError(
            "Per-project mix plan revision quota exceeded",
            code=MIX_PLAN_QUOTA_EXCEEDED,
            details={"limit": settings.max_revisions_per_project, "current": current},
        )
    revision_id = allocate_id()
    created_at = _utc_now_iso()
    plan_rel = revision_plan_rel(project_id, revision_id)
    mix_rel = revision_mix_rel(project_id, revision_id)
    plan_path = absolute_under_root(settings, plan_rel)
    mix_path = absolute_under_root(settings, mix_rel)
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(plan.model_dump(mode="json"), separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    digest = hashlib.sha256(mix_bytes).hexdigest()
    sha_prefix = digest[:SHA256_PREFIX_LEN]
    plan_path.write_text(raw.decode("utf-8"), encoding="utf-8")
    mix_path.write_bytes(mix_bytes)
    conn.execute(
        "UPDATE mix_plan_revisions SET is_head = 0 WHERE project_id = ? AND stem_set_id = ?",
        (project_id, stem_set_id),
    )
    conn.execute(
        """
        INSERT INTO mix_plan_revisions (
            id, project_id, stem_set_id, parent_revision_id, status, master_target,
            plan_relpath, mix_relpath, source_stem_set_fingerprint, stem_sha256_pins_json,
            dsp_backend, byte_size, sha256_prefix, created_at, is_head
        ) VALUES (?, ?, ?, ?, 'applied', ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            revision_id,
            project_id,
            stem_set_id,
            parent_revision_id,
            master_target,
            plan_rel,
            mix_rel,
            fingerprint,
            stem_pins_json,
            dsp_backend,
            len(mix_bytes),
            sha_prefix,
            created_at,
        ),
    )
    logger.info(
        "Mix plan revision persisted",
        extra={
            "revision_id": revision_id,
            "project_id": project_id,
            "stem_set_id": stem_set_id,
            "byte_size": len(mix_bytes),
            "sha256_prefix": sha_prefix,
            "master_target": master_target,
            "dsp_backend": dsp_backend,
        },
    )
    return MixPlanRevisionMeta(
        id=revision_id,
        project_id=project_id,
        stem_set_id=stem_set_id,
        parent_revision_id=parent_revision_id,
        master_target=master_target,  # type: ignore[arg-type]
        dsp_backend=dsp_backend,  # type: ignore[arg-type]
        byte_size=len(mix_bytes),
        sha256_prefix=sha_prefix,
        created_at=created_at,
        is_head=True,
        source_stem_set_fingerprint=fingerprint,
        plan_relpath=plan_rel,
        mix_relpath=mix_rel,
    )


def get_revision_row(conn: sqlite3.Connection, revision_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM mix_plan_revisions WHERE id = ?",
        (revision_id,),
    ).fetchone()
    return dict(row) if row else None


def list_revision_rows(
    conn: sqlite3.Connection, *, project_id: str, stem_set_id: str | None, limit: int = 50
) -> list[dict[str, Any]]:
    if stem_set_id:
        rows = conn.execute(
            """
            SELECT * FROM mix_plan_revisions
            WHERE project_id = ? AND stem_set_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (project_id, stem_set_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM mix_plan_revisions
            WHERE project_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (project_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def row_to_meta(row: Mapping[str, Any]) -> MixPlanRevisionMeta:
    return MixPlanRevisionMeta(
        id=str(row["id"]),
        project_id=str(row["project_id"]),
        stem_set_id=str(row["stem_set_id"]),
        parent_revision_id=row.get("parent_revision_id"),
        master_target=row["master_target"],
        dsp_backend=row["dsp_backend"],
        byte_size=int(row["byte_size"]),
        sha256_prefix=str(row["sha256_prefix"]),
        created_at=str(row["created_at"]),
        is_head=bool(row["is_head"]),
        source_stem_set_fingerprint=str(row["source_stem_set_fingerprint"]),
        plan_relpath=str(row["plan_relpath"]),
        mix_relpath=str(row["mix_relpath"]),
    )


def load_revision_plan(settings: MixPlanSettings, row: Mapping[str, Any]) -> MixPlanV1:
    path = absolute_under_root(settings, str(row["plan_relpath"]))
    if not path.is_file():
        raise MixPlanError("Mix plan revision file missing", code=MIX_PLAN_NOT_FOUND)
    return MixPlanV1.model_validate(json.loads(path.read_text(encoding="utf-8")))


def revision_audio_path(settings: MixPlanSettings, row: Mapping[str, Any]) -> Path:
    path = absolute_under_root(settings, str(row["mix_relpath"]))
    if not path.is_file():
        raise MixPlanError("Mix revision audio missing", code=MIX_PLAN_NOT_FOUND)
    return path


def undo_revision(conn: sqlite3.Connection, revision_id: str) -> MixPlanRevisionMeta | None:
    row = get_revision_row(conn, revision_id)
    if row is None:
        raise MixPlanError("Mix plan revision not found", code=MIX_PLAN_NOT_FOUND)
    if not row["is_head"]:
        raise MixPlanError(
            "Undo applies to the current mix head",
            code="mix_plan_invalid_request",
            http_status=409,
        )
    parent_id = row.get("parent_revision_id")
    if not parent_id:
        raise MixPlanError(
            "This mix revision has no parent to restore",
            code="mix_plan_undo_empty",
        )
    conn.execute(
        "UPDATE mix_plan_revisions SET is_head = 0 WHERE id = ?",
        (revision_id,),
    )
    conn.execute(
        "UPDATE mix_plan_revisions SET is_head = 1 WHERE id = ?",
        (parent_id,),
    )
    logger.info(
        "Mix plan head moved to parent",
        extra={"revision_id": revision_id, "parent_revision_id": parent_id},
    )
    parent = get_revision_row(conn, str(parent_id))
    return row_to_meta(parent) if parent else None


def cleanup_project_mix_plans(project_id: str, *, env: Mapping[str, str] | None = None) -> None:
    settings = load_mix_plan_settings(env)
    root = settings.plan_root.resolve()
    target = (settings.plan_root / project_id).resolve()
    removed = 0
    if root == target or root not in target.parents:
        logger.warning(
            "Mix plan cleanup skipped unsafe path",
            extra={"project_id": project_id},
        )
    elif target.is_dir():
        shutil.rmtree(target)
        removed = 1
    try:
        db_path = get_project_db_path()
        if env and env.get("PROJECT_DB_PATH"):
            db_path = Path(env["PROJECT_DB_PATH"])
        if db_path.is_file():
            with get_connection(db_path) as conn:
                cur = conn.execute(
                    "DELETE FROM mix_plan_revisions WHERE project_id = ?",
                    (project_id,),
                )
                removed_rows = cur.rowcount if cur.rowcount is not None else 0
        else:
            removed_rows = 0
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Mix plan revision row cleanup failed",
            extra={"project_id": project_id, "error_type": type(exc).__name__},
        )
        removed_rows = 0
    logger.info(
        "Mix plan project cleanup finished",
        extra={"project_id": project_id, "dir_removed": removed, "rows": removed_rows},
    )


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
