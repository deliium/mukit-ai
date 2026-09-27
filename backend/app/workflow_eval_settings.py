"""``EVAL_BENCHMARK_ROOT`` for offline musical workflow evaluation.

Reports stay under the benchmark root. The loader refuses a root that resolves
to ``DATASET_ROOT`` or ``PROJECT_DB_PATH``. The directory is created on first
write, not at import.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from app.storage_root_policy import StorageRootError, reject_storage_root

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_ROOT = _REPO_ROOT / "var" / "workflow-benchmarks"
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_PROJECT_DB_PATH = _BACKEND_ROOT / "data" / "projects.db"


class WorkflowEvalConfigError(RuntimeError):
    """Raised when the benchmark root is not usable."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class WorkflowEvalSettings:
    benchmark_root: Path


def _resolve_project_db_path(env: Mapping[str, str]) -> Path:
    raw = (env.get("PROJECT_DB_PATH") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return _DEFAULT_PROJECT_DB_PATH


def _reject_root(root: Path, *, dataset_root: Path | None, project_db: Path) -> None:
    try:
        reject_storage_root(root, dataset_root=dataset_root, project_db=project_db)
    except StorageRootError as exc:
        logger.warning(
            "benchmark_root_rejected",
            extra={"code": "benchmark_root_rejected", "reason": exc.reason},
        )
        raise WorkflowEvalConfigError("benchmark_root_rejected") from exc
    logger.info(
        "storage_root_accepted",
        extra={"settings": "workflow_eval", "basename": root.name},
    )


def load_workflow_eval_settings(
    env: Mapping[str, str] | None = None,
) -> WorkflowEvalSettings:
    """Resolve ``EVAL_BENCHMARK_ROOT``. Does not create the directory."""
    source = env if env is not None else os.environ
    raw = (source.get("EVAL_BENCHMARK_ROOT") or "").strip()
    root = Path(raw).expanduser() if raw else _DEFAULT_ROOT
    dataset_raw = (source.get("DATASET_ROOT") or "").strip()
    dataset_root = Path(dataset_raw).expanduser() if dataset_raw else None
    project_db = _resolve_project_db_path(source)
    _reject_root(root, dataset_root=dataset_root, project_db=project_db)
    logger.debug(
        "Workflow eval settings loaded",
        extra={"benchmark_root_name": root.name},
    )
    return WorkflowEvalSettings(benchmark_root=root)
