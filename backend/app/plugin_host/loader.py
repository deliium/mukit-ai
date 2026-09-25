"""Directory discovery, dependency order, and activation. The only code path that calls ``register``."""

from __future__ import annotations

import importlib.util
import json
import logging
import time
from pathlib import Path
from typing import Any, Mapping

from app.plugin_sdk.context import PluginContext, PluginLogger
from app.plugin_sdk.errors import PluginError
from app.plugin_sdk.manifest import (
    CATEGORY_METHOD,
    PluginManifestV1,
    parse_manifest,
    peek_manifest_id,
)
from app.plugin_sdk.version import PLUGIN_API_VERSION, accepts_api_compatibility

from .catalog import PluginRecord, list_records, replace_catalog, update_record
from .import_guard import drop_private_module, plugin_import_guard, scan_plugin_sources
from .settings import PluginSettings, load_plugin_settings

logger = logging.getLogger(__name__)

_MANIFEST_NAME = "plugin.manifest.json"
_CONFIG_NAME = "config.json"


def load_plugins(env: Mapping[str, str] | None = None) -> list[PluginRecord]:
    """Scan ``PLUGIN_PATHS``, replace the catalog, and return the snapshot.

    A bad plugin is recorded and skipped. Built-in models are untouched here.
    """
    settings = load_plugin_settings(env)
    logger.info("plugin load start", extra={"path_count": settings.path_count, "enabled": settings.enabled})
    for previous in list_records():
        if previous.module_name:
            drop_private_module(previous.module_name)
    if not settings.enabled:
        replace_catalog([])
        logger.info("plugins discovered", extra={"enabled_count": 0, "plugin_count": 0, "path_count": settings.path_count})
        return []

    roots = discover_plugin_roots(settings)
    records: list[PluginRecord] = []
    for index, root in enumerate(roots):
        if index >= settings.max_count:
            records.append(
                _failure(
                    None,
                    status="failed",
                    code="plugin_limit_exceeded",
                    root=root,
                )
            )
            logger.warning("plugin skip", extra={"code": "plugin_limit_exceeded", "plugin_id": None})
            continue
        records.append(_prepare_candidate(root, settings))

    claim_duplicate_ids(records)
    replace_catalog(records)
    enabled_count = sum(1 for item in records if item.status == "enabled")
    logger.info(
        "plugins discovered",
        extra={"enabled_count": enabled_count, "plugin_count": len(records), "path_count": settings.path_count},
    )
    return list(records)


def discover_plugin_roots(settings: PluginSettings) -> list[Path]:
    """Plugin directories in stable path order. Symlinks that leave an entry are skipped."""
    roots: list[Path] = []
    for raw in settings.paths:
        entry = Path(raw)
        try:
            if not entry.is_dir():
                logger.warning("plugin path entry skipped", extra={"code": "plugin_manifest_invalid", "plugin_id": None})
                continue
            entry_resolved = entry.resolve()
        except OSError:
            logger.warning("plugin path entry skipped", extra={"code": "plugin_manifest_invalid", "plugin_id": None})
            continue
        if not entry_resolved.is_dir():
            continue
        if _has_manifest(entry):
            if _confined(entry, entry):
                roots.append(entry_resolved)
            else:
                logger.warning("plugin skip", extra={"code": "plugin_manifest_invalid", "plugin_id": None})
            continue
        try:
            children = sorted(entry.iterdir(), key=lambda path: path.name)
        except OSError:
            logger.warning("plugin path entry skipped", extra={"code": "plugin_manifest_invalid", "plugin_id": None})
            continue
        for child in children:
            try:
                looks_dir = child.is_dir() or child.is_symlink()
            except OSError:
                continue
            if not looks_dir or not _has_manifest(child):
                continue
            if not _confined(child, entry):
                logger.warning("plugin skip", extra={"code": "plugin_manifest_invalid", "plugin_id": None})
                continue
            try:
                resolved = child.resolve()
            except OSError:
                logger.warning("plugin skip", extra={"code": "plugin_manifest_invalid", "plugin_id": None})
                continue
            if resolved.is_dir():
                roots.append(resolved)
    ordered: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(root)
    logger.debug("plugin directories resolved", extra={"directory_count": len(ordered)})
    return ordered


