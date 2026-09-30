"""Immutable project video bytes and the ``video.scoring.v1`` document.

The stored file is written once to a new path. Probe and scoring updates do
not open it for write. Project delete removes the directory after SQLite
cascade drops the rows.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path

from app.db.connection import get_connection
from app.services.composition_timeline import CompiledTimeline
from app.services.video_container_probe import VideoAssetProbe, probe_iso_bmff
from app.video_scoring_schemas import (
    VideoAssetV1,
    VideoScoringPersistedV1,
    VideoScoringUpdateV1,
    VideoScoringV1,
    VideoScoringError,
    default_video_scoring,
    parse_video_scoring,
    scoring_from_update,
)
from app.video_scoring_settings import VideoScoringSettings, load_video_scoring_settings

logger = logging.getLogger(__name__)


def allocate_asset_id() -> str:
    return "vid_" + secrets.token_hex(4)


def ingest_video_bytes(
    payload: bytes,
    *,
    project_id: str,
    db_path: Path | str,
    settings: VideoScoringSettings | None = None,
) -> tuple[VideoAssetV1, VideoScoringV1]:
    """Probe a temp upload, write a new file, commit, then delete the previous file."""
    resolved = settings if settings is not None else load_video_scoring_settings()
    directory = _project_directory(resolved, project_id)
    directory.mkdir(parents=True, exist_ok=True)
    asset_id = allocate_asset_id()
    temp = directory / f".{asset_id}.part"
    logger.info(
        "video asset ingest started",
        extra={"project_id": project_id, "asset_id": asset_id, "byte_size": len(payload)},
    )
    try:
        temp.write_bytes(payload)
        probe = probe_iso_bmff(temp)
    except Exception:
        temp.unlink(missing_ok=True)
        logger.debug(
            "video asset temp removed after probe failure",
            extra={"project_id": project_id, "asset_id": asset_id},
        )
        raise
    ext = "mov" if probe.container == "mov" else "mp4"
    final_name = f"{asset_id}.{ext}"
    final = directory / final_name
    relpath = f"{project_id}/{final_name}"
    try:
        temp.replace(final)
    except OSError:
        temp.unlink(missing_ok=True)
        raise
    digest = hashlib.sha256(payload).hexdigest()
    created_at = _utc_now()
    asset = VideoAssetV1(
        asset_id=asset_id,
        project_id=project_id,
        container=probe.container,  # type: ignore[arg-type]
        content_type=probe.content_type,  # type: ignore[arg-type]
        byte_size=len(payload),
        sha256_prefix=digest[:16],
        duration_seconds=probe.duration_seconds,
        frame_rate_numerator=probe.frame_rate_numerator,
        frame_rate_denominator=probe.frame_rate_denominator,
        has_audio=probe.has_audio,
        width=probe.width,
        height=probe.height,
        created_at=created_at,
    )
    previous_relpath: str | None = None
    try:
        with get_connection(db_path) as conn:
            previous_relpath = _commit_asset(conn, asset, relpath, probe)
            scoring = _load_scoring_row(conn, project_id)
    except Exception:
        final.unlink(missing_ok=True)
        logger.warning(
            "video asset commit failed; new file removed",
            extra={"project_id": project_id, "asset_id": asset_id, "byte_size": len(payload)},
        )
        raise
    if previous_relpath and previous_relpath != relpath:
        _unlink_relpath(resolved, previous_relpath)
    logger.info(
        "video asset stored",
        extra={
            "project_id": project_id,
            "asset_id": asset.asset_id,
            "byte_size": asset.byte_size,
            "document_revision": scoring.document_revision,
        },
    )
    return asset, scoring


def get_video_asset(
    project_id: str,
    *,
    db_path: Path | str,
) -> VideoAssetV1:
    with get_connection(db_path) as conn:
        row = _asset_row(conn, project_id)
    if row is None:
        raise VideoScoringError("video_asset_missing")
    return _asset_from_row(row)


def video_media_path(
    project_id: str,
    *,
    db_path: Path | str,
    settings: VideoScoringSettings | None = None,
) -> tuple[VideoAssetV1, Path]:
    resolved = settings if settings is not None else load_video_scoring_settings()
    asset_row_path: str
    with get_connection(db_path) as conn:
        row = _asset_row(conn, project_id)
        if row is None:
            raise VideoScoringError("video_asset_missing")
        asset = _asset_from_row(row)
        asset_row_path = str(row["relpath"])
    path = _path_under_root(resolved, asset_row_path)
    if not path.is_file():
        raise VideoScoringError("video_asset_missing")
    return asset, path


def delete_video_asset(
    project_id: str,
    *,
    db_path: Path | str,
    settings: VideoScoringSettings | None = None,
) -> VideoScoringV1:
    resolved = settings if settings is not None else load_video_scoring_settings()
    with get_connection(db_path) as conn:
        row = _asset_row(conn, project_id)
        if row is None:
            raise VideoScoringError("video_asset_missing")
        relpath = str(row["relpath"])
        byte_size = int(row["byte_size"])
        conn.execute("DELETE FROM video_assets WHERE project_id = ?", (project_id,))
        scoring = _clear_picture_binding(conn, project_id)
    _unlink_relpath(resolved, relpath)
    logger.info(
        "video asset deleted",
        extra={
            "project_id": project_id,
            "byte_size": byte_size,
            "document_revision": scoring.document_revision,
        },
    )
    return scoring


def get_video_scoring(project_id: str, *, db_path: Path | str) -> VideoScoringV1:
    """Return the document. No row yields revision 0 and does not insert."""
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT body_json, document_revision FROM video_scoring WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        if row is None:
            logger.debug("video scoring defaults", extra={"project_id": project_id, "document_revision": 0})
            return default_video_scoring(project_id)
        document = parse_video_scoring(json.loads(row["body_json"]), persisted=True)
    return document


def put_video_scoring(
    project_id: str,
    update: VideoScoringUpdateV1,
    *,
    db_path: Path | str,
    timeline: CompiledTimeline,
) -> VideoScoringV1:
    with get_connection(db_path) as conn:
        current = _scoring_revision(conn, project_id)
        if update.expected_document_revision != current:
            logger.warning(
                "video scoring conflict",
                extra={
                    "project_id": project_id,
                    "error_code": "video_scoring_conflict",
                    "document_revision": current,
                },
            )
            raise VideoScoringError("video_scoring_conflict")
        asset = _asset_row(conn, project_id)
        asset_id = None if asset is None else str(asset["asset_id"])
        document = scoring_from_update(
            project_id,
            update,
            asset_id=asset_id,
            document_revision=current + 1,
            timeline=timeline,
        )
        VideoScoringPersistedV1.model_validate(document.model_dump())
        _upsert_scoring(conn, document, inserting=current == 0)
    logger.info(
        "video scoring stored",
        extra={
            "project_id": project_id,
            "asset_id": document.asset_id,
            "document_revision": document.document_revision,
        },
    )
    return document


def cleanup_project_video_assets(
    project_id: str,
    *,
    settings: VideoScoringSettings | None = None,
) -> None:
    """Delete the project picture directory. Failures are warnings."""
    try:
        resolved = settings if settings is not None else load_video_scoring_settings()
        directory = _project_directory(resolved, project_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "video asset cleanup skipped",
            extra={"project_id": project_id, "error_type": type(exc).__name__},
        )
        return
    if not directory.exists():
        logger.debug("video asset cleanup empty", extra={"project_id": project_id})
        return
    try:
        shutil.rmtree(directory)
        logger.info("video asset directory removed", extra={"project_id": project_id})
    except OSError as exc:
        logger.warning(
            "video asset cleanup failed",
            extra={"project_id": project_id, "error_type": type(exc).__name__},
        )


def _commit_asset(conn, asset: VideoAssetV1, relpath: str, probe: VideoAssetProbe) -> str | None:
    previous = _asset_row(conn, asset.project_id)
    previous_relpath = None if previous is None else str(previous["relpath"])
    if previous is not None:
        conn.execute("DELETE FROM video_assets WHERE project_id = ?", (asset.project_id,))
    conn.execute(
        """
        INSERT INTO video_assets (
            asset_id, project_id, container, content_type, byte_size, sha256_prefix,
            duration_seconds, frame_rate_numerator, frame_rate_denominator, has_audio,
            width, height, relpath, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            asset.asset_id,
            asset.project_id,
            asset.container,
            asset.content_type,
            asset.byte_size,
            asset.sha256_prefix,
            asset.duration_seconds,
            asset.frame_rate_numerator,
            asset.frame_rate_denominator,
            1 if asset.has_audio else 0,
            asset.width,
            asset.height,
            relpath,
            asset.created_at,
        ),
    )
    _apply_probe_to_scoring(conn, asset.project_id, asset.asset_id, probe)
    return previous_relpath


