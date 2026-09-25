"""Plugin discovery. Does not invoke plugins except through POST /plugins/reload."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException

from app.ai_runtime.registry import reload_registry
from app.plugin_host.catalog import get_record
from app.plugin_host.schemas import PluginListResponse, PluginPublic
from app.plugin_host.settings import load_plugin_settings
from app.services.plugin_lifecycle import (
    PluginLifecycleError,
    disable_plugin,
    enable_plugin,
    install_plugin,
    public_plugins,
    reconcile_on_startup,
    save_config,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/plugins", tags=["plugins"])


@router.get("", response_model=PluginListResponse)
def list_plugins() -> PluginListResponse:
    plugins = public_plugins()
    logger.debug("plugin list", extra={"plugin_count": len(plugins)})
    return PluginListResponse(plugins=plugins)


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
        plugins = reconcile_on_startup()
    except PluginLifecycleError as exc:
        logger.warning("plugin reload failed", extra={"code": exc.code, "error_type": type(exc).__name__})
        raise HTTPException(status_code=exc.http_status, detail={"code": exc.code, "message": exc.code}) from exc
    enabled_count = sum(1 for plugin in plugins if plugin.status == "enabled")
    logger.info(
        "plugin reload result",
        extra={"plugin_count": len(plugins), "enabled_count": enabled_count},
    )
    return PluginListResponse(plugins=plugins)


@router.post("/{plugin_id}/install", response_model=PluginPublic)
def install_plugin_route(plugin_id: str) -> PluginPublic:
    return _mutation("install", plugin_id, install_plugin, refresh_registry=False)


@router.post("/{plugin_id}/enable", response_model=PluginPublic)
def enable_plugin_route(plugin_id: str) -> PluginPublic:
    return _mutation("enable", plugin_id, enable_plugin, refresh_registry=True)


@router.post("/{plugin_id}/disable", response_model=PluginPublic)
def disable_plugin_route(plugin_id: str) -> PluginPublic:
    return _mutation("disable", plugin_id, disable_plugin, refresh_registry=True)


@router.put("/{plugin_id}/config", response_model=PluginPublic)
def save_plugin_config(plugin_id: str, body: dict[str, Any]) -> PluginPublic:
    decoded = unquote(plugin_id)
    logger.info("plugin route", extra={"route": "config", "plugin_id": decoded})
    try:
        plugin = save_config(decoded, body)
    except PluginLifecycleError as exc:
        logger.warning("plugin route rejected", extra={"plugin_id": decoded, "code": exc.code, "route": "config"})
        raise HTTPException(status_code=exc.http_status, detail={"code": exc.code, "message": exc.code}) from exc
    logger.info(
        "plugin route result",
        extra={"route": "config", "plugin_id": decoded, "lifecycle": plugin.status, "config_present": plugin.config_present},
    )
    return plugin


@router.get("/{plugin_id:path}", response_model=PluginPublic)
def get_plugin(plugin_id: str) -> PluginPublic:
    decoded = unquote(plugin_id)
    logger.debug("plugin detail", extra={"plugin_id": decoded})
    if decoded.startswith("plugin:"):
        raise HTTPException(status_code=404, detail={"code": "plugin_not_found", "message": "plugin_not_found"})
    record = get_record(decoded)
    if record is None:
        raise HTTPException(status_code=404, detail={"code": "plugin_not_found", "message": "plugin_not_found"})
    matched = next((plugin for plugin in public_plugins() if plugin.id == decoded), None)
    if matched is None:
        raise HTTPException(status_code=404, detail={"code": "plugin_not_found", "message": "plugin_not_found"})
    return matched


def _mutation(route: str, plugin_id: str, action, *, refresh_registry: bool) -> PluginPublic:
    decoded = unquote(plugin_id)
    logger.info("plugin route", extra={"route": route, "plugin_id": decoded})
    try:
        plugin = action(decoded)
    except PluginLifecycleError as exc:
        logger.warning("plugin route rejected", extra={"plugin_id": decoded, "code": exc.code, "route": route})
        raise HTTPException(status_code=exc.http_status, detail={"code": exc.code, "message": exc.code}) from exc
    if refresh_registry:
        reload_registry()
    logger.info(
        "plugin route result",
        extra={"route": route, "plugin_id": decoded, "lifecycle": plugin.status},
    )
    return plugin
