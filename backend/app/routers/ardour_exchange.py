"""HTTP surface for Ardour session exchange packages (ingest / realize / prepare)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from app.ardour_companion_settings import load_ardour_companion_settings
from app.ardour_exchange_schemas import (
    ArdourExchangeApplyResponseV1,
    ArdourExchangeError,
    ArdourExchangeIngestRequestV1,
    ArdourExchangePackageV1,
    ArdourExchangePrepareRequestV1,
    ArdourExchangePrepareResultV1,
    ArdourExchangePreviewV1,
    ArdourExchangeRealizeRequestV1,
    ArdourExchangeSessionContextV1,
    ArdourExchangeStatusV1,
    map_ardour_exchange_error_to_http,
)
from app.ardour_exchange_settings import load_ardour_exchange_settings
from app.audio_upload import UploadTooLargeError, read_upload_bounded
from app.services import ardour_companion_service as companion
from app.services import ardour_exchange_store as store
from app.services.ardour_exchange_context import build_exchange_session_context
from app.services.ardour_exchange_ingest import (
    apply_preview_payload,
    ingest_package_id,
    ingest_package_zip,
)
from app.services.ardour_exchange_prepare import prepare_outbound_package, prepare_zip_bytes
from app.services.ardour_exchange_realize import realize_exchange_intent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ardour/exchange", tags=["ardour-exchange"])


def _raise(exc: ArdourExchangeError) -> None:
    status, detail = map_ardour_exchange_error_to_http(exc)
    raise HTTPException(status_code=status, detail=detail) from exc


def _require_enabled_root():
    settings = load_ardour_exchange_settings()
    if not settings.enabled:
        raise ArdourExchangeError("ardour_exchange_disabled")
    if not settings.root_configured or settings.exchange_root is None:
        raise ArdourExchangeError("ardour_exchange_root_unconfigured")
    return settings


@router.get("/status", response_model=ArdourExchangeStatusV1)
async def ardour_exchange_status() -> ArdourExchangeStatusV1:
    """Always 200 with ``enabled``; never 403 when the flag is off."""
    settings = load_ardour_exchange_settings()
    companion_settings = load_ardour_companion_settings()
    companion_status = companion.get_ardour_companion_status(settings=companion_settings)
    companion_connected = companion_status.connection_state in {
        "connected",
        "awaiting_feedback",
        "stale",
    }
    preview = store.get_exchange_preview()
    status = ArdourExchangeStatusV1(
        enabled=settings.enabled,
        root_configured=settings.root_configured,
        companion_connected=companion_connected,
        max_package_bytes=settings.max_package_bytes,
        preview_present=preview is not None,
        package_count=store.package_count(settings) if settings.root_configured else 0,
    )
    logger.info(
        "Ardour exchange status",
        extra={"path": "/ardour/exchange/status", "code": "ok", "enabled": status.enabled},
    )
    return status


@router.get("/context", response_model=ArdourExchangeSessionContextV1)
async def ardour_exchange_context() -> ArdourExchangeSessionContextV1:
    context = build_exchange_session_context()
    logger.info(
        "Ardour exchange context",
        extra={
            "path": "/ardour/exchange/context",
            "code": "ok",
            "connection_state": context.connection_state,
        },
    )
    return context


@router.get("/packages", response_model=list[ArdourExchangePackageV1])
async def ardour_exchange_list_packages() -> list[ArdourExchangePackageV1]:
    try:
        settings = _require_enabled_root()
        rows = store.list_packages(settings)
    except ArdourExchangeError as exc:
        _raise(exc)
    logger.info(
        "Ardour exchange list",
        extra={"path": "/ardour/exchange/packages", "code": "ok", "count": len(rows)},
    )
    return rows


@router.post("/ingest", response_model=ArdourExchangePreviewV1)
async def ardour_exchange_ingest(request: Request) -> ArdourExchangePreviewV1:
    """Ingest multipart zip (bounded) or JSON ``{ package_id }`` under the root."""
    try:
        settings = _require_enabled_root()
        session_context = build_exchange_session_context()
        content_type = (request.headers.get("content-type") or "").lower()
        if "multipart/form-data" in content_type:
            form = await request.form()
            upload = form.get("file")
            if upload is None or not hasattr(upload, "read"):
                package_id = form.get("package_id")
                if isinstance(package_id, str) and package_id.strip():
                    body = ArdourExchangeIngestRequestV1.model_validate(
                        {"package_id": package_id.strip()}
                    )
                    preview = ingest_package_id(
                        settings,
                        body.package_id,
                        session_context=session_context,
                    )
                else:
                    raise ArdourExchangeError(
                        "ardour_exchange_package_invalid",
                        details={"reason": "missing_package"},
                    )
            else:
                try:
                    zip_bytes = await read_upload_bounded(
                        upload,
                        max_bytes=settings.max_package_bytes,
                    )
                except UploadTooLargeError as exc:
                    raise ArdourExchangeError("ardour_exchange_package_too_large") from exc
                preview = ingest_package_zip(
                    settings,
                    zip_bytes,
                    session_context=session_context,
                )
        else:
            payload = await request.json()
            body = ArdourExchangeIngestRequestV1.model_validate(payload)
            preview = ingest_package_id(
                settings,
                body.package_id,
                session_context=session_context,
            )
    except ArdourExchangeError as exc:
        logger.info(
            "Ardour exchange ingest refused",
            extra={"path": "/ardour/exchange/ingest", "code": exc.code},
        )
        _raise(exc)
    except HTTPException:
        raise
    except Exception as exc:
        logger.info(
            "Ardour exchange ingest invalid",
            extra={
                "path": "/ardour/exchange/ingest",
                "code": "ardour_exchange_package_invalid",
                "error_type": type(exc).__name__,
            },
        )
        _raise(ArdourExchangeError("ardour_exchange_package_invalid"))
    logger.info(
        "Ardour exchange ingest",
        extra={
            "path": "/ardour/exchange/ingest",
            "code": "ok",
            "package_id": preview.package_id,
        },
    )
    return preview


@router.get("/preview", response_model=ArdourExchangePreviewV1)
async def ardour_exchange_get_preview() -> ArdourExchangePreviewV1:
    preview = store.get_exchange_preview()
    if preview is None:
        _raise(ArdourExchangeError("ardour_exchange_preview_missing"))
    logger.info(
        "Ardour exchange preview",
        extra={"path": "/ardour/exchange/preview", "code": "ok", "package_id": preview.package_id},
    )
    return preview


@router.delete("/preview")
async def ardour_exchange_delete_preview() -> dict[str, Any]:
    store.clear_exchange_preview()
    logger.info(
        "Ardour exchange preview cleared",
        extra={"path": "/ardour/exchange/preview", "code": "ok"},
    )
    return {"cleared": True}


@router.post("/apply", response_model=ArdourExchangeApplyResponseV1)
async def ardour_exchange_apply() -> ArdourExchangeApplyResponseV1:
    """Return composition for SPA ``completeImport``. Does not write project rows."""
    try:
        payload = apply_preview_payload()
    except ArdourExchangeError as exc:
        _raise(exc)
    logger.info(
        "Ardour exchange apply",
        extra={"path": "/ardour/exchange/apply", "code": "ok"},
    )
    return ArdourExchangeApplyResponseV1.model_validate(payload)


@router.post("/realize")
async def ardour_exchange_realize(body: ArdourExchangeRealizeRequestV1) -> dict[str, Any]:
    try:
        _require_enabled_root()
        result = await realize_exchange_intent(body)
    except ArdourExchangeError as exc:
        logger.info(
            "Ardour exchange realize refused",
            extra={"path": "/ardour/exchange/realize", "code": exc.code},
        )
        _raise(exc)
    logger.info(
        "Ardour exchange realize",
        extra={
            "path": "/ardour/exchange/realize",
            "code": "ok",
            "intent": body.intent,
        },
    )
    return result


@router.post("/prepare", response_model=ArdourExchangePrepareResultV1)
async def ardour_exchange_prepare(
    body: ArdourExchangePrepareRequestV1,
) -> ArdourExchangePrepareResultV1:
    try:
        settings = _require_enabled_root()
        result = prepare_outbound_package(settings, body)
    except ArdourExchangeError as exc:
        logger.info(
            "Ardour exchange prepare refused",
            extra={"path": "/ardour/exchange/prepare", "code": exc.code},
        )
        _raise(exc)
    logger.info(
        "Ardour exchange prepare",
        extra={
            "path": "/ardour/exchange/prepare",
            "code": "ok",
            "package_id": result.package_id,
        },
    )
    return result


@router.get("/packages/{package_id}/download")
async def ardour_exchange_download(package_id: str) -> Response:
    try:
        settings = _require_enabled_root()
        zip_bytes = prepare_zip_bytes(settings, package_id)
    except ArdourExchangeError as exc:
        _raise(exc)
    logger.info(
        "Ardour exchange download",
        extra={
            "path": "/ardour/exchange/packages/download",
            "code": "ok",
            "package_id": package_id,
        },
    )
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{package_id}.zip"',
        },
    )