def _prepare_candidate(root: Path, settings: PluginSettings) -> PluginRecord:
    manifest_path = root / _MANIFEST_NAME
    try:
        raw = manifest_path.read_bytes()
    except OSError:
        logger.warning("plugin skip", extra={"code": "plugin_manifest_invalid", "plugin_id": None})
        return _failure(None, status="failed", code="plugin_manifest_invalid", root=root)
    if len(raw) > settings.max_manifest_bytes:
        logger.warning("plugin skip", extra={"code": "plugin_manifest_invalid", "plugin_id": None})
        return _failure(None, status="failed", code="plugin_manifest_invalid", root=root)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        logger.warning("plugin skip", extra={"code": "plugin_manifest_invalid", "plugin_id": None})
        return _failure(None, status="failed", code="plugin_manifest_invalid", root=root)
    plugin_id = peek_manifest_id(data)
    try:
        manifest = parse_manifest(data, source_name=manifest_path.name)
    except PluginError as exc:
        return _failure(plugin_id, status="failed", code=exc.code, root=root)
    if not accepts_api_compatibility(manifest.api_compatibility):
        record = _from_manifest(manifest, root, status="incompatible", code="plugin_api_incompatible")
        logger.warning(
            "plugin skip",
            extra={"code": "plugin_api_incompatible", "plugin_id": manifest.id},
        )
        _log_discovered(record)
        return record
    # Package config.json is merged at enable time. A missing or invalid file
    # stays discovered until an explicit enable attempt.
    record = _from_manifest(manifest, root, status="discovered", code=None, config={})
    _log_discovered(record)
    return record


def _log_discovered(record: PluginRecord) -> None:
    logger.info(
        "plugin discovered",
        extra={
            "plugin_id": record.id,
            "version": record.version,
            "lifecycle": record.status,
        },
    )


def claim_duplicate_ids(records: list[PluginRecord]) -> dict[str, PluginRecord]:
    """Keep the first discovered record per id. Later copies are ``failed``.

    Dependency and cycle checks run at enable time, not during discovery.
    """
    eligible: dict[str, PluginRecord] = {}
    for record in records:
        if record.status != "discovered" or record.id is None or record.manifest is None:
            continue
        if record.id in eligible:
            _mark(record, status="failed", code="plugin_duplicate_id")
            record.health_status = "unhealthy"
            record.health_code = "plugin_duplicate_id"
            logger.warning("plugin skip", extra={"code": "plugin_duplicate_id", "plugin_id": record.id})
            continue
        eligible[record.id] = record
    return eligible


def dependency_block_code(
    record: PluginRecord,
    *,
    enabled_versions: Mapping[str, str],
    manifests_by_id: Mapping[str, PluginManifestV1],
) -> str | None:
    """Return a dependency code when ``record`` cannot be enabled yet.

    Version pins compare ``dependency.version`` to the enabled plugin's manifest
    version. A cycle through plugins that are not already enabled is
    ``plugin_dependency_cycle``. A direct dependency that is not enabled is
    ``plugin_dependency_missing``.
    """
    if record.manifest is None or record.id is None:
        return "plugin_dependency_missing"
    if _unsatisfied_cycle(record.id, enabled_versions, manifests_by_id):
        return "plugin_dependency_cycle"
    for dependency in record.manifest.dependencies:
        if dependency.id not in enabled_versions:
            return "plugin_dependency_missing"
        if dependency.version is not None and enabled_versions[dependency.id] != dependency.version:
            return "plugin_dependency_missing"
    return None


def _unsatisfied_cycle(
    start: str,
    enabled_versions: Mapping[str, str],
    manifests_by_id: Mapping[str, PluginManifestV1],
) -> bool:
    stack: list[str] = []

    def walk(plugin_id: str) -> bool:
        if plugin_id in stack:
            return start in stack
        if plugin_id in enabled_versions and plugin_id != start:
            return False
        manifest = manifests_by_id.get(plugin_id)
        if manifest is None:
            return False
        stack.append(plugin_id)
        for dependency in manifest.dependencies:
            if walk(dependency.id):
                return True
        stack.pop()
        return False

    return walk(start)


def _cycle_ids(eligible: dict[str, PluginRecord]) -> set[str]:
    color = {plugin_id: 0 for plugin_id in eligible}
    cycles: set[str] = set()

    def visit(plugin_id: str, stack: list[str]) -> None:
        color[plugin_id] = 1
        stack.append(plugin_id)
        record = eligible[plugin_id]
        deps = () if record.manifest is None else tuple(dep.id for dep in record.manifest.dependencies)
        for dep in deps:
            if dep not in color:
                continue
            if color[dep] == 1:
                cycles.update(stack[stack.index(dep) :])
            elif color[dep] == 0:
                visit(dep, stack)
        stack.pop()
        color[plugin_id] = 2

    for plugin_id in sorted(eligible):
        if color[plugin_id] == 0:
            visit(plugin_id, [])
    return cycles


