"""Register completed Model Lab experiments as symbolic composers.

A missing table or a database that cannot be opened logs a warning and
returns no descriptors. Incomplete or unregistered rows stay out.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Mapping

from app.db.connection import get_project_db_path

from ..capabilities import ModelCapability
from ..operations import AiOperation
from ..types import ModelDescriptor, ModelHealth

logger = logging.getLogger(__name__)

_READY_SQL = """
SELECT id, display_name, registry_model_id, engine, registered_checkpoint_step, status
FROM model_lab_experiments
WHERE status = 'complete'
  AND registry_model_id IS NOT NULL
  AND registered_checkpoint_step IS NOT NULL
"""


def model_lab_descriptors(
    env: Mapping[str, str] | None = None,
) -> list[ModelDescriptor]:
    """One ready descriptor per registered Lab experiment."""
    source = env if env is not None else os.environ
    path = get_project_db_path(source)
    rows = _registered_rows(path)
    descriptors: list[ModelDescriptor] = []
    for row in rows:
        registry_id = str(row["registry_model_id"])
        descriptors.append(
            ModelDescriptor(
                id=registry_id,
                display_name=str(row["display_name"]),
                provider="model_lab",
                runtime="model_lab",
                primary_capability=ModelCapability.SYMBOLIC_COMPOSER,
                locality="local",
                model_version=str(row["id"])[:20],
                supported_operations=(AiOperation.GENERATE_COMPOSER,),
                status="ready",
                health=ModelHealth(
                    status="ready",
                    detail="model_lab_experiment",
                    credentials_present=True,
                ),
                limits={
                    "engine": str(row["engine"]),
                    "experiment_id": str(row["id"]),
                    "checkpoint_step": int(row["registered_checkpoint_step"]),
                },
            )
        )
        logger.info(
            "Model Lab descriptor ready",
            extra={"model_id": registry_id, "status": "ready"},
        )
    return descriptors


def _registered_rows(path: Path) -> list[sqlite3.Row]:
    if not path.is_file():
        logger.warning(
            "Model Lab registry skipped",
            extra={"code": "model_lab_table_unavailable", "basename": path.name},
        )
        return []
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        logger.warning(
            "Model Lab registry skipped",
            extra={"code": "model_lab_table_unavailable", "error_type": type(exc).__name__},
        )
        return []
    try:
        connection.row_factory = sqlite3.Row
        return list(connection.execute(_READY_SQL))
    except sqlite3.Error as exc:
        logger.warning(
            "Model Lab registry skipped",
            extra={"code": "model_lab_table_unavailable", "error_type": type(exc).__name__},
        )
        return []
    finally:
        connection.close()
