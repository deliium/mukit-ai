"""Model spans, call ceilings, and mid-stream cancellation."""

from __future__ import annotations

import asyncio
import logging
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.operation_trace import (
    OperationBudgetExceeded,
    OperationCancelled,
    build_summary,
    mark_run_cancelled,
    operation_span,
    reset_operation_trace_for_tests,
)
from app.services.llm_chat_client import ainvoke_chat_text


def setup_function() -> None:
    reset_operation_trace_for_tests()


def test_reserve_ceiling_refuses_the_next_provider_call(monkeypatch) -> None:
    monkeypatch.setenv("OPERATION_MAX_MODEL_CALLS", "1")

    async def _body() -> None:
        async with operation_span("run", run_id=str(uuid.uuid4())) as run:
            from app.operation_trace import reserve_model_call

            assert reserve_model_call() is True

            async def _gen(_prompt):
                raise AssertionError("provider I/O must not start")
                yield  # pragma: no cover

            client = MagicMock()
            client.astream = _gen
            with pytest.raises(OperationBudgetExceeded) as exc_info:
                await ainvoke_chat_text(client, "prompt", purpose="unit", model_id="m")
            assert exc_info.value.budget_code == "operation_model_call_budget"
            assert run.budget_code == "operation_model_call_budget"
            assert run.model_call_count == 1

    asyncio.run(_body())


def test_stream_usage_metadata_lands_on_the_span() -> None:
    async def _gen(_prompt):
        yield SimpleNamespace(
            content="ok",
            usage_metadata={"input_tokens": 4, "output_tokens": 2},
        )

    client = MagicMock()
    client.astream = _gen

    async def _body() -> None:
        async with operation_span("run", run_id=str(uuid.uuid4())) as run:
            output = await ainvoke_chat_text(
                client,
                "prompt",
                purpose="unit",
                model_id="demo-model",
                runtime="openai_compatible_chat",
            )
            assert output == "ok"
            summary = build_summary(run)
        assert summary.prompt_tokens == 4
        assert summary.completion_tokens == 2
        assert summary.usage_status == "available"
        assert summary.model_call_count == 1
        assert summary.local_inference_ms is None
        model = next(child for child in run.children if child.kind == "model")
        assert model.model_id == "demo-model"
        assert model.runtime == "openai_compatible_chat"

    asyncio.run(_body())


def test_cancel_between_chunks_does_not_log_chunk_text(caplog) -> None:
    run_id = str(uuid.uuid4())

    async def _gen(_prompt):
        yield SimpleNamespace(content="SECRET-CHUNK-BODY")
        mark_run_cancelled(run_id)
        yield SimpleNamespace(content="MORE-SECRET")

    client = MagicMock()
    client.astream = _gen

    async def _body() -> None:
        async with operation_span("run", run_id=run_id):
            with pytest.raises(OperationCancelled):
                await ainvoke_chat_text(client, "prompt", purpose="unit", model_id="m")

    caplog.set_level(logging.DEBUG)
    asyncio.run(_body())
    blob = " ".join(record.getMessage() for record in caplog.records)
    extras = " ".join(
        str(getattr(record, key, ""))
        for record in caplog.records
        for key in ("error_detail", "failure_code", "chunk")
    )
    assert "SECRET-CHUNK-BODY" not in blob
    assert "SECRET-CHUNK-BODY" not in extras
    assert "MORE-SECRET" not in blob
