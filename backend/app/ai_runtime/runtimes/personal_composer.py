"""Register completed personal adapters as symbolic composers.

A missing table or a database that cannot be opened logs a warning and
returns no descriptors. This module does not run Alembic and does not
reload the registry.
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
SELECT id, display_name, registry_model_id, engine, snapshot_version, status
FROM personal_composer_adapters
WHERE status = 'complete'
"""


def personal_composer_descriptors(
    env: Mapping[str, str] | None = None,
) -> list[ModelDescriptor]:
    """One ready descriptor per completed adapter. Incomplete rows stay out."""
    source = env if env is not None else os.environ
    path = get_project_db_path(source)
    rows = _complete_rows(path)
    descriptors: list[ModelDescriptor] = []
    for row in rows:
        registry_id = str(row["registry_model_id"])
        descriptors.append(
            ModelDescriptor(
                id=registry_id,
                display_name=str(row["display_name"]),
                provider="personal",
                runtime="personal_composer",
                primary_capability=ModelCapability.SYMBOLIC_COMPOSER,
                locality="local",
                model_version=str(row["snapshot_version"])[:12],
                supported_operations=(AiOperation.GENERATE_COMPOSER,),
                status="ready",
                health=ModelHealth(
                    status="ready",
                    detail="personal_adapter",
                    credentials_present=True,
                ),
                limits={"engine": str(row["engine"]), "adapter_id": str(row["id"])},
            )
        )
        logger.info(
            "Personal composer descriptor ready",
            extra={"model_id": registry_id, "status": "ready"},
        )
    return descriptors


def _complete_rows(path: Path) -> list[sqlite3.Row]:
    if not path.is_file():
        logger.warning(
            "Personal composer registry skipped",
            extra={"code": "personal_table_unavailable", "basename": path.name},
        )
        return []
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        logger.warning(
            "Personal composer registry skipped",
            extra={"code": "personal_table_unavailable", "error_type": type(exc).__name__},
        )
        return []
    try:
        connection.row_factory = sqlite3.Row
        return list(connection.execute(_READY_SQL))
    except sqlite3.Error as exc:
        logger.warning(
            "Personal composer registry skipped",
            extra={"code": "personal_table_unavailable", "error_type": type(exc).__name__},
        )
        return []
    finally:
        connection.close()
