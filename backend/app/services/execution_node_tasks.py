"""Process-local controller index: task_id → worker cancel path.

Restart clears the index. In-flight remote tasks are lost — acceptable for v1.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from app.execution_node_schemas import ExecutionNodeError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExecutionTaskIndexEntry:
    node_id: str
    cancel_path: str
    operation_run_id: str | None = None


_lock = threading.Lock()
_INDEX: dict[str, ExecutionTaskIndexEntry] = {}


def register_task(
    task_id: str,
    *,
    node_id: str,
    cancel_path: str,
    operation_run_id: str | None = None,
) -> None:
    entry = ExecutionTaskIndexEntry(
        node_id=node_id,
        cancel_path=cancel_path,
        operation_run_id=operation_run_id,
    )
    with _lock:
        _INDEX[task_id] = entry
    logger.debug(
        "execution task index insert",
        extra={"task_id": task_id, "node_id": node_id},
    )


def get_task(task_id: str) -> ExecutionTaskIndexEntry | None:
    with _lock:
        return _INDEX.get(task_id)


def pop_task(task_id: str) -> ExecutionTaskIndexEntry | None:
    with _lock:
        entry = _INDEX.pop(task_id, None)
    if entry is not None:
        logger.debug(
            "execution task index remove",
            extra={"task_id": task_id, "node_id": entry.node_id},
        )
    return entry


def require_task(task_id: str) -> ExecutionTaskIndexEntry:
    entry = get_task(task_id)
    if entry is None:
        raise ExecutionNodeError(
            "execution_node_task_not_found",
            "Execution task was not found.",
            details={"task_id": task_id},
        )
    return entry


def list_task_ids() -> list[str]:
    with _lock:
        return sorted(_INDEX.keys())


def clear_tasks() -> None:
    with _lock:
        _INDEX.clear()
    logger.debug("execution task index cleared")
