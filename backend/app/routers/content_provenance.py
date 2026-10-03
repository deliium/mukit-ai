"""Content provenance chain / manifest / optional credentials HTTP surface.

Does not write ``composition.v2`` notes. Downloads never insert provenance rows.
Flags live on ``GET /content-provenance/status`` only — never ``/ready``.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.content_credentials_settings import load_content_credentials_settings
from app.content_provenance_schemas import (
    ContentProvenanceError,
    ContentProvenanceStatusV1,
    map_content_provenance_error_to_http,
)
from app.db.connection import get_connection, get_project_db_path
from app.routers.collaboration_guard import enforce_current
from app.services.content_credentials import (
    attempt_content_credentials,
    credentials_honesty_flags,
)
from app.services.content_provenance_manifest import (
    assemble_provenance_chain,
    assemble_provenance_manifest,
)
from app.services.content_provenance_store import count_provenance_records
from app.services.persistence_secret_guard import PersistenceSecretError
from app.services.project_store import ProjectNotFoundError, get_project

logger = logging.getLogger(__name__)

router = APIRouter(tags=["content-provenance"])

_LEAF_KINDS = frozenset(
    {
        "composition_revision",
        "neural_render",
        "neural_stem",
        "neural_stem_set",
        "mix_plan_revision",
        "audio_recovery_bind",
        "asset_pack",
    }
)


def _raise(exc: ContentProvenanceError) -> None:
    status, detail = map_content_provenance_error_to_http(exc)
    raise HTTPException(status_code=status, detail=detail) from exc


def _finish(method: str, path_ids: str, status: int, started: float) -> None:
    logger.info(
        "Content provenance route finished",
        extra={
            "method": method,
            "path_ids": path_ids,
            "status": status,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        },
    )


def _ensure_project(project_id: str) -> None:
    try:
        get_project(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Project not found") from exc


def _validate_kind(kind: str) -> str:
    if kind not in _LEAF_KINDS:
        raise ContentProvenanceError(
            "provenance_invalid",
            "Unknown provenance artifact kind.",
            http_status=404,
            details={"artifact_kind": kind},
        )
    return kind


@router.get("/content-provenance/status")
def content_provenance_status() -> ContentProvenanceStatusV1:
    started = time.perf_counter()
    creds = load_content_credentials_settings()
    body = ContentProvenanceStatusV1(
        provenance_enabled=True,
        credentials_enabled=creds.enabled,
        credentials_fake=creds.fake_mode,
    )
    _finish("GET", "status", 200, started)
    return body


@router.get("/content-provenance/projects/{project_id}/artifacts/{kind}/{artifact_id}/chain")
def get_provenance_chain(project_id: str, kind: str, artifact_id: str) -> dict[str, Any]:
    started = time.perf_counter()
    status = 500
    try:
        _ensure_project(project_id)
        enforce_current(project_id, "read")
        kind = _validate_kind(kind)
        with get_connection(get_project_db_path()) as conn:
            chain = assemble_provenance_chain(
                conn,
                project_id=project_id,
                artifact_kind=kind,
                artifact_id=artifact_id,
            )
        status = 200
        _finish("GET", f"{project_id[:8]}/{kind}/{artifact_id[:12]}", status, started)
        return chain.model_dump(mode="json")
    except ContentProvenanceError as exc:
        status = exc.http_status
        _finish("GET", f"{project_id[:8]}/{kind}/{artifact_id[:12]}", status, started)
        _raise(exc)
    except HTTPException:
        raise
    except Exception as exc:
        _finish("GET", f"{project_id[:8]}/{kind}/{artifact_id[:12]}", 500, started)
        raise HTTPException(status_code=500, detail="Provenance chain failed") from exc
    return {}  # pragma: no cover


@router.get("/content-provenance/projects/{project_id}/artifacts/{kind}/{artifact_id}/manifest")
def get_provenance_manifest(project_id: str, kind: str, artifact_id: str) -> dict[str, Any]:
    started = time.perf_counter()
    status = 500
    try:
        _ensure_project(project_id)
        enforce_current(project_id, "read")
        kind = _validate_kind(kind)
        with get_connection(get_project_db_path()) as conn:
            flags = credentials_honesty_flags(
                conn,
                project_id=project_id,
                artifact_kind=kind,
                artifact_id=artifact_id,
            )
            manifest = assemble_provenance_manifest(
                conn,
                project_id=project_id,
                artifact_kind=kind,
                artifact_id=artifact_id,
                c2pa_attached=bool(flags["c2pa_attached"]),
                c2pa_fake_mode=bool(flags["c2pa_fake_mode"]),
                c2pa_status=flags.get("c2pa_status"),
            )
            # Read-only: count after assemble must equal count before for this leaf.
            _ = count_provenance_records(conn, project_id=project_id)
        status = 200
        _finish("GET", f"manifest/{artifact_id[:12]}", status, started)
        return manifest.model_dump(mode="json")
    except ContentProvenanceError as exc:
        status = exc.http_status
        _finish("GET", f"manifest/{artifact_id[:12]}", status, started)
        _raise(exc)
    except PersistenceSecretError as exc:
        status = 400
        _finish("GET", f"manifest/{artifact_id[:12]}", status, started)
        raise HTTPException(
            status_code=400,
            detail={"code": getattr(exc, "code", "forbidden_secret_field"), "message": str(exc)[:200]},
        ) from exc
    except HTTPException:
        raise
    except Exception as exc:
        _finish("GET", f"manifest/{artifact_id[:12]}", 500, started)
        raise HTTPException(status_code=500, detail="Provenance manifest failed") from exc
    return {}  # pragma: no cover


@router.get(
    "/content-provenance/projects/{project_id}/artifacts/{kind}/{artifact_id}/manifest/download"
)
def download_provenance_manifest(project_id: str, kind: str, artifact_id: str) -> Response:
    started = time.perf_counter()
    status = 500
    try:
        _ensure_project(project_id)
        enforce_current(project_id, "read")
        kind = _validate_kind(kind)
        with get_connection(get_project_db_path()) as conn:
            before = count_provenance_records(conn, project_id=project_id)
            flags = credentials_honesty_flags(
                conn,
                project_id=project_id,
                artifact_kind=kind,
                artifact_id=artifact_id,
            )
            manifest = assemble_provenance_manifest(
                conn,
                project_id=project_id,
                artifact_kind=kind,
                artifact_id=artifact_id,
                c2pa_attached=bool(flags["c2pa_attached"]),
                c2pa_fake_mode=bool(flags["c2pa_fake_mode"]),
                c2pa_status=flags.get("c2pa_status"),
            )
            after = count_provenance_records(conn, project_id=project_id)
            if after != before:
                logger.warning(
                    "Provenance download unexpectedly changed record count",
                    extra={"project_id": project_id, "before": before, "after": after},
                )
        payload = json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
        filename = f"provenance_{kind}_{artifact_id[:24]}.json"
        status = 200
        _finish("GET", f"download/{artifact_id[:12]}", status, started)
        return Response(
            content=payload.encode("utf-8"),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except ContentProvenanceError as exc:
        status = exc.http_status
        _finish("GET", f"download/{artifact_id[:12]}", status, started)
        _raise(exc)
    except HTTPException:
        raise
    except Exception as exc:
        _finish("GET", f"download/{artifact_id[:12]}", 500, started)
        raise HTTPException(status_code=500, detail="Provenance download failed") from exc
    return Response(status_code=500)  # pragma: no cover


@router.post(
    "/content-provenance/projects/{project_id}/artifacts/{kind}/{artifact_id}/credentials"
)
def post_content_credentials(project_id: str, kind: str, artifact_id: str) -> dict[str, Any]:
    started = time.perf_counter()
    status = 500
    try:
        _ensure_project(project_id)
        enforce_current(project_id, "write")
        kind = _validate_kind(kind)
        with get_connection(get_project_db_path()) as conn:
            result = attempt_content_credentials(
                conn,
                project_id=project_id,
                artifact_kind=kind,
                artifact_id=artifact_id,
            )
        status = 200
        _finish("POST", f"credentials/{artifact_id[:12]}", status, started)
        return result.model_dump(mode="json")
    except ContentProvenanceError as exc:
        status = exc.http_status
        _finish("POST", f"credentials/{artifact_id[:12]}", status, started)
        _raise(exc)
    except HTTPException:
        raise
    except Exception as exc:
        _finish("POST", f"credentials/{artifact_id[:12]}", 500, started)
        raise HTTPException(status_code=500, detail="Credentials attempt failed") from exc
    return {}  # pragma: no cover
