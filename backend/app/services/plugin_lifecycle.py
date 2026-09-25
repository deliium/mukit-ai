"""Install, enable, disable, and startup reconcile for discovered plugins.

This module is the only writer of ``plugin_installations``. The host never
reads SQLite. Derivation does not import plugin modules.
"""

from __future__ import annotations

import logging
from typing import Any

from app.plugin_host.bridge import activate_plugin, reload_plugins
from app.plugin_host.catalog import PluginRecord, get_record, list_records, update_record
from app.plugin_host.import_guard import drop_private_module
from app.plugin_host.loader import (
    _activation_order,
    _cycle_ids,
    dependency_block_code,
    read_package_config,
    reread_manifest,
)
from app.plugin_host.schemas import PluginPublic, public_plugin
from app.plugin_host.settings import load_plugin_settings
from app.plugin_sdk.config import validate_config
from app.plugin_sdk.errors import PluginError
from app.plugin_sdk.manifest import PluginManifestV1
from app.plugin_sdk.version import accepts_api_compatibility
from app.services.persistence_secret_guard import PersistenceSecretError
from app.services.plugin_installation_store import (
    PluginInstallation,
    get_installation,
    list_installations,
    upsert_installation,
)

logger = logging.getLogger(__name__)

_DISCOVERY_REFUSAL = frozenset(
    {
        "plugin_manifest_invalid",
        "plugin_duplicate_id",
        "plugin_limit_exceeded",
    }
)


class PluginLifecycleError(Exception):
    """Gate refusal or missing plugin. ``desired_state`` is left unchanged."""

    def __init__(self, code: str, *, http_status: int = 409) -> None:
        self.code = code
        self.http_status = http_status
        super().__init__(code)


def derive_lifecycle(
    *,
    scan_status: str,
    scan_code: str | None,
    resources: tuple[str, ...] | list[str],
    manifest_version: str | None,
    desired_state: str | None,
    installed_version: str | None,
    dependency_code: str | None = None,
    activation_code: str | None = None,
) -> tuple[str, str | None]:
    """First matching lifecycle state. Does not import plugin code.

    Returns ``(status, code)``. ``desired_state`` is None when no row exists.
    """
    if scan_status == "failed":
        return "failed", scan_code or "plugin_manifest_invalid"
    if scan_status == "incompatible":
        return "incompatible", scan_code or "plugin_api_incompatible"
    if desired_state is None:
        return "discovered", None
    if manifest_version != installed_version:
        return "failed", "plugin_version_mismatch"
    if desired_state == "disabled":
        return "disabled", None
    if desired_state == "installed":
        return "installed", None
    if tuple(resources):
        return "failed", "plugin_isolation_unavailable"
    if dependency_code:
        return "failed", dependency_code
    if activation_code:
        return "failed", activation_code
    return "enabled", None


def reconcile_on_startup() -> list[PluginPublic]:
    """Rescan manifests and re-apply desired state. Does not install new ids."""
    logger.info("plugin reconcile start", extra={"db_configured": True})
    try:
        reload_plugins()
        rows = {row.plugin_id: row for row in list_installations()}
    except Exception as exc:
        logger.error(
            "plugin reconcile store failure",
            extra={"error_type": type(exc).__name__, "code": "plugin_reconcile_failed"},
        )
        raise
    records = list_records()
    pending: dict[str, PluginRecord] = {}
    for record in records:
        row = rows.get(record.id) if record.id else None
        _attach_install_meta(record, row)
        if record.status in {"failed", "incompatible"} or record.id is None or record.manifest is None:
            if record.status in {"failed", "incompatible"}:
                _mark_unhealthy(record, record.code)
            update_record(record)
            continue
        if row is None:
            _mark_quiet(record, "discovered")
            update_record(record)
            continue
        if record.version != row.installed_version:
            logger.warning(
                "plugin version mismatch",
                extra={
                    "plugin_id": record.id,
                    "installed_version": row.installed_version,
                    "manifest_version": record.version,
                },
            )
            _mark_failed(record, "plugin_version_mismatch")
            _warn_reconcile_failure(record.id, "plugin_version_mismatch", error_type="VersionMismatch")
            update_record(record)
            continue
        if row.desired_state == "disabled":
            _drop_instance(record)
            _mark_quiet(record, "disabled")
            update_record(record)
            continue
        if row.desired_state == "installed":
            _mark_quiet(record, "installed")
            update_record(record)
            continue
        resources = tuple(record.resources) or tuple(record.manifest.resources)
        if resources:
            logger.warning(
                "plugin enable refused",
                extra={
                    "plugin_id": record.id,
                    "code": "plugin_isolation_unavailable",
                    "resource_count": len(resources),
                },
            )
            logger.debug(
                "plugin enable refused resources",
                extra={"plugin_id": record.id, "resources": list(resources)},
            )
            _mark_failed(record, "plugin_isolation_unavailable")
            _warn_reconcile_failure(record.id, "plugin_isolation_unavailable", error_type="IsolationRefusal")
            update_record(record)
            continue
        pending[record.id] = record

    cycles = _cycle_ids(pending)
    for plugin_id in sorted(cycles):
        record = pending.pop(plugin_id)
        _mark_failed(record, "plugin_dependency_cycle")
        _warn_reconcile_failure(plugin_id, "plugin_dependency_cycle", error_type="DependencyCycle")
        update_record(record)

    enabled_versions: dict[str, str] = {}
    for plugin_id in _activation_order(pending):
        record = pending[plugin_id]
        row = rows[plugin_id]
        blocked = _direct_dependency_code(record, enabled_versions)
        if blocked:
            _mark_failed(record, blocked)
            _warn_reconcile_failure(plugin_id, blocked, error_type="DependencyMissing")
            update_record(record)
            continue
        _attempt_register(record, row, from_state=record.status or "discovered", reconcile=True)
        if record.status == "enabled" and record.version:
            enabled_versions[plugin_id] = record.version
    logger.info("plugin reconcile finished", extra={"plugin_count": len(list_records())})
    return public_plugins()