def _apply_probe_to_scoring(conn, project_id: str, asset_id: str, probe: VideoAssetProbe) -> None:
    existing = _load_scoring_row(conn, project_id)
    now = _utc_now()
    if existing.document_revision == 0:
        numerator = denominator = None
        source = None
        if probe.frame_rate_snapped:
            numerator = probe.frame_rate_numerator
            denominator = probe.frame_rate_denominator
            source = "probed"
        document = VideoScoringPersistedV1.model_validate(
            {
                "project_id": project_id,
                "asset_id": asset_id,
                "frame_rate_numerator": numerator,
                "frame_rate_denominator": denominator,
                "frame_rate_source": source,
                "timecode_mode": "non_drop",
                "start_timecode": "00:00:00:00",
                "video_origin_seconds": 0,
                "musical_origin_tick": 0,
                "hit_points": [],
                "document_revision": 1,
            }
        )
        _upsert_scoring(conn, document, inserting=True)
        return
    numerator = existing.frame_rate_numerator
    denominator = existing.frame_rate_denominator
    source = existing.frame_rate_source
    timecode_mode = existing.timecode_mode
    if source != "explicit":
        if probe.frame_rate_snapped:
            numerator = probe.frame_rate_numerator
            denominator = probe.frame_rate_denominator
            source = "probed"
        else:
            numerator = None
            denominator = None
            source = None
            if timecode_mode == "drop_frame":
                timecode_mode = "non_drop"
    document = existing.model_copy(
        update={
            "asset_id": asset_id,
            "frame_rate_numerator": numerator,
            "frame_rate_denominator": denominator,
            "frame_rate_source": source,
            "timecode_mode": timecode_mode,
            "document_revision": existing.document_revision + 1,
        }
    )
    VideoScoringPersistedV1.model_validate(document.model_dump())
    _upsert_scoring(conn, document, inserting=False)


