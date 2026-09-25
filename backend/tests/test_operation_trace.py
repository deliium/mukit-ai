"""Unit tests for operation spans, redaction, and model-call reservation."""

from __future__ import annotations

import ast
import asyncio
import logging
import time
import uuid
from pathlib import Path

from app.operation_budget_settings import load_operation_budget_settings
from app.operation_trace import (
    FORBIDDEN_LOG_FIELD_NAMES,
    adopt_operation_run_id,
    build_summary,
    current_run_id,
    mark_run_cancelled,
    note_failure,
    note_retry,
    note_run_wall_limit,
    note_usage,
    operation_span,
    redact_log_fields,
    render_attempt_decision,
    reserve_model_call,
    reset_operation_trace_for_tests,
)
from app.operation_trace_schemas import OperationSpanV1, OperationSummaryV1
from app.services.persistence_secret_guard import FORBIDDEN_SECRET_FIELD_NAMES

_TRACE_PATH = Path(__file__).resolve().parents[1] / "app" / "operation_trace.py"


def setup_function() -> None:
    reset_operation_trace_for_tests()


def test_secret_field_names_stay_aligned() -> None:
    assert set(FORBIDDEN_LOG_FIELD_NAMES) == set(FORBIDDEN_SECRET_FIELD_NAMES)


def test_redact_keeps_token_fields_and_drops_secrets() -> None:
    payload = redact_log_fields(
        {
            "prompt_tokens": 3,
            "completion_tokens": 4,
            "max_prompt_tokens": 8,
            "api_key": "sk-secret",
            "openai_api_key": "sk-other",
            "vendor_api_key": "hidden",
            "prompt": "do not log",
            "composition": {"tracks": []},
            "long_text": "x" * 300,
            "source_fingerprint": "abcdef1234567890ffff",
            "instructions": "y" * 80,
        }
    )
    assert payload["prompt_tokens"] == 3
    assert payload["completion_tokens"] == 4
    assert payload["max_prompt_tokens"] == 8
    assert "api_key" not in payload
    assert "openai_api_key" not in payload
    assert "vendor_api_key" not in payload
    assert "prompt" not in payload
    assert "composition" not in payload
    assert payload["long_text"] == {"redacted": True, "length": 300}
    assert payload["source_fingerprint"] == "abcdef123456"
    assert payload["instructions_len"] == 80
    assert "instructions" not in payload


def test_operation_trace_imports_no_forbidden_layers() -> None:
    tree = ast.parse(_TRACE_PATH.read_text(encoding="utf-8"))
    forbidden = ("app.ai_agents", "app.ai_runtime", "app.services", "app.db", "fastapi")
    for node in ast.walk(tree):
        module = ""
        if isinstance(node, ast.ImportFrom) and node.module:
            module = node.module
        elif isinstance(node, ast.Import):
            module = ".".join(alias.name for alias in node.names)
        assert not module.startswith(forbidden)


def test_span_schema_forbids_prompt_fields() -> None:
    span = OperationSpanV1(
        run_id=str(uuid.uuid4()),
        span_id=str(uuid.uuid4()),
        kind="run",
        status="ok",
        duration_ms=1,
    )
    dumped = span.model_dump()
    assert dumped["schema_version"] == "operation.span.v1"
    assert "prompt" not in dumped


def test_nested_spans_share_run_and_parent() -> None:
    run_id = str(uuid.uuid4())

    async def _body() -> OperationSummaryV1:
        async with operation_span("run", run_id=run_id) as run:
            assert current_run_id() == run_id
            async with operation_span("agent", agent_id="critic") as agent:
                assert agent.run_id == run.run_id
                assert agent.parent_span_id == run.span_id
                note_usage(prompt_tokens=5, completion_tokens=2, usage_status="available")
                note_retry()
            note_failure("agent_run_failed", error_type="RuntimeError")
        return build_summary(run)

    summary = asyncio.run(_body())
    assert summary.run_id == run_id
    assert summary.schema_version == "operation.summary.v1"
    assert summary.prompt_tokens == 5
    assert summary.completion_tokens == 2
    assert summary.retry_count == 1
    assert summary.failure_count >= 1
    assert summary.status == "failed"


def test_reserve_model_call_trips_ceiling(monkeypatch, caplog) -> None:
    monkeypatch.setenv("OPERATION_MAX_MODEL_CALLS", "1")
    run_id = str(uuid.uuid4())

    async def _body() -> None:
        async with operation_span("run", run_id=run_id) as run:
            assert reserve_model_call() is True
            with caplog.at_level(logging.WARNING):
                assert reserve_model_call() is False
            assert run.budget_code == "operation_model_call_budget"
            assert run.model_call_count == 1
            summary = build_summary(run)
            assert summary.model_call_count == 1
            assert summary.budget_code == "operation_model_call_budget"

    caplog.set_level(logging.WARNING)
    asyncio.run(_body())
    assert any(
        getattr(record, "budget_code", None) == "operation_model_call_budget"
        for record in caplog.records
    )


