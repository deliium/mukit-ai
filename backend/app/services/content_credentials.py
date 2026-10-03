"""Optional C2PA Content Credentials evaluation for supported WAV egress.

Never required for core studio operation. Fake mode never upgrades provenance
records to ``c2pa_signed``. Failure never blocks unsigned WAV download.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.content_credentials_settings import load_content_credentials_settings
from app.content_provenance_schemas import (
    CONTENT_PROVENANCE_ERROR_CODES,
    ContentCredentialsStatusV1,
    ContentProvenanceError,
)

logger = logging.getLogger(__name__)

_SUPPORTED_KINDS = frozenset({"neural_render", "neural_stem", "mix_plan_revision"})


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def attempt_content_credentials(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    artifact_kind: str,
    artifact_id: str,
    env: dict[str, str] | None = None,
) -> ContentCredentialsStatusV1:
    """Attempt optional credential attach. Soft-import real C2PA when available."""
    settings = load_content_credentials_settings(env)
    if not settings.enabled:
        logger.info(
            "Content credentials attempt skipped",
            extra={"reason_code": "content_credentials_disabled", "artifact_kind": artifact_kind},
        )
        raise ContentProvenanceError(
            "content_credentials_disabled",
            CONTENT_PROVENANCE_ERROR_CODES["content_credentials_disabled"],
            http_status=422,
        )
    if artifact_kind not in _SUPPORTED_KINDS:
        status = ContentCredentialsStatusV1(
            enabled=True,
            fake_mode=settings.fake_mode,
            attached=False,
            status="unsupported_media",
            reason_code="unsupported_media",
            artifact_kind=artifact_kind,  # type: ignore[arg-type]
            artifact_id=artifact_id,
        )
        _persist_status(conn, project_id, status)
        return status

    if settings.fake_mode:
        # Fake credentials: sibling meta only; records stay mukit_internal.
        settings.credentials_root.mkdir(parents=True, exist_ok=True)
        sibling = settings.credentials_root / project_id / f"{artifact_kind}_{artifact_id}.c2pa.fake.json"
        sibling.parent.mkdir(parents=True, exist_ok=True)
        sibling.write_text(
            json.dumps(
                {
                    "schema_version": "content.credentials.status.v1",
                    "fake_mode": True,
                    "artifact_kind": artifact_kind,
                    "artifact_id": artifact_id,
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        status = ContentCredentialsStatusV1(
            enabled=True,
            fake_mode=True,
            attached=True,
            status="fake",
            reason_code=None,
            artifact_kind=artifact_kind,  # type: ignore[arg-type]
            artifact_id=artifact_id,
            credential_path_prefix=sibling.name[:80],
        )
        _persist_status(conn, project_id, status)
        logger.info(
            "Content credentials fake attach",
            extra={
                "artifact_kind": artifact_kind,
                "artifact_id_prefix": artifact_id[:12],
                "fake_mode": True,
            },
        )
        return status

    # Soft import real library — never required in CI.
    try:
        import c2pa  # type: ignore[import-not-found]  # noqa: F401
    except Exception:
        logger.warning(
            "Content credentials library unavailable",
            extra={"reason_code": "c2pa_library_unavailable"},
        )
        status = ContentCredentialsStatusV1(
            enabled=True,
            fake_mode=False,
            attached=False,
            status="unavailable",
            reason_code="c2pa_library_unavailable",
            artifact_kind=artifact_kind,  # type: ignore[arg-type]
            artifact_id=artifact_id,
        )
        _persist_status(conn, project_id, status)
        return status

    # Real attach path reserved for operators with keys; ship-1 soft-fails.
    status = ContentCredentialsStatusV1(
        enabled=True,
        fake_mode=False,
        attached=False,
        status="failed",
        reason_code="content_credentials_failed",
        artifact_kind=artifact_kind,  # type: ignore[arg-type]
        artifact_id=artifact_id,
    )
    _persist_status(conn, project_id, status)
    logger.warning(
        "Content credentials attach failed",
        extra={"reason_code": "content_credentials_failed", "artifact_kind": artifact_kind},
    )
    return status


def _persist_status(
    conn: sqlite3.Connection,
    project_id: str,
    status: ContentCredentialsStatusV1,
) -> None:
    body = status.model_dump(mode="json")
    conn.execute(
        """
        INSERT INTO content_provenance_credentials (
            project_id, artifact_kind, artifact_id, status, fake_mode, attached,
            reason_code, credential_path_prefix, body_json, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(project_id, artifact_kind, artifact_id) DO UPDATE SET
            status = excluded.status,
            fake_mode = excluded.fake_mode,
            attached = excluded.attached,
            reason_code = excluded.reason_code,
            credential_path_prefix = excluded.credential_path_prefix,
            body_json = excluded.body_json,
            updated_at = excluded.updated_at
        """,
        (
            project_id,
            status.artifact_kind,
            status.artifact_id,
            status.status,
            1 if status.fake_mode else 0,
            1 if status.attached else 0,
            status.reason_code,
            status.credential_path_prefix,
            json.dumps(body, separators=(",", ":")),
            _utc_now_iso(),
        ),
    )


def load_credentials_status(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    artifact_kind: str,
    artifact_id: str,
) -> ContentCredentialsStatusV1 | None:
    row = conn.execute(
        """
        SELECT body_json FROM content_provenance_credentials
        WHERE project_id = ? AND artifact_kind = ? AND artifact_id = ?
        """,
        (project_id, artifact_kind, artifact_id),
    ).fetchone()
    if row is None:
        return None
    try:
        payload = json.loads(str(row["body_json"]))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return ContentCredentialsStatusV1.model_validate(payload)


def credentials_honesty_flags(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    artifact_kind: str,
    artifact_id: str,
) -> dict[str, Any]:
    """Flags for manifest honesty; fake never implies cryptographic=true alone."""
    status = load_credentials_status(
        conn,
        project_id=project_id,
        artifact_kind=artifact_kind,
        artifact_id=artifact_id,
    )
    if status is None:
        return {"c2pa_attached": False, "c2pa_fake_mode": False, "c2pa_status": None}
    return {
        "c2pa_attached": bool(status.attached),
        "c2pa_fake_mode": bool(status.fake_mode),
        "c2pa_status": status.status,
    }
