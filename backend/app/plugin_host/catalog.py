"""In-memory plugin catalog. Not project history and not the model registry."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.plugin_sdk.manifest import PluginManifestV1

logger = logging.getLogger(__name__)

_lock = threading.RLock()


@dataclass
class PluginRecord:
    """One discovered plugin, including failures that did not activate."""

    id: str | None
    status: str
    code: str | None
    message: str
    name: str | None = None
    version: str | None = None
    api_compatibility: str | None = None
    category: str | None = None
    capabilities: tuple[str, ...] = ()
    dependency_ids: tuple[str, ...] = ()
    configuration_schema: dict[str, Any] | None = None
    config: dict[str, Any] = field(default_factory=dict)
    manifest: PluginManifestV1 | None = None
    root: Path | None = None
    entry_module: str | None = None
    entry_attr: str | None = None
    instance: Any = None
    module_name: str | None = None


_records: list[PluginRecord] = []


def replace_catalog(records: list[PluginRecord]) -> None:
    """Swap the process catalog. Callers must already have dropped previous modules."""
    global _records
    with _lock:
        _records = list(records)
        logger.debug(
            "plugin catalog replaced",
            extra={
                "plugin_count": len(_records),
                "active_count": sum(1 for item in _records if item.status == "active"),
            },
        )


def list_records() -> list[PluginRecord]:
    with _lock:
        return list(_records)


def get_record(plugin_id: str) -> PluginRecord | None:
    with _lock:
        for record in _records:
            if record.id == plugin_id:
                return record
    return None


def update_record(record: PluginRecord) -> None:
    with _lock:
        for index, current in enumerate(_records):
            if current is record or (record.id is not None and current.id == record.id):
                _records[index] = record
                return


def clear_plugins_for_tests() -> None:
    """Drop catalog rows and private plugin modules. Does not touch built-in models."""
    import sys

    global _records
    with _lock:
        module_names = [record.module_name for record in _records if record.module_name]
        _records = []
    for name in module_names:
        _drop_module(sys.modules, name)
    logger.debug("plugin catalog cleared for tests")


def _drop_module(modules: dict[str, Any], name: str) -> None:
    doomed = [key for key in modules if key == name or key.startswith(f"{name}.")]
    for key in doomed:
        modules.pop(key, None)
