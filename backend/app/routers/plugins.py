"""Plugin discovery. Does not invoke plugins except through POST /plugins/reload."""

from __future__ import annotations

import logging
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException

from app.plugin_host.bridge import reload_plugins
from app.plugin_host.catalog import get_record, list_records
from app.plugin_host.schemas import PluginListResponse, PluginPublic, public_plugin
from app.plugin_host.settings import load_plugin_settings
from app.plugin_sdk.errors import PluginError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/plugins", tags=["plugins"])


@router.get("", response_model=PluginListResponse)
def list_plugins() -> PluginListResponse:
    logger.debug("plugin list", extra={"plugin_count": len(list_records())})
    return PluginListResponse(plugins=[public_plugin(record) for record in list_records()])


@router.get("/{plugin_id:path}", response_model=PluginPublic)
def get_plugin(plugin_id: str) -> PluginPublic:
    decoded = unquote(plugin_id)
    logger.debug("plugin detail", extra={"plugin_id": decoded})
    record = get_record(decoded)
    if record is None or decoded.startswith("plugin:"):
        raise HTTPException(status_code=404, detail={"code": "plugin_not_found", "message": "plugin_not_found"})
    return public_plugin(record)


@router.post("/reload", response_model=PluginListResponse)
def reload_plugin_catalog() -> PluginListResponse:
    settings = load_plugin_settings()
    logger.info("plugin reload requested", extra={"reload_enabled": settings.reload_enabled})
    if not settings.reload_enabled:
        raise HTTPException(
            status_code=403,
            detail={"code": "plugin_reload_disabled", "message": "plugin_reload_disabled"},
        )
    try:
        records = reload_plugins()
    except PluginError as exc:
        logger.warning("plugin reload failed", extra={"code": exc.code, "error_type": type(exc).__name__})
        raise HTTPException(status_code=422, detail={"code": exc.code, "message": exc.code}) from exc
    active_count = sum(1 for record in records if record.status == "active")
    logger.info(
        "plugin reload result",
        extra={"plugin_count": len(records), "active_count": active_count},
    )
    return PluginListResponse(plugins=[public_plugin(record) for record in records])
