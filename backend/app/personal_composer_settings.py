"""``PERSONAL_COMPOSER_*`` limits.

The adapter root is a private snapshot directory. It is not ``DATASET_ROOT``
and it is not the project database. This module does not open SQLite.
Runtime verbosity stays controlled by ``LOG_LEVEL``.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from app.storage_root_policy import reject_storage_root

logger = logging.getLogger(__name__)

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_PROJECT_DB_PATH = _BACKEND_ROOT / "data" / "projects.db"
_DEFAULT_ROOT_NAME = "personal_composers"
_DEFAULT_MAX_PROJECTS = 8
_DEFAULT_MAX_STEPS = 50
_TRUTHY = frozenset({"1", "true", "yes", "on"})


@dataclass(frozen=True)
class PersonalComposerSettings:
    """Env-backed personal composer limits. No secrets and no weight paths."""

    root: Path
    fake: bool
    max_projects: int
    max_steps: int


def _env(source: Mapping[str, str], key: str) -> str:
    raw = source.get(key)
    return "" if raw is None else str(raw).strip()


def _truthy(value: str) -> bool:
    return value.strip().lower() in _TRUTHY


def _clamp_int(raw: str, *, default: int, low: int, high: int) -> int:
    if not raw:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        logger.debug(
            "Personal composer setting ignored",
            extra={"field_name": "int", "code": "personal_setting_invalid"},
        )
        return default
    if parsed < low:
        return low
    if parsed > high:
        return high
    return parsed


def _resolve_project_db_path(env: Mapping[str, str]) -> Path:
    raw = _env(env, "PROJECT_DB_PATH")
    if raw:
        return Path(raw).expanduser()
    return _DEFAULT_PROJECT_DB_PATH


def _resolve_dataset_root(env: Mapping[str, str]) -> Path | None:
    raw = _env(env, "DATASET_ROOT")
    if not raw:
        return None
    return Path(raw).expanduser()


def load_personal_composer_settings(
    env: Mapping[str, str] | None = None,
) -> PersonalComposerSettings:
    """Resolve settings. Does not create the directory and does not open SQLite."""
    source = env if env is not None else os.environ
    raw_root = _env(source, "PERSONAL_COMPOSER_ROOT")
    root = Path(raw_root).expanduser() if raw_root else Path(_DEFAULT_ROOT_NAME)
    fake = _truthy(_env(source, "PERSONAL_COMPOSER_FAKE"))
    max_projects = _clamp_int(
        _env(source, "PERSONAL_COMPOSER_MAX_PROJECTS"),
        default=_DEFAULT_MAX_PROJECTS,
        low=1,
        high=8,
    )
    max_steps = _clamp_int(
        _env(source, "PERSONAL_COMPOSER_MAX_STEPS"),
        default=_DEFAULT_MAX_STEPS,
        low=1,
        high=50,
    )
    settings = PersonalComposerSettings(
        root=root,
        fake=fake,
        max_projects=max_projects,
        max_steps=max_steps,
    )
    logger.info(
        "Personal composer settings loaded",
        extra={
            "fake": settings.fake,
            "max_projects": settings.max_projects,
            "max_steps": settings.max_steps,
            "root_basename": settings.root.name,
        },
    )
    return settings


def assert_personal_composer_storage_root(
    env: Mapping[str, str] | None = None,
    *,
    root: Path | None = None,
) -> Path:
    """Refuse a root that collides with the dataset or the project database.

    The service calls this before the first write. A rejected root raises
    ``StorageRootError`` and must not insert a row.
    """
    source = env if env is not None else os.environ
    resolved_root = root if root is not None else load_personal_composer_settings(source).root
    dataset_root = _resolve_dataset_root(source)
    project_db = _resolve_project_db_path(source)
    logger.debug(
        "Personal composer storage root check",
        extra={"root_basename": resolved_root.name},
    )
    reject_storage_root(resolved_root, dataset_root=dataset_root, project_db=project_db)
    logger.info(
        "Personal composer storage root accepted",
        extra={"root_basename": Path(resolved_root).name},
    )
    return Path(resolved_root)