def _activation_order(eligible: dict[str, PluginRecord]) -> list[str]:
    indegree = {plugin_id: 0 for plugin_id in eligible}
    dependents: dict[str, list[str]] = {plugin_id: [] for plugin_id in eligible}
    for plugin_id, record in eligible.items():
        if record.manifest is None:
            continue
        for dep in record.manifest.dependencies:
            if dep.id in eligible:
                indegree[plugin_id] += 1
                dependents[dep.id].append(plugin_id)
    ready = sorted(plugin_id for plugin_id, degree in indegree.items() if degree == 0)
    order: list[str] = []
    while ready:
        plugin_id = ready.pop(0)
        order.append(plugin_id)
        for child in dependents[plugin_id]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
        ready.sort()
    return order


def _activate(record: PluginRecord) -> None:
    manifest = record.manifest
    if manifest is None or record.root is None or record.id is None:
        _mark(record, status="failed", code="plugin_entry_invalid")
        return
    started = time.perf_counter()
    logger.info("plugin activate start", extra={"plugin_id": manifest.id})
    logger.debug("register start", extra={"plugin_id": manifest.id})
    module_name = f"mukit_plugin_{manifest.id}"
    try:
        violation = scan_plugin_sources(record.root)
        if violation is not None:
            code, module = violation
            _mark(record, status="failed", code=code)
            logger.warning("plugin skip", extra={"code": code, "plugin_id": manifest.id, "imported_name": module})
            return
        module_stem, attr = manifest.entry.split(":", 1)
        entry_path = record.root / f"{module_stem}.py"
        if not entry_path.is_file() or not _confined(entry_path, record.root):
            _mark(record, status="failed", code="plugin_entry_invalid")
            logger.warning("plugin skip", extra={"code": "plugin_entry_invalid", "plugin_id": manifest.id})
            return
        spec = importlib.util.spec_from_file_location(module_name, entry_path)
        if spec is None or spec.loader is None:
            _mark(record, status="failed", code="plugin_entry_invalid")
            logger.warning("plugin skip", extra={"code": "plugin_entry_invalid", "plugin_id": manifest.id})
            return
        module = importlib.util.module_from_spec(spec)
        import sys

        sys.modules[module_name] = module
        record.module_name = module_name
        with plugin_import_guard(module_name) as guard:
            spec.loader.exec_module(module)
            register = getattr(module, attr, None)
            if not callable(register):
                raise PluginError("plugin_entry_invalid", "plugin_entry_invalid")
            ctx = PluginContext(
                api_version=PLUGIN_API_VERSION,
                plugin_id=manifest.id,
                config=dict(record.config),
                logger=PluginLogger(manifest.id),
            )
            instance = register(ctx)
            method_name = CATEGORY_METHOD[manifest.category]
            method = getattr(instance, method_name, None)
            if not callable(method):
                raise PluginError("plugin_entry_invalid", "plugin_entry_invalid")
            if guard.violation is not None:
                raise PluginError("plugin_forbidden_import", "plugin_forbidden_import")
        record.instance = instance
        record.status = "enabled"
        record.code = None
        record.message = "enabled"
        record.health_status = "healthy"
        record.health_code = None
        record.invocation_failure_count = 0
        logger.debug("register end", extra={"plugin_id": manifest.id, "elapsed_ms": _elapsed_ms(started)})
    except ImportError as exc:
        drop_private_module(module_name)
        record.instance = None
        record.module_name = None
        _mark(record, status="failed", code="plugin_forbidden_import")
        logger.warning(
            "plugin skip",
            extra={"code": "plugin_forbidden_import", "plugin_id": manifest.id, "imported_name": str(exc)},
        )
        logger.debug("register end", extra={"plugin_id": manifest.id, "elapsed_ms": _elapsed_ms(started)})
    except PluginError as exc:
        drop_private_module(module_name)
        record.instance = None
        record.module_name = None
        _mark(record, status="failed", code=exc.code)
        if exc.code == "plugin_register_failed":
            logger.error("plugin_register_failed", extra={"plugin_id": manifest.id, "error_type": "PluginError"})
        logger.debug("register end", extra={"plugin_id": manifest.id, "elapsed_ms": _elapsed_ms(started)})
        logger.warning("plugin skip", extra={"code": exc.code, "plugin_id": manifest.id})
    except SystemExit as exc:
        drop_private_module(module_name)
        record.instance = None
        record.module_name = None
        _mark(record, status="failed", code="plugin_register_failed")
        logger.warning(
            "plugin crashed",
            extra={"plugin_id": manifest.id, "error_type": type(exc).__name__, "code": "plugin_register_failed"},
        )
        logger.debug(
            "plugin crashed stack",
            extra={"plugin_id": manifest.id, "code": "plugin_register_failed"},
            exc_info=True,
        )
        logger.debug("register end", extra={"plugin_id": manifest.id, "elapsed_ms": _elapsed_ms(started)})
    except Exception as exc:
        drop_private_module(module_name)
        record.instance = None
        record.module_name = None
        _mark(record, status="failed", code="plugin_register_failed")
        logger.warning(
            "plugin crashed",
            extra={"plugin_id": manifest.id, "error_type": type(exc).__name__, "code": "plugin_register_failed"},
        )
        logger.error(
            "plugin_register_failed",
            extra={"plugin_id": manifest.id, "error_type": type(exc).__name__},
        )
        logger.debug(
            "plugin crashed stack",
            extra={"plugin_id": manifest.id, "code": "plugin_register_failed"},
            exc_info=True,
        )
        logger.debug("register end", extra={"plugin_id": manifest.id, "elapsed_ms": _elapsed_ms(started)})
    finally:
        logger.info(
            "plugin activate finished",
            extra={"plugin_id": manifest.id, "elapsed_ms": _elapsed_ms(started), "lifecycle": record.status},
        )


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _from_manifest(
    manifest: PluginManifestV1,
    root: Path,
    *,
    status: str,
    code: str | None,
    config: dict[str, Any] | None = None,
) -> PluginRecord:
    module_stem, attr = manifest.entry.split(":", 1)
    unhealthy = status in {"failed", "incompatible"}
    return PluginRecord(
        id=manifest.id,
        status=status,
        code=code,
        message=code or status,
        name=manifest.name,
        version=manifest.version,
        api_compatibility=manifest.api_compatibility,
        category=manifest.category,
        capabilities=tuple(manifest.capabilities),
        dependency_ids=tuple(item.id for item in manifest.dependencies),
        configuration_schema=manifest.configuration_schema,
        config=dict(config or {}),
        manifest=manifest,
        root=root,
        entry_module=module_stem,
        entry_attr=attr,
        resources=tuple(manifest.resources),
        health_status="unhealthy" if unhealthy else "unknown",
        health_code=code if unhealthy else None,
    )


