"""Refuse filesystem roots that collide with the dataset or the project database.

Callers pass a resolved-intent directory (recovery assets, neural renders, mix
reports, mix plans, workflow-eval benchmarks, or a SQLite backup). The function
compares resolved paths and logs only a reason code.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_CODE = "storage_root_rejected"


class StorageRootError(RuntimeError):
    """Raised when a storage root must not be used."""

    def __init__(self, reason: str) -> None:
        self.code = _CODE
        self.reason = reason
        super().__init__(_CODE)


def reject_storage_root(
    root: Path,
    *,
    dataset_root: Path | None,
    project_db: Path,
) -> None:
    """Raise ``StorageRootError`` when ``root`` is unsafe.

    Reasons: ``dataset_root``, ``inside_dataset_root``, ``project_db_path``,
    ``contains_project_db``.
    """
    logger.debug(
        "storage_root_check",
        extra={"basename": root.name, "has_dataset_root": dataset_root is not None},
    )
    resolved = _resolve(root)
    reason = _reason(resolved, dataset_root=dataset_root, project_db=project_db)
    if reason is None:
        logger.debug("storage_root_check_ok", extra={"basename": resolved.name})
        return
    logger.warning("storage_root_rejected", extra={"reason": reason})
    raise StorageRootError(reason)


def _reason(
    resolved: Path,
    *,
    dataset_root: Path | None,
    project_db: Path,
) -> str | None:
    if dataset_root is not None:
        dataset_resolved = _resolve(dataset_root)
        if resolved == dataset_resolved:
            return "dataset_root"
        if resolved.is_relative_to(dataset_resolved):
            return "inside_dataset_root"
    db_resolved = _resolve(project_db)
    if resolved == db_resolved:
        return "project_db_path"
    if db_resolved.is_relative_to(resolved):
        return "contains_project_db"
    return None


def _resolve(path: Path) -> Path:
    expanded = path.expanduser()
    try:
        return expanded.resolve()
    except OSError:
        logger.debug("storage_root_resolve_failed", extra={"basename": expanded.name})
        return expanded
