"""LanguageModel adapter that forwards complete_text to a worker ExecutionNode."""

from __future__ import annotations

import logging
import secrets
from typing import Any

import httpx

from app.execution_node_schemas import (
    ExecutionTaskResultV1,
    ExecutionTaskV1,
    parse_execution_node_model_id,
)
from app.execution_node_settings import load_execution_node_settings
from app.operation_trace import is_run_cancelled
from app.services import execution_node_tasks as tasks

from ..errors import ModelUnavailableError
from ..types import ModelDescriptor

logger = logging.getLogger(__name__)

EXECUTION_NODE_RUNTIME = "execution_node"


class ExecutionNodeLanguageModel:
    """LanguageModel for ``runtime=execution_node`` via typed worker HTTP."""

    def __init__(
        self,
        descriptor: ModelDescriptor,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if descriptor.runtime != EXECUTION_NODE_RUNTIME:
            raise ModelUnavailableError(
                f"Runtime mismatch for {descriptor.id}: expected {EXECUTION_NODE_RUNTIME}",
                code="model_unavailable",
            )
        if descriptor.status != "ready":
            raise ModelUnavailableError(
                f"Execution node model is not ready: {descriptor.id}",
                code="model_unavailable",
            )
        self._descriptor = descriptor
        self._http_client = http_client
        node_id = str((descriptor.limits or {}).get("execution_node_id") or "")
        address = str((descriptor.limits or {}).get("execution_node_address") or "")
        if not node_id or not address:
            raise ModelUnavailableError(
                f"Execution node model is missing peer metadata: {descriptor.id}",
                code="model_unavailable",
            )
        self._node_id = node_id
        self._address = address.rstrip("/")
        _, local_id = parse_execution_node_model_id(descriptor.id)
        self._local_model_id = local_id
        logger.info(
            "Constructed execution_node LanguageModel",
            extra={"model_id": descriptor.id, "node_id": node_id},
        )

    @property
    def model_id(self) -> str:
        return self._descriptor.id

    @property
    def descriptor(self) -> ModelDescriptor:
        return self._descriptor

    async def complete_text(self, prompt: str, *, purpose: str | None = None) -> str:
        settings = load_execution_node_settings()
        if not settings.enabled or not settings.token:
            raise ModelUnavailableError(
                "Execution nodes are not configured",
                code="model_unavailable",
            )
        task_id = f"task_{secrets.token_hex(8)}"
        cancel_path = f"/execution/v1/tasks/{task_id}/cancel"
        operation_run_id = None
        try:
            from app.operation_trace import current_run_id

            operation_run_id = current_run_id()
        except Exception:  # noqa: BLE001
            operation_run_id = None

        tasks.register_task(
            task_id,
            node_id=self._node_id,
            cancel_path=cancel_path,
            operation_run_id=operation_run_id,
        )
        if operation_run_id and is_run_cancelled(operation_run_id):
            tasks.pop_task(task_id)
            raise ModelUnavailableError(
                "Operation cancelled before remote complete_text",
                code="operation_cancelled",
            )

        task = ExecutionTaskV1(
            task_id=task_id,
            operation="complete_text",
            model_id=self._local_model_id,
            input_text=prompt,
            purpose=purpose,
            operation_run_id=operation_run_id,
        )
        url = f"{self._address}/execution/v1/complete_text"
        headers = {"Authorization": f"Bearer {settings.token}"}
        logger.debug(
            "ExecutionNodeLanguageModel complete_text start",
            extra={
                "model_id": self.model_id,
                "node_id": self._node_id,
                "task_id": task_id,
                "purpose": purpose,
                "prompt_length": len(prompt or ""),
            },
        )
        owns_client = self._http_client is None
        client = self._http_client or _build_client(self._address)
        try:
            response = await client.post(
                url,
                headers=headers,
                json=task.model_dump(mode="json"),
            )
        except Exception as exc:
            tasks.pop_task(task_id)
            logger.warning(
                "Execution node transport failure",
                extra={
                    "model_id": self.model_id,
                    "node_id": self._node_id,
                    "task_id": task_id,
                    "error_type": type(exc).__name__,
                },
            )
            raise ModelUnavailableError(
                f"Execution node transport failed: {type(exc).__name__}",
                code="model_unavailable",
            ) from exc
        finally:
            if owns_client:
                await client.aclose()

        if response.status_code >= 400:
            tasks.pop_task(task_id)
            raise ModelUnavailableError(
                f"Execution node returned HTTP {response.status_code}",
                code="model_unavailable",
            )
        try:
            result = ExecutionTaskResultV1.model_validate(response.json())
        except Exception as exc:
            tasks.pop_task(task_id)
            raise ModelUnavailableError(
                "Execution node returned an invalid task result",
                code="model_unavailable",
            ) from exc
        tasks.pop_task(task_id)
        if result.status == "cancelled":
            raise ModelUnavailableError(
                "Execution task cancelled",
                code="operation_cancelled",
            )
        if result.status != "completed" or not result.output_text:
            raise ModelUnavailableError(
                f"Execution task failed: {result.failure_code or result.status}",
                code="model_unavailable",
            )
        logger.debug(
            "ExecutionNodeLanguageModel complete_text done",
            extra={
                "model_id": self.model_id,
                "task_id": task_id,
                "latency_ms": result.latency_ms,
                "output_length": len(result.output_text),
            },
        )
        return result.output_text


def _build_client(address: str) -> httpx.AsyncClient:
    if "execution-node.fake" in address:
        from app.services.execution_node_fake import get_fake_worker_asgi_app

        app = get_fake_worker_asgi_app()
        transport = httpx.ASGITransport(app=app)
        return httpx.AsyncClient(transport=transport, base_url=address, timeout=60.0)
    return httpx.AsyncClient(timeout=60.0)


def build_execution_node_language_model(
    descriptor: ModelDescriptor,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> ExecutionNodeLanguageModel:
    return ExecutionNodeLanguageModel(descriptor, http_client=http_client)