def _failure(
    plugin_id: str | None,
    *,
    status: str,
    code: str,
    root: Path | None,
) -> PluginRecord:
    unhealthy = status in {"failed", "incompatible"}
    return PluginRecord(
        id=plugin_id,
        status=status,
        code=code,
        message=code,
        root=root,
        health_status="unhealthy" if unhealthy else "unknown",
        health_code=code if unhealthy else None,
    )


def _mark(record: PluginRecord, *, status: str, code: str) -> None:
    record.status = status
    record.code = code
    record.message = code
    record.instance = None
    if status in {"failed", "incompatible"}:
        record.health_status = "unhealthy"
        record.health_code = code
    elif status in {"discovered", "installed", "disabled"}:
        record.health_status = "unknown"
        record.health_code = None


def activate_loaded_plugin(record: PluginRecord, config: Mapping[str, Any]) -> PluginRecord:
    """Import and ``register()`` one already-discovered plugin. Host does not read SQLite."""
    record.config = dict(config)
    _activate(record)
    update_record(record)
    return record


def read_package_config(root: Path, settings: PluginSettings) -> dict[str, Any] | None:
    """Return parsed ``config.json`` or None when the file is absent.

    Invalid JSON raises ``PluginError``. Values are not logged.
    """
    config_path = root / _CONFIG_NAME
    if not config_path.is_file():
        return None
    try:
        raw = config_path.read_bytes()
    except OSError as exc:
        raise PluginError("plugin_config_invalid", "plugin_config_invalid") from exc
    if len(raw) > settings.max_manifest_bytes:
        raise PluginError("plugin_config_invalid", "plugin_config_invalid")
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PluginError("plugin_config_invalid", "plugin_config_invalid") from exc
    if not isinstance(document, dict):
        raise PluginError("plugin_config_invalid", "plugin_config_invalid")
    return document


def reread_manifest(root: Path, settings: PluginSettings) -> PluginManifestV1:
    """Parse the on-disk manifest again. Does not import plugin code."""
    manifest_path = root / _MANIFEST_NAME
    try:
        raw = manifest_path.read_bytes()
    except OSError as exc:
        raise PluginError("plugin_manifest_invalid", "plugin_manifest_invalid") from exc
    if len(raw) > settings.max_manifest_bytes:
        raise PluginError("plugin_manifest_invalid", "plugin_manifest_invalid")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PluginError("plugin_manifest_invalid", "plugin_manifest_invalid") from exc
    return parse_manifest(data, source_name=manifest_path.name)


def _has_manifest(path: Path) -> bool:
    try:
        return (path / _MANIFEST_NAME).is_file()
    except OSError:
        return False


def _confined(candidate: Path, entry: Path) -> bool:
    try:
        resolved = candidate.resolve()
        parent = entry.resolve()
    except OSError:
        return False
    if not resolved.exists():
        return False
    if resolved == parent:
        return True
    return _is_relative_to(resolved, parent)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