def install_plugin(plugin_id: str) -> PluginPublic:
    """Record desired ``installed`` for this manifest version. Does not import."""
    record = _require_record(plugin_id)
    from_state = record.status
    _refuse_discovery_failure(record)
    row = get_installation(plugin_id)
    manifest = _require_fresh_manifest(record)
    if not accepts_api_compatibility(manifest.api_compatibility):
        raise PluginLifecycleError("plugin_api_incompatible", http_status=409)
    if row is not None and row.installed_version == manifest.version:
        logger.info(
            "plugin lifecycle transition",
            extra={"plugin_id": plugin_id, "from_state": from_state, "to_state": from_state, "code": None},
        )
        return public_plugin(record)
    _drop_instance(record)
    saved = upsert_installation(
        plugin_id=plugin_id,
        installed_version=manifest.version,
        desired_state="installed",
        config=row.config if row is not None else {},
        last_error_code=None,
    )
    _apply_manifest(record, manifest)
    _mark_quiet(record, "installed")
    _attach_install_meta(record, saved)
    update_record(record)
    logger.info(
        "plugin lifecycle transition",
        extra={"plugin_id": plugin_id, "from_state": from_state, "to_state": "installed", "code": None},
    )
    return public_plugin(record)


def enable_plugin(plugin_id: str) -> PluginPublic:
    """Gate, then register. HTTP 409 leaves the installation row unchanged."""
    record = _require_record(plugin_id)
    from_state = record.status
    _refuse_discovery_failure(record)
    if record.status == "incompatible":
        raise PluginLifecycleError("plugin_api_incompatible", http_status=409)
    row = get_installation(plugin_id)
    if row is None:
        raise PluginLifecycleError("plugin_not_installed", http_status=409)
    manifest = _require_fresh_manifest(record)
    if not accepts_api_compatibility(manifest.api_compatibility):
        raise PluginLifecycleError("plugin_api_incompatible", http_status=409)
    if manifest.version != row.installed_version:
        logger.warning(
            "plugin version mismatch",
            extra={
                "plugin_id": plugin_id,
                "installed_version": row.installed_version,
                "manifest_version": manifest.version,
            },
        )
        raise PluginLifecycleError("plugin_version_mismatch", http_status=409)
    if manifest.resources:
        logger.warning(
            "plugin enable refused",
            extra={
                "plugin_id": plugin_id,
                "code": "plugin_isolation_unavailable",
                "resource_count": len(manifest.resources),
            },
        )
        logger.debug(
            "plugin enable refused resources",
            extra={"plugin_id": plugin_id, "resources": list(manifest.resources)},
        )
        raise PluginLifecycleError("plugin_isolation_unavailable", http_status=409)
    _apply_manifest(record, manifest)
    enabled_versions = {
        item.id: item.version
        for item in list_records()
        if item.status == "enabled" and item.id and item.version and item.id != plugin_id
    }
    manifests = _manifests_by_id()
    manifests[manifest.id] = manifest
    blocked = dependency_block_code(
        record,
        enabled_versions=enabled_versions,
        manifests_by_id=manifests,
    )
    if blocked:
        raise PluginLifecycleError(blocked, http_status=409)
    return _attempt_register(record, row, from_state=from_state, reconcile=False)


