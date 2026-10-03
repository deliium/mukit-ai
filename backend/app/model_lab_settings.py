"""``MODEL_LAB_*`` limits for the Model Lab research control plane.

The Lab root holds Music Transformer experiment directories. It is not
``DATASET_ROOT`` and it is not the project database. This module does not
open SQLite. Runtime verbosity stays controlled by ``LOG_LEVEL``.
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
_DEFAULT_ROOT = "experiments/music_transformer"
_DEFAULT_MAX_STEPS = 64
_HARD_MAX_STEPS = 512
_DEFAULT_MAX_BATCH = 8
_HARD_MAX_BATCH = 64
_DEFAULT_MAX_CONCURRENT = 1
_DEFAULT_MAX_COMPARE = 8
_HARD_MAX_COMPARE = 8
_DEFAULT_RETENTION = 50
_TRUTHY = frozenset({"1", "true", "yes", "on"})


@dataclass(frozen=True)
class ModelLabSettings:
    """Env-backed Model Lab limits. No secrets and no free-form weight paths."""

    enabled: bool
    fake: bool
    root: Path
    max_steps: int
    max_batch: int
    max_concurrent: int
    max_compare: int
    allow_accelerator: bool
    retention: int


def _env(source: Mapping[str, str], key: str) -> str:
    raw = source.get(key)
    return "" if raw is None else str(raw).strip()


def _truthy(value: str) -> bool:
    return value.strip().lower() in _TRUTHY


def _clamp_int(raw: str, *, default: int, low: int, high: int, field_name: str) -> int:
    if not raw:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        logger.debug(
            "Model Lab setting ignored",
            extra={"field_name": field_name, "code": "model_lab_setting_invalid"},
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


def clamp_lab_steps(steps: int, *, max_steps: int | None = None) -> int:
    """Clamp Lab train steps to configured / hard ceilings."""
    ceiling = int(max_steps) if max_steps is not None else _DEFAULT_MAX_STEPS
    ceiling = min(max(ceiling, 1), _HARD_MAX_STEPS)
    clamped = min(max(int(steps), 1), ceiling)
    if clamped != steps:
        logger.debug(
            "Model Lab steps clamped",
            extra={"field_name": "steps", "code": "model_lab_steps_clamped"},
        )
    return clamped


def clamp_lab_batch(batch_size: int, *, max_batch: int | None = None) -> int:
    """Clamp Lab batch size to configured / hard ceilings."""
    ceiling = int(max_batch) if max_batch is not None else _DEFAULT_MAX_BATCH
    ceiling = min(max(ceiling, 1), _HARD_MAX_BATCH)
    clamped = min(max(int(batch_size), 1), ceiling)
    if clamped != batch_size:
        logger.debug(
            "Model Lab batch clamped",
            extra={"field_name": "batch_size", "code": "model_lab_batch_clamped"},
        )
    return clamped


def clamp_lab_compare_arity(count: int, *, max_compare: int | None = None) -> int:
    """Return the effective compare arity ceiling (does not raise)."""
    ceiling = int(max_compare) if max_compare is not None else _DEFAULT_MAX_COMPARE
    return min(max(ceiling, 2), _HARD_MAX_COMPARE)


def hard_max_steps() -> int:
    return _HARD_MAX_STEPS


def hard_max_batch() -> int:
    return _HARD_MAX_BATCH


def load_model_lab_settings(env: Mapping[str, str] | None = None) -> ModelLabSettings:
    """Resolve settings. Does not create the directory and does not open SQLite."""
    source = env if env is not None else os.environ
    raw_root = _env(source, "MODEL_LAB_ROOT")
    root = Path(raw_root).expanduser() if raw_root else Path(_DEFAULT_ROOT)
    enabled = _truthy(_env(source, "MODEL_LAB_ENABLED"))
    fake = _truthy(_env(source, "MODEL_LAB_FAKE"))
    allow_accelerator = _truthy(_env(source, "MODEL_LAB_ALLOW_ACCELERATOR"))
    max_steps = _clamp_int(
        _env(source, "MODEL_LAB_MAX_STEPS"),
        default=_DEFAULT_MAX_STEPS,
        low=1,
        high=_HARD_MAX_STEPS,
        field_name="MODEL_LAB_MAX_STEPS",
    )
    max_batch = _clamp_int(
        _env(source, "MODEL_LAB_MAX_BATCH"),
        default=_DEFAULT_MAX_BATCH,
        low=1,
        high=_HARD_MAX_BATCH,
        field_name="MODEL_LAB_MAX_BATCH",
    )
    max_concurrent = _clamp_int(
        _env(source, "MODEL_LAB_MAX_CONCURRENT"),
        default=_DEFAULT_MAX_CONCURRENT,
        low=1,
        high=4,
        field_name="MODEL_LAB_MAX_CONCURRENT",
    )
    max_compare = _clamp_int(
        _env(source, "MODEL_LAB_MAX_COMPARE"),
        default=_DEFAULT_MAX_COMPARE,
        low=2,
        high=_HARD_MAX_COMPARE,
        field_name="MODEL_LAB_MAX_COMPARE",
    )
    retention = _clamp_int(
        _env(source, "MODEL_LAB_RETENTION"),
        default=_DEFAULT_RETENTION,
        low=1,
        high=200,
        field_name="MODEL_LAB_RETENTION",
    )
    settings = ModelLabSettings(
        enabled=enabled,
        fake=fake,
        root=root,
        max_steps=max_steps,
        max_batch=max_batch,
        max_concurrent=max_concurrent,
        max_compare=max_compare,
        allow_accelerator=allow_accelerator,
        retention=retention,
    )
    logger.info(
        "Model Lab settings loaded",
        extra={
            "enabled": settings.enabled,
            "fake": settings.fake,
            "max_steps": settings.max_steps,
            "max_batch": settings.max_batch,
            "max_concurrent": settings.max_concurrent,
            "root_basename": settings.root.name,
        },
    )
    return settings


def assert_model_lab_storage_root(
    env: Mapping[str, str] | None = None,
    *,
    root: Path | None = None,
) -> Path:
    """Refuse a root that collides with the dataset or the project database.

    The service calls this before the first experiment write. A rejected root
    raises ``StorageRootError`` and must not insert a row.
    """
    source = env if env is not None else os.environ
    resolved_root = root if root is not None else load_model_lab_settings(source).root
    dataset_root = _resolve_dataset_root(source)
    project_db = _resolve_project_db_path(source)
    logger.debug(
        "Model Lab storage root check",
        extra={"root_basename": resolved_root.name},
    )
    reject_storage_root(resolved_root, dataset_root=dataset_root, project_db=project_db)
    logger.info(
        "Model Lab storage root accepted",
        extra={"root_basename": Path(resolved_root).name},
    )
    return Path(resolved_root)