def _clear_picture_binding(conn, project_id: str) -> VideoScoringV1:
    existing = _load_scoring_row(conn, project_id)
    if existing.document_revision == 0:
        return existing
    document = existing.model_copy(
        update={
            "asset_id": None,
            "hit_points": [],
            "document_revision": existing.document_revision + 1,
        }
    )
    VideoScoringPersistedV1.model_validate(document.model_dump())
    _upsert_scoring(conn, document, inserting=False)
    return document


def _load_scoring_row(conn, project_id: str) -> VideoScoringV1:
    row = conn.execute(
        "SELECT body_json FROM video_scoring WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    if row is None:
        return default_video_scoring(project_id)
    return parse_video_scoring(json.loads(row["body_json"]), persisted=True)


def _scoring_revision(conn, project_id: str) -> int:
    row = conn.execute(
        "SELECT document_revision FROM video_scoring WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    if row is None:
        return 0
    return int(row["document_revision"])


def _upsert_scoring(conn, document: VideoScoringV1, *, inserting: bool) -> None:
    body = json.dumps(document.model_dump(mode="json"), separators=(",", ":"), sort_keys=True)
    now = _utc_now()
    if inserting:
        conn.execute(
            """
            INSERT INTO video_scoring (project_id, body_json, document_revision, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (document.project_id, body, document.document_revision, now, now),
        )
        return
    updated = conn.execute(
        """
        UPDATE video_scoring
        SET body_json = ?, document_revision = ?, updated_at = ?
        WHERE project_id = ? AND document_revision = ?
        """,
        (
            body,
            document.document_revision,
            now,
            document.project_id,
            document.document_revision - 1,
        ),
    )
    if updated.rowcount != 1:
        raise VideoScoringError("video_scoring_conflict")


def _asset_row(conn, project_id: str):
    return conn.execute(
        "SELECT * FROM video_assets WHERE project_id = ?",
        (project_id,),
    ).fetchone()


def _asset_from_row(row) -> VideoAssetV1:
    return VideoAssetV1.model_validate(
        {
            "asset_id": row["asset_id"],
            "project_id": row["project_id"],
            "container": row["container"],
            "content_type": row["content_type"],
            "byte_size": int(row["byte_size"]),
            "sha256_prefix": row["sha256_prefix"],
            "duration_seconds": float(row["duration_seconds"]),
            "frame_rate_numerator": int(row["frame_rate_numerator"]),
            "frame_rate_denominator": int(row["frame_rate_denominator"]),
            "has_audio": bool(row["has_audio"]),
            "width": int(row["width"]),
            "height": int(row["height"]),
            "created_at": row["created_at"],
        }
    )


def _project_directory(settings: VideoScoringSettings, project_id: str) -> Path:
    if (
        not project_id
        or "/" in project_id
        or "\\" in project_id
        or project_id in {".", ".."}
    ):
        raise VideoScoringError("video_scoring_invalid")
    return _path_under_root(settings, project_id)


def _path_under_root(settings: VideoScoringSettings, relative: str) -> Path:
    root = settings.asset_root.expanduser().resolve()
    path = (root / relative).resolve()
    if path != root and root not in path.parents:
        raise VideoScoringError("video_scoring_invalid")
    return path


def _unlink_relpath(settings: VideoScoringSettings, relative: str) -> None:
    try:
        path = _path_under_root(settings, relative)
    except VideoScoringError:
        logger.warning("video asset unlink skipped", extra={"error_code": "video_scoring_invalid"})
        return
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning(
            "video asset unlink failed",
            extra={"error_type": type(exc).__name__},
        )


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