def disable_plugin(plugin_id: str) -> PluginPublic:
    """Set desired ``disabled`` and drop this plugin only."""
    record = _require_record(plugin_id)
    row = get_installation(plugin_id)
    if row is None:
        raise PluginLifecycleError("plugin_not_installed", http_status=409)
    from_state = record.status
    _drop_instance(record)
    saved = upsert_installation(
        plugin_id=plugin_id,
        installed_version=row.installed_version,
        desired_state="disabled",
        config=row.config,
        last_error_code=row.last_error_code,
    )
    _mark_quiet(record, "disabled")
    _attach_install_meta(record, saved)
    update_record(record)
    logger.info(
        "plugin lifecycle transition",
        extra={"plugin_id": plugin_id, "from_state": from_state, "to_state": "disabled", "code": None},
    )
    return public_plugin(record)


def save_config(plugin_id: str, document: Any) -> PluginPublic:
    """Store a user override. Does not hot-reload an enabled instance."""
    record = _require_record(plugin_id)
    row = get_installation(plugin_id)
    if row is None:
        raise PluginLifecycleError("plugin_not_installed", http_status=409)
    if not isinstance(document, dict):
        raise PluginLifecycleError("plugin_config_invalid", http_status=422)
    schema = record.configuration_schema
    if record.manifest is not None:
        schema = record.manifest.configuration_schema
    try:
        validated = validate_config(schema, document, plugin_id=plugin_id)
    except PluginError as exc:
        logger.warning(
            "plugin config rejected",
            extra={"plugin_id": plugin_id, "code": "plugin_config_invalid"},
        )
        raise PluginLifecycleError("plugin_config_invalid", http_status=422) from exc
    try:
        saved = upsert_installation(
            plugin_id=plugin_id,
            installed_version=row.installed_version,
            desired_state=row.desired_state,
            config=validated,
            last_error_code=row.last_error_code,
        )
    except PersistenceSecretError as exc:
        logger.warning(
            "plugin config rejected",
            extra={"plugin_id": plugin_id, "code": "persistence_secret_rejected"},
        )
        raise PluginLifecycleError("persistence_secret_rejected", http_status=422) from exc
    _attach_install_meta(record, saved)
    if record.status == "enabled":
        record.message = "config applies on next enable"
    update_record(record)
    logger.info(
        "plugin lifecycle transition",
        extra={
            "plugin_id": plugin_id,
            "from_state": record.status,
            "to_state": record.status,
            "code": None,
            "config_present": record.config_present,
        },
    )
    return public_plugin(record)


def public_plugins() -> list[PluginPublic]:
    """Catalog rows with installation fields copied for the public DTO."""
    rows = {row.plugin_id: row for row in list_installations()}
    publics: list[PluginPublic] = []
    for record in list_records():
        _attach_install_meta(record, rows.get(record.id) if record.id else None)
        update_record(record)
        publics.append(public_plugin(record))
    logger.debug("plugin public list", extra={"plugin_count": len(publics)})
    return publics


def _attempt_register(
    record: PluginRecord,
    row: PluginInstallation,
    *,
    from_state: str,
    reconcile: bool,
) -> PluginPublic:
    plugin_id = record.id or ""
    try:
        merged = _merged_config(record, row.config)
    except PluginError as exc:
        code = exc.code or "plugin_config_invalid"
        saved = _persist_attempt(record, row, code)
        _mark_failed(record, code)
        if saved is not None:
            _attach_install_meta(record, saved)
        update_record(record)
        logger.info(
            "plugin lifecycle transition",
            extra={"plugin_id": plugin_id, "from_state": from_state, "to_state": "failed", "code": code},
        )
        if reconcile:
            _warn_reconcile_failure(plugin_id, code, error_type="PluginError")
        return public_plugin(record)
    activated = activate_plugin(plugin_id, merged)
    if activated.status != "enabled":
        code = activated.code or "plugin_register_failed"
        saved = _persist_attempt(activated, row, code)
        if saved is not None:
            _attach_install_meta(activated, saved)
        logger.info(
            "plugin lifecycle transition",
            extra={"plugin_id": plugin_id, "from_state": from_state, "to_state": "failed", "code": code},
        )
        if reconcile:
            _warn_reconcile_failure(plugin_id, code, error_type="RegisterFailed")
        return public_plugin(activated)
    saved = upsert_installation(
        plugin_id=plugin_id,
        installed_version=row.installed_version,
        desired_state="enabled",
        config=row.config,
        last_error_code=None,
    )
    _attach_install_meta(activated, saved)
    activated.health_status = "healthy"
    activated.health_code = None
    activated.invocation_failure_count = 0
    update_record(activated)
    logger.info(
        "plugin lifecycle transition",
        extra={"plugin_id": plugin_id, "from_state": from_state, "to_state": "enabled", "code": None},
    )
    return public_plugin(activated)