def test_reserve_without_run_is_allowed() -> None:
    assert current_run_id() is None
    assert reserve_model_call() is True


def test_mark_run_cancelled_sets_event_and_caps_the_set() -> None:
    run_id = str(uuid.uuid4())

    async def _body() -> None:
        async with operation_span("run", run_id=run_id) as run:
            assert run.cancel_event is not None
            mark_run_cancelled(run_id)
            assert run.cancel_event.is_set()
            assert reserve_model_call() is False

    asyncio.run(_body())
    for _index in range(300):
        mark_run_cancelled(str(uuid.uuid4()))
    # The helper itself enforces the cap; a later mark still records the new id.
    newest = str(uuid.uuid4())
    mark_run_cancelled(newest)
    from app.operation_trace import _cancelled_runs

    assert len(_cancelled_runs) <= 256
    assert newest in _cancelled_runs


def test_span_close_logs_redacted_record(caplog) -> None:
    caplog.set_level(logging.INFO)

    async def _body() -> None:
        async with operation_span("run", run_id=str(uuid.uuid4())):
            return None

    asyncio.run(_body())
    closed = [record for record in caplog.records if record.getMessage() == "Operation span closed"]
    assert closed
    assert closed[-1].operation_trace is True
    assert not hasattr(closed[-1], "composition")
    assert not hasattr(closed[-1], "api_key")


def test_adopt_operation_run_id_rejects_garbage() -> None:
    minted = adopt_operation_run_id(None)
    uuid.UUID(minted)
    canonical = adopt_operation_run_id(minted)
    assert canonical == minted
    try:
        adopt_operation_run_id("not-a-uuid")
    except ValueError as exc:
        assert exc.code == "operation_run_id_invalid"  # type: ignore[attr-defined]
    else:
        raise AssertionError("expected invalid run id")


def test_span_close_samples_rss_and_skips_gpu_without_torch(monkeypatch) -> None:
    import sys

    monkeypatch.delitem(sys.modules, "torch", raising=False)

    async def _body() -> None:
        async with operation_span("run", run_id=str(uuid.uuid4())) as run:
            pass
        assert run.memory_status == "available"
        assert isinstance(run.process_rss_kb, int)
        assert run.process_rss_kb >= 0
        assert isinstance(run.process_rss_delta_kb, int)
        assert run.gpu_allocated_bytes is None

    asyncio.run(_body())


def test_rss_sampling_failure_stays_null(monkeypatch, caplog) -> None:
    def _boom(*_args, **_kwargs):
        raise OSError("rss-unavailable")

    monkeypatch.setattr("app.operation_trace.resource.getrusage", _boom)
    caplog.set_level(logging.DEBUG)

    async def _body() -> None:
        async with operation_span("run", run_id=str(uuid.uuid4())) as run:
            pass
        assert run.process_rss_kb is None
        assert run.memory_status == "unavailable"

    asyncio.run(_body())
    assert any(getattr(record, "error_type", None) == "OSError" for record in caplog.records)


def test_gpu_sampled_only_from_already_imported_torch(monkeypatch) -> None:
    import sys
    from types import SimpleNamespace

    torch = SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: True, memory_allocated=lambda: 4096)
    )
    monkeypatch.setitem(sys.modules, "torch", torch)

    async def _body() -> None:
        async with operation_span("model", model_id="local", runtime="local_openai_compatible") as span:
            pass
        assert span.gpu_allocated_bytes == 4096
        assert span.memory_status == "available"
        assert span.local_inference_ms is not None

    asyncio.run(_body())


def test_render_attempt_stops_when_run_wall_is_exceeded() -> None:
    run_id = str(uuid.uuid4())
    other = str(uuid.uuid4())

    async def _body() -> None:
        async with operation_span("run", run_id=run_id) as span:
            span.started_at = time.perf_counter() - 5
            note_run_wall_limit(1)
            assert render_attempt_decision(run_id, 0, 2) == "runtime"
            assert render_attempt_decision(other, 0, 2) == "go"
            assert render_attempt_decision(None, 0, 2) == "go"

    asyncio.run(_body())
    assert render_attempt_decision(run_id, 0, 2) == "runtime"


def test_failed_span_does_not_publish_rss() -> None:
    async def _body() -> None:
        async with operation_span("agent", agent_id="critic") as span:
            note_failure("agent_run_failed", error_type="RuntimeError")
        assert span.process_rss_kb is None
        assert span.memory_status == "unavailable"
        assert span.status == "failed"

    asyncio.run(_body())
    settings = load_operation_budget_settings({})
    assert settings.max_model_calls == 32
    summary = OperationSummaryV1(
        run_id=str(uuid.uuid4()),
        status="ok",
        duration_ms=0,
    )
    assert summary.model_call_count == 0
    assert summary.usage_status == "unavailable"
