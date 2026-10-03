"""Typed worker inference dispatch. No shell, subprocess, or os.system."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from dataclasses import dataclass, field

from app.execution_node_schemas import (
    ExecutionNodeCatalogV1,
    ExecutionNodeError,
    ExecutionNodeInstalledModelV1,
    ExecutionTaskResultV1,
    ExecutionTaskV1,
)
from app.execution_node_settings import load_execution_node_settings
from app.operation_trace import is_run_cancelled

logger = logging.getLogger(__name__)

_SLOW_ENV = "AI_EXECUTION_NODE_FAKE_SLOW"


@dataclass
class WorkerTaskState:
    cancelled: threading.Event = field(default_factory=threading.Event)
    status: str = "running"


_lock = threading.Lock()
_TASKS: dict[str, WorkerTaskState] = {}
_ACTIVE = 0


def clear_worker_tasks() -> None:
    global _ACTIVE
    with _lock:
        _TASKS.clear()
        _ACTIVE = 0


def cancel_worker_task(task_id: str) -> dict[str, str]:
    with _lock:
        state = _TASKS.get(task_id)
        if state is None:
            raise ExecutionNodeError(
                "execution_node_task_not_found",
                "Execution task was not found.",
                details={"task_id": task_id},
            )
        state.cancelled.set()
        state.status = "cancelled"
    logger.info("Worker task cancelled", extra={"task_id": task_id})
    return {"task_id": task_id, "status": "cancelled"}


def worker_catalog(*, node_id: str) -> ExecutionNodeCatalogV1:
    """Advertise the in-process fake language model for CI / local workers."""
    models = [
        ExecutionNodeInstalledModelV1(
            id="fake:language",
            display_name="Fake language (worker)",
            primary_capability="language_planner",
            runtime="fake",
            locality="local",
            status="ready",
            supported_operations=["generate", "region_edit"],
        )
    ]
    return ExecutionNodeCatalogV1(
        node_id=node_id,
        installed_models=models,
    )


def _worker_node_id() -> str:
    # Stable default for local worker process; registration may override on controller.
    return "node_0123456789abcdef"


async def dispatch_complete_text(task: ExecutionTaskV1) -> ExecutionTaskResultV1:
    """Run one typed complete_text task against local fake/local adapters."""
    global _ACTIVE
    settings = load_execution_node_settings()
    started = time.perf_counter()
    state = WorkerTaskState()
    with _lock:
        if _ACTIVE >= 64:
            raise ExecutionNodeError(
                "execution_node_busy",
                "Worker is at concurrency capacity.",
            )
        _TASKS[task.task_id] = state
        _ACTIVE += 1
    logger.info(
        "Worker task started",
        extra={"task_id": task.task_id, "model_id": task.model_id, "purpose": task.purpose},
    )
    try:
        if task.operation_run_id and is_run_cancelled(task.operation_run_id):
            return ExecutionTaskResultV1(
                task_id=task.task_id,
                status="cancelled",
                failure_code="operation_cancelled",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )

        # Optional slow path for cancel tests (AI_EXECUTION_NODE_FAKE_SLOW=1).
        if settings.fake and (os.environ.get(_SLOW_ENV) or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }:
            for _ in range(100):
                if state.cancelled.is_set():
                    return ExecutionTaskResultV1(
                        task_id=task.task_id,
                        status="cancelled",
                        failure_code="operation_cancelled",
                        latency_ms=int((time.perf_counter() - started) * 1000),
                    )
                if task.operation_run_id and is_run_cancelled(task.operation_run_id):
                    return ExecutionTaskResultV1(
                        task_id=task.task_id,
                        status="cancelled",
                        failure_code="operation_cancelled",
                        latency_ms=int((time.perf_counter() - started) * 1000),
                    )
                await asyncio.sleep(0.05)

        text = await _complete_locally(task)
        if state.cancelled.is_set():
            return ExecutionTaskResultV1(
                task_id=task.task_id,
                status="cancelled",
                failure_code="operation_cancelled",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        latency = int((time.perf_counter() - started) * 1000)
        logger.info(
            "Worker task completed",
            extra={"task_id": task.task_id, "latency_ms": latency},
        )
        return ExecutionTaskResultV1(
            task_id=task.task_id,
            status="completed",
            output_text=text,
            latency_ms=latency,
        )
    except ExecutionNodeError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Worker task failed",
            extra={
                "task_id": task.task_id,
                "failure_code": "execution_node_unavailable",
                "error_type": type(exc).__name__,
            },
        )
        return ExecutionTaskResultV1(
            task_id=task.task_id,
            status="failed",
            failure_code="execution_node_unavailable",
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
    finally:
        with _lock:
            _TASKS.pop(task.task_id, None)
            _ACTIVE = max(0, _ACTIVE - 1)


async def _complete_locally(task: ExecutionTaskV1) -> str:
    """Prefer deterministic fake text; otherwise try LocalLanguageModel."""
    purpose = task.purpose or "complete_text"
    settings = load_execution_node_settings()
    if settings.fake or task.model_id.startswith("fake:"):
        logger.debug(
            "Worker fake complete",
            extra={"task_id": task.task_id, "purpose": purpose},
        )
        return f"fake-execution-node:{purpose}"

    try:
        from app.ai_runtime.bootstrap import get_model_registry
        from app.ai_runtime.runtimes.local_language import LocalLanguageModel

        registry = get_model_registry()
        descriptor = registry.get(task.model_id)
        if descriptor is None:
            raise ExecutionNodeError(
                "execution_node_unavailable",
                "Local model is not registered on the worker.",
                details={"model_id": task.model_id},
            )
        model = LocalLanguageModel(descriptor)
        return await model.complete_text(task.input_text, purpose=purpose)
    except ExecutionNodeError:
        raise
    except Exception as exc:
        raise ExecutionNodeError(
            "execution_node_unavailable",
            "Worker local complete_text failed.",
            details={"error_type": type(exc).__name__},
        ) from exc


def default_worker_node_id() -> str:
    return _worker_node_id()