def _merged_config(record: PluginRecord, override: dict[str, Any]) -> dict[str, Any]:
    schema = None
    if record.manifest is not None:
        schema = record.manifest.configuration_schema
    file_doc: dict[str, Any] = {}
    if record.root is not None:
        parsed = read_package_config(record.root, load_plugin_settings())
        if isinstance(parsed, dict):
            file_doc = parsed
    merged = dict(file_doc)
    merged.update(override)
    return validate_config(schema, merged, plugin_id=record.id)


def _persist_attempt(record: PluginRecord, row: PluginInstallation, code: str) -> PluginInstallation | None:
    if record.id is None:
        return None
    return upsert_installation(
        plugin_id=record.id,
        installed_version=row.installed_version,
        desired_state="enabled",
        config=row.config,
        last_error_code=code,
    )


def _direct_dependency_code(record: PluginRecord, enabled_versions: dict[str, str]) -> str | None:
    if record.manifest is None:
        return "plugin_dependency_missing"
    for dependency in record.manifest.dependencies:
        if dependency.id not in enabled_versions:
            return "plugin_dependency_missing"
        if dependency.version is not None and enabled_versions[dependency.id] != dependency.version:
            return "plugin_dependency_missing"
    return None


def _manifests_by_id() -> dict[str, PluginManifestV1]:
    found: dict[str, PluginManifestV1] = {}
    for record in list_records():
        if record.id and record.manifest is not None:
            found[record.id] = record.manifest
    return found


def _require_record(plugin_id: str) -> PluginRecord:
    if plugin_id.startswith("plugin:"):
        raise PluginLifecycleError("plugin_not_found", http_status=404)
    record = get_record(plugin_id)
    if record is None:
        raise PluginLifecycleError("plugin_not_found", http_status=404)
    return record


def _refuse_discovery_failure(record: PluginRecord) -> None:
    if record.status == "incompatible":
        raise PluginLifecycleError("plugin_api_incompatible", http_status=409)
    if record.status == "failed" and record.code in _DISCOVERY_REFUSAL:
        raise PluginLifecycleError(record.code or "plugin_manifest_invalid", http_status=409)


def _require_fresh_manifest(record: PluginRecord) -> PluginManifestV1:
    if record.root is None:
        raise PluginLifecycleError(record.code or "plugin_manifest_invalid", http_status=409)
    try:
        return reread_manifest(record.root, load_plugin_settings())
    except PluginError as exc:
        raise PluginLifecycleError(exc.code, http_status=409) from exc


def _apply_manifest(record: PluginRecord, manifest: PluginManifestV1) -> None:
    record.manifest = manifest
    record.version = manifest.version
    record.name = manifest.name
    record.api_compatibility = manifest.api_compatibility
    record.category = manifest.category
    record.capabilities = tuple(manifest.capabilities)
    record.dependency_ids = tuple(item.id for item in manifest.dependencies)
    record.configuration_schema = manifest.configuration_schema
    record.resources = tuple(manifest.resources)
    module_stem, attr = manifest.entry.split(":", 1)
    record.entry_module = module_stem
    record.entry_attr = attr


def _attach_install_meta(record: PluginRecord, row: PluginInstallation | None) -> None:
    if row is None or record.status == "discovered":
        record.installed_version = None
        record.config_present = False
        return
    record.installed_version = row.installed_version
    record.config_present = bool(row.config)


def _mark_quiet(record: PluginRecord, status: str) -> None:
    record.status = status
    record.code = None
    record.message = status
    record.health_status = "unknown"
    record.health_code = None


def _mark_failed(record: PluginRecord, code: str) -> None:
    record.status = "failed"
    record.code = code
    record.message = code
    record.instance = None
    _mark_unhealthy(record, code)


def _mark_unhealthy(record: PluginRecord, code: str | None) -> None:
    record.health_status = "unhealthy"
    record.health_code = code


def _drop_instance(record: PluginRecord) -> None:
    if record.module_name:
        drop_private_module(record.module_name)
    record.instance = None
    record.module_name = None


def _warn_reconcile_failure(plugin_id: str, code: str, *, error_type: str) -> None:
    logger.warning(
        "plugin startup reconcile failed",
        extra={"plugin_id": plugin_id, "error_type": error_type, "code": code},
    )
    logger.debug(
        "plugin startup reconcile failed detail",
        extra={"plugin_id": plugin_id, "code": code, "error_type": error_type},
    )
