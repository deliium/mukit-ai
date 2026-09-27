"""ContextVar operation spans for one asyncio task.

Imports nothing from ``ai_agents``, ``ai_runtime``, ``services``, ``db``, or FastAPI.
Render workers read the cancelled-run set; they do not see this ContextVar.
"""

from __future__ import annotations

import asyncio
import logging
import resource
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Mapping

from app.operation_budget_settings import (
    BUDGET_COST,
    BUDGET_MODEL_CALL,
    BUDGET_RUNTIME,
    BUDGET_TOKEN,
    load_operation_budget_settings,
)
from app.operation_trace_schemas import OperationSpanV1, OperationSummaryV1

logger = logging.getLogger(__name__)

# Duplicated from persistence_secret_guard.FORBIDDEN_SECRET_FIELD_NAMES.
# Exact-name match only — a substring search would drop prompt_tokens.
FORBIDDEN_LOG_FIELD_NAMES: tuple[str, ...] = (
    "api_key",
    "apikey",
    "openai_api_key",
    "deepseek_api_key",
    "authorization",
    "access_token",
    "secret",
    "password",
    "bearer",
    "token",
    "refresh_token",
)

_DROP_EXACT = frozenset(
    {
        "prompt",
        "messages",
        "composition",
        "events",
        "audio",
        "pcm",
        "wav_bytes",
        "api_key",
    }
)

_FINGERPRINT_PREFIX_LEN = 12
_INSTRUCTIONS_TEXT_MAX = 64
_STRING_LOG_MAX = 256
_CANCELLED_RUN_MAX = 256

_LOCAL_RUNTIMES = frozenset({"fake", "stub", "plugin", "in_process"})


class OperationRunIdError(ValueError):
    """Client ``operation_run_id`` is not a UUID string of at most 64 characters."""

    code = "operation_run_id_invalid"


class OperationCancelled(Exception):
    """In-flight work stopped because the run was cancelled."""

    code = "operation_cancelled"

    def __init__(self) -> None:
        super().__init__(self.code)


class OperationBudgetExceeded(Exception):
    """The next model call is refused by an env or request ceiling."""

    def __init__(self, budget_code: str) -> None:
        self.budget_code = budget_code
        self.code = budget_code
        super().__init__(budget_code)


@dataclass
class MutableSpan:
    """Mutable span open on the current task. Validated only at close."""

    run_id: str
    span_id: str
    parent_span_id: str | None
    kind: str
    started_at: float
    parent: MutableSpan | None = None
    status: str = "ok"
    agent_id: str | None = None
    model_id: str | None = None
    runtime: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    usage_status: str = "unavailable"
    local_inference_ms: int | None = None
    process_rss_kb: int | None = None
    process_rss_delta_kb: int | None = None
    rss_at_enter_kb: int | None = None
    gpu_allocated_bytes: int | None = None
    memory_status: str = "unavailable"
    retry_count: int = 0
    revision_count: int | None = None
    failure_code: str | None = None
    provider_reported_cost_micros: int | None = None
    model_call_count: int = 0
    failure_count: int = 0
    stop_reason: str | None = None
    budget_code: str | None = None
    duration_ms: int = 0
    byte_size: int | None = None
    event_count: int | None = None
    instructions_len: int | None = None
    error_type: str | None = None
    cancel_event: asyncio.Event | None = None
    failure_noted: bool = False
    children: list[MutableSpan] = field(default_factory=list)


_current: ContextVar[MutableSpan | None] = ContextVar("operation_span", default=None)
_lock = threading.Lock()
_open_runs: dict[str, MutableSpan] = {}
_cancelled_runs: dict[str, None] = {}


@dataclass
class _RunClock:
    """Wall clock a render worker can read after the request span has closed."""

    started_at: float
    wall_ms: int


_run_clocks: dict[str, _RunClock] = {}


def reset_operation_trace_for_tests() -> None:
    """Drop process-wide cancel state. Does not clear another task's ContextVar."""
    with _lock:
        _open_runs.clear()
        _cancelled_runs.clear()
        _run_clocks.clear()
    _current.set(None)


def adopt_operation_run_id(raw: str | None) -> str:
    """Return a canonical UUID string, or mint one when the client omitted the field."""
    if raw is None or str(raw).strip() == "":
        minted = str(uuid.uuid4())
        logger.debug("Minted operation run id", extra={"run_id": minted})
        return minted
    text = str(raw).strip()
    if len(text) > 64:
        logger.info(
            "Rejected operation run id",
            extra={"code": OperationRunIdError.code, "length": len(text)},
        )
        raise OperationRunIdError(OperationRunIdError.code)
    try:
        parsed = uuid.UUID(text)
    except ValueError as exc:
        logger.info(
            "Rejected operation run id",
            extra={"code": OperationRunIdError.code, "error_type": "ValueError"},
        )
        raise OperationRunIdError(OperationRunIdError.code) from exc
    adopted = str(parsed)
    logger.debug("Adopted operation run id", extra={"run_id": adopted})
    return adopted


def current_span() -> MutableSpan | None:
    return _current.get()


def current_run_id() -> str | None:
    span = _current.get()
    if span is None:
        return None
    return span.run_id


def current_budget_code() -> str | None:
    run = _run_span(_current.get())
    if run is None:
        return None
    return run.budget_code


def remember_run_clock(run_id: str, *, started_at: float, wall_ms: int) -> None:
    """Record the start and wall limit a later render attempt can see."""
    if not run_id:
        return
    limit = max(1, int(wall_ms))
    with _lock:
        if run_id not in _run_clocks and len(_run_clocks) >= _CANCELLED_RUN_MAX:
            oldest = next(iter(_run_clocks))
            _run_clocks.pop(oldest, None)
            logger.debug(
                "Run clock evicted oldest id",
                extra={"evicted_prefix": oldest[:12]},
            )
        _run_clocks[run_id] = _RunClock(started_at=float(started_at), wall_ms=limit)
    logger.debug(
        "[FIX] Recorded run wall clock",
        extra={"run_id": run_id, "wall_ms": limit},
    )


def note_run_wall_limit(wall_ms: int | None) -> None:
    """Lower the open run's wall. A missing or larger value does not raise it."""
    span = _current.get()
    run = _run_span(span) if span is not None else None
    if run is None or wall_ms is None:
        return
    limit = max(1, int(wall_ms))
    with _lock:
        clock = _run_clocks.get(run.run_id)
        if clock is None or limit >= clock.wall_ms:
            return
        clock.wall_ms = limit
    logger.info(
        "[FIX] Run wall limit lowered",
        extra={"run_id": run.run_id, "wall_ms": limit, "budget_code": BUDGET_RUNTIME},
    )


def _run_wall_exceeded(run_id: str) -> bool:
    with _lock:
        live = _open_runs.get(run_id)
        clock = _run_clocks.get(run_id)
        if live is None and clock is None:
            return False
        started = live.started_at if live is not None else clock.started_at  # type: ignore[union-attr]
        wall = (
            clock.wall_ms
            if clock is not None
            else load_operation_budget_settings().max_runtime_ms
        )
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return elapsed_ms >= wall


def render_attempt_decision(
    operation_run_id: str | None,
    attempt_count: int,
    max_attempts: int,
) -> str:
    """Return ``go``, ``cancelled``, ``runtime``, or ``budget`` before an engine attempt."""

    if operation_run_id and is_run_cancelled(operation_run_id):
        return "cancelled"
    if operation_run_id and _run_wall_exceeded(operation_run_id):
        logger.warning(
            "[FIX] Render attempt stopped by runtime budget",
            extra={
                "budget_code": BUDGET_RUNTIME,
                "run_id": operation_run_id,
                "attempt_count": attempt_count,
            },
        )
        return "runtime"
    if attempt_count >= max_attempts:
        return "budget"
    return "go"


def is_run_cancelled(run_id: str | None = None) -> bool:
    span = _current.get()
    target = run_id or (span.run_id if span is not None else None)
    if target is None:
        return False
    with _lock:
        if target in _cancelled_runs:
            return True
    run = _open_runs.get(target)
    if run is not None and run.cancel_event is not None and run.cancel_event.is_set():
        return True
    return False


def mark_run_cancelled(run_id: str) -> None:
    """Record a cancelled run for this process and wake an open run's event."""
    if not run_id:
        return
    with _lock:
        if run_id not in _cancelled_runs and len(_cancelled_runs) >= _CANCELLED_RUN_MAX:
            oldest = next(iter(_cancelled_runs))
            _cancelled_runs.pop(oldest, None)
            logger.debug(
                "Cancelled-run set evicted oldest id",
                extra={"evicted_prefix": oldest[:12]},
            )
        _cancelled_runs[run_id] = None
        run = _open_runs.get(run_id)
    if run is not None and run.cancel_event is not None:
        run.cancel_event.set()
        if run.status == "ok":
            run.status = "cancelled"
            run.failure_code = run.failure_code or "operation_cancelled"
    logger.info("Run marked cancelled", extra={"run_id": run_id})


def note_usage(
    *,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    usage_status: str | None = None,
    local_inference_ms: int | None = None,
    provider_reported_cost_micros: int | None = None,
) -> None:
    """Record reported usage. Missing numbers stay null — zeros are not invented."""
    span = _current.get()
    if span is None:
        return
    if prompt_tokens is not None:
        span.prompt_tokens = (span.prompt_tokens or 0) + int(prompt_tokens)
    if completion_tokens is not None:
        span.completion_tokens = (span.completion_tokens or 0) + int(completion_tokens)
    if usage_status:
        span.usage_status = _merge_usage(span.usage_status, usage_status)
    if local_inference_ms is not None:
        span.local_inference_ms = int(local_inference_ms)
    if provider_reported_cost_micros is not None:
        span.provider_reported_cost_micros = int(provider_reported_cost_micros)
    elif load_operation_budget_settings().remote_cost_micros is not None:
        logger.debug(
            "Provider cost unavailable",
            extra={"cost_status": "unavailable", "run_id": span.run_id},
        )
    logger.debug(
        "Noted operation usage",
        extra={
            "run_id": span.run_id,
            "span_id_prefix": span.span_id[:12],
            "usage_status": span.usage_status,
            "prompt_tokens": span.prompt_tokens,
            "completion_tokens": span.completion_tokens,
        },
    )


def note_retry(count: int = 1) -> None:
    span = _current.get()
    if span is None or count <= 0:
        return
    span.retry_count += int(count)
    logger.debug(
        "Operation span retry",
        extra={"span_id_prefix": span.span_id[:12], "retry_count": span.retry_count},
    )


def note_failure(failure_code: str, *, error_type: str | None = None) -> None:
    span = _current.get()
    if span is None:
        return
    span.failure_code = str(failure_code)[:64]
    if span.status == "ok":
        span.status = "failed"
    if error_type:
        span.error_type = str(error_type)[:64]
    _note_failure_count(span)
    logger.debug(
        "Operation span failure noted",
        extra={
            "span_id_prefix": span.span_id[:12],
            "failure_code": span.failure_code,
            "error_type": span.error_type or "unknown",
        },
    )


def note_run_outcome(
    *,
    revision_count: int | None = None,
    stop_reason: str | None = None,
    budget_code: str | None = None,
    status: str | None = None,
) -> None:
    run = _run_span(_current.get())
    if run is None:
        return
    if revision_count is not None:
        run.revision_count = max(0, min(int(revision_count), 8))
    if stop_reason:
        run.stop_reason = str(stop_reason)[:64]
    if budget_code:
        run.budget_code = str(budget_code)[:64]
        if run.status == "ok":
            run.status = "budget_exceeded"
    if status:
        run.status = status
    logger.debug(
        "Noted run outcome",
        extra={
            "run_id": run.run_id,
            "revision_count": run.revision_count,
            "stop_reason": run.stop_reason,
            "budget_code": run.budget_code,
            "status": run.status,
        },
    )


def reserve_model_call() -> bool:
    """Return whether a provider call may start. No open run means allowed, uncounted."""
    run = _run_span(_current.get())
    if run is None:
        logger.debug("Model call reserved without an open run")
        return True
    if is_run_cancelled(run.run_id):
        logger.info(
            "Model call cancelled",
            extra={"run_id": run.run_id},
        )
        return False
    settings = load_operation_budget_settings()
    elapsed_ms = int((time.perf_counter() - run.started_at) * 1000)
    if elapsed_ms >= settings.max_runtime_ms:
        _refuse_budget(run, BUDGET_RUNTIME)
        return False
    if settings.max_model_calls != 0 and run.model_call_count >= settings.max_model_calls:
        _refuse_budget(run, BUDGET_MODEL_CALL)
        return False
    if (
        settings.max_prompt_tokens is not None
        and run.usage_status != "unavailable"
        and run.prompt_tokens is not None
        and run.prompt_tokens >= settings.max_prompt_tokens
    ):
        _refuse_budget(run, BUDGET_TOKEN)
        return False
    if (
        settings.remote_cost_micros is not None
        and run.provider_reported_cost_micros is not None
        and run.provider_reported_cost_micros >= settings.remote_cost_micros
    ):
        _refuse_budget(run, BUDGET_COST)
        return False
    run.model_call_count += 1
    logger.debug(
        "Model call reserved",
        extra={"run_id": run.run_id, "model_call_count": run.model_call_count},
    )
    return True


def raise_if_model_call_blocked() -> None:
    """Refuse before provider I/O. Cancel and budget are distinct errors."""
    if is_run_cancelled():
        raise OperationCancelled()
    if not reserve_model_call():
        code = current_budget_code() or BUDGET_MODEL_CALL
        if code == "operation_cancelled" or is_run_cancelled():
            raise OperationCancelled()
        raise OperationBudgetExceeded(code)


@asynccontextmanager
async def operation_span(kind: str, **fields: Any) -> AsyncIterator[MutableSpan]:
    """Open a span for the current task. Close and log it when the body finishes."""
    parent = _current.get()
    if kind == "run":
        run_id = str(fields.get("run_id") or uuid.uuid4())
        parent_span_id = None
        parent_ref = None
    elif parent is None:
        run_id = str(fields.get("run_id") or uuid.uuid4())
        parent_span_id = None
        parent_ref = None
    else:
        run_id = parent.run_id
        parent_span_id = parent.span_id
        parent_ref = parent
    span_id = str(uuid.uuid4())
    agent_id = fields.get("agent_id")
    if isinstance(agent_id, str):
        agent_id = agent_id[:64]
    span = MutableSpan(
        run_id=run_id[:64],
        span_id=span_id,
        parent_span_id=parent_span_id,
        kind=kind,
        started_at=time.perf_counter(),
        parent=parent_ref,
        agent_id=agent_id,
        model_id=_clip(fields.get("model_id")),
        runtime=_clip(fields.get("runtime")),
        byte_size=_optional_int(fields.get("byte_size")),
        event_count=_optional_int(fields.get("event_count")),
        instructions_len=_optional_int(fields.get("instructions_len")),
        retry_count=int(fields.get("retry_count") or 0),
    )
    if kind == "run":
        span.cancel_event = asyncio.Event()
        span.revision_count = 0
        with _lock:
            _open_runs[span.run_id] = span
            if span.run_id in _cancelled_runs:
                span.cancel_event.set()
                span.status = "cancelled"
                span.failure_code = "operation_cancelled"
        remember_run_clock(
            span.run_id,
            started_at=span.started_at,
            wall_ms=load_operation_budget_settings().max_runtime_ms,
        )
    span.rss_at_enter_kb = _sample_rss_kb()
    token = _current.set(span)
    logger.debug(
        "Operation span open",
        extra={
            "span_kind": kind,
            "span_id_prefix": span.span_id[:12],
            "run_id": span.run_id,
        },
    )
    error_type: str | None = None
    try:
        yield span
    except KeyboardInterrupt:
        error_type = "KeyboardInterrupt"
        raise
    except SystemExit:
        error_type = "SystemExit"
        span.status = "failed"
        span.failure_code = span.failure_code or "operation_model_crashed"
        span.error_type = error_type
        _note_failure_count(span)
        raise
    except asyncio.CancelledError:
        error_type = "CancelledError"
        if span.status == "ok":
            span.status = "cancelled"
            span.failure_code = span.failure_code or "operation_cancelled"
        span.error_type = error_type
        raise
    except Exception as exc:
        error_type = type(exc).__name__
        if span.status == "ok":
            span.status = "failed"
        if not span.failure_code:
            code = getattr(exc, "code", None)
            span.failure_code = str(code)[:64] if isinstance(code, str) and code else "operation_failed"
        span.error_type = error_type
        _note_failure_count(span)
        raise
    finally:
        _close_span(span, error_type=error_type)
        _current.reset(token)
        if kind == "run":
            with _lock:
                clock = _run_clocks.get(span.run_id)
                if clock is not None:
                    clock.started_at = span.started_at
                _open_runs.pop(span.run_id, None)


def build_summary(
    root_span: MutableSpan,
    children: list[MutableSpan] | None = None,
) -> OperationSummaryV1:
    """Fill operation.summary.v1 from a closed (or still-open) run span."""
    nodes = _walk(root_span if children is None else _tree_from(root_span, children))
    failure_count = sum(1 for node in nodes if node.status == "failed")
    retry_count = sum(int(node.retry_count) for node in nodes)
    runtimes: list[str] = []
    usage = root_span.usage_status or "unavailable"
    for node in nodes:
        if node.runtime and node.runtime not in runtimes and len(runtimes) < 8:
            runtimes.append(node.runtime)
        usage = _merge_usage(usage, node.usage_status)
    # Token totals live on the run after each child closes. Do not add children again.
    prompt_sum = root_span.prompt_tokens
    completion_sum = root_span.completion_tokens
    local_ms = root_span.local_inference_ms
    status = _summary_status(root_span, failure_count)
    revision_count = root_span.revision_count if root_span.revision_count is not None else 0
    logger.debug(
        "Built operation summary",
        extra={
            "run_id": root_span.run_id,
            "status": status,
            "model_call_count": root_span.model_call_count,
            "revision_count": revision_count,
            "failure_count": failure_count,
        },
    )
    return OperationSummaryV1(
        run_id=root_span.run_id,
        status=status,
        duration_ms=max(0, int(root_span.duration_ms)),
        model_call_count=max(0, int(root_span.model_call_count)),
        revision_count=max(0, min(int(revision_count), 8)),
        failure_count=failure_count,
        retry_count=retry_count,
        stop_reason=root_span.stop_reason,
        budget_code=root_span.budget_code,
        runtimes=runtimes,
        usage_status=usage,  # type: ignore[arg-type]
        prompt_tokens=prompt_sum,
        completion_tokens=completion_sum,
        local_inference_ms=local_ms,
    )


def redact_log_fields(mapping: Mapping[str, Any]) -> dict[str, Any]:
    """Drop secret-like names and shrink long strings before a span log line."""
    redacted: dict[str, Any] = {}
    for key, value in mapping.items():
        name = str(key)
        normalized = name.strip().lower().replace("-", "_")
        if _is_secret_name(normalized) or normalized in _DROP_EXACT:
            logger.debug("Operation trace dropped field", extra={"field_name": name[:64]})
            continue
        if normalized == "instructions" and isinstance(value, str) and len(value) > _INSTRUCTIONS_TEXT_MAX:
            redacted["instructions_len"] = len(value)
            continue
        if normalized.endswith("fingerprint") and isinstance(value, str) and len(value) > _FINGERPRINT_PREFIX_LEN:
            redacted[name] = value[:_FINGERPRINT_PREFIX_LEN]
            continue
        redacted[name] = _redact_value(value)
    return redacted


def runtime_is_local(runtime: str | None) -> bool:
    if not runtime:
        return False
    value = runtime.strip().lower()
    if value.startswith("local"):
        return True
    return value in _LOCAL_RUNTIMES


def _refuse_budget(run: MutableSpan, budget_code: str) -> None:
    run.budget_code = budget_code
    if run.status == "ok":
        run.status = "budget_exceeded"
    logger.warning(
        "Operation budget refused the next model call",
        extra={
            "budget_code": budget_code,
            "model_call_count": run.model_call_count,
            "run_id": run.run_id,
        },
    )


def _note_failure_count(span: MutableSpan) -> None:
    if span.failure_noted:
        return
    span.failure_noted = True
    run = _run_span(span)
    if run is None:
        return
    run.failure_count += 1


def _close_span(span: MutableSpan, *, error_type: str | None) -> None:
    span.duration_ms = max(0, int((time.perf_counter() - span.started_at) * 1000))
    if error_type and span.error_type is None:
        span.error_type = error_type
    if span.kind != "run":
        span.revision_count = None
    if runtime_is_local(span.runtime) and span.local_inference_ms is None and span.kind == "model":
        span.local_inference_ms = span.duration_ms
    _attach_memory(span)
    if span.parent is not None:
        span.parent.children.append(span)
        _fold_child_usage(span.parent if span.parent.kind == "run" else _run_span(span.parent), span)
    model = _validated_span(span)
    payload: dict[str, Any] = {
        "operation_trace": True,
        **(model.model_dump(mode="json") if model is not None else _fallback_payload(span)),
    }
    if span.byte_size is not None:
        payload["byte_size"] = span.byte_size
    if span.event_count is not None:
        payload["event_count"] = span.event_count
    if span.instructions_len is not None:
        payload["instructions_len"] = span.instructions_len
    safe = redact_log_fields(payload)
    logger.info("Operation span closed", extra=safe)
    if span.status == "failed":
        logger.error(
            "Operation span failed",
            extra={
                "failure_code": span.failure_code or "operation_failed",
                "error_type": span.error_type or "unknown",
                "run_id": span.run_id,
                "model_id": span.model_id,
            },
        )


def record_finished_span(span: MutableSpan) -> None:
    """Validate and log a span built off the request task.

    Render workers cannot see the request ``ContextVar``. ``parent_span_id``
    may be null; ``run_id`` is the join key when the job stored one.
    """

    if span.started_at <= 0:
        span.started_at = time.perf_counter()
    _close_span(span, error_type=span.error_type)


def _fold_child_usage(run: MutableSpan | None, child: MutableSpan) -> None:
    if run is None:
        return
    if child.prompt_tokens is not None:
        run.prompt_tokens = (run.prompt_tokens or 0) + child.prompt_tokens
    if child.completion_tokens is not None:
        run.completion_tokens = (run.completion_tokens or 0) + child.completion_tokens
    if child.provider_reported_cost_micros is not None:
        run.provider_reported_cost_micros = (
            child.provider_reported_cost_micros
            if run.provider_reported_cost_micros is None
            else run.provider_reported_cost_micros + child.provider_reported_cost_micros
        )
    run.usage_status = _merge_usage(run.usage_status, child.usage_status)
    if child.local_inference_ms is not None:
        run.local_inference_ms = (run.local_inference_ms or 0) + child.local_inference_ms
    if child.runtime and run.runtime is None:
        run.runtime = child.runtime


def _validated_span(span: MutableSpan) -> OperationSpanV1 | None:
    try:
        return OperationSpanV1(
            run_id=span.run_id,
            span_id=span.span_id,
            parent_span_id=span.parent_span_id,
            kind=span.kind,  # type: ignore[arg-type]
            status=span.status,  # type: ignore[arg-type]
            duration_ms=span.duration_ms,
            agent_id=span.agent_id,
            model_id=span.model_id,
            runtime=span.runtime,
            prompt_tokens=span.prompt_tokens,
            completion_tokens=span.completion_tokens,
            usage_status=span.usage_status,  # type: ignore[arg-type]
            local_inference_ms=span.local_inference_ms,
            process_rss_kb=span.process_rss_kb,
            process_rss_delta_kb=span.process_rss_delta_kb,
            gpu_allocated_bytes=span.gpu_allocated_bytes,
            memory_status=span.memory_status,  # type: ignore[arg-type]
            retry_count=span.retry_count,
            revision_count=span.revision_count,
            failure_code=span.failure_code,
            provider_reported_cost_micros=span.provider_reported_cost_micros,
        )
    except Exception as exc:  # noqa: BLE001 — close must not mask the body error
        logger.error(
            "Operation span validation failed",
            extra={"failure_code": "operation_span_invalid", "error_type": type(exc).__name__},
        )
        return None


def _fallback_payload(span: MutableSpan) -> dict[str, Any]:
    return {
        "run_id": span.run_id,
        "span_id": span.span_id,
        "kind": span.kind,
        "status": span.status,
        "duration_ms": span.duration_ms,
        "failure_code": span.failure_code,
    }


def _attach_memory(span: MutableSpan) -> None:
    if span.status != "ok":
        span.process_rss_kb = None
        span.process_rss_delta_kb = None
        span.gpu_allocated_bytes = None
        span.memory_status = "unavailable"
        return
    rss = _sample_rss_kb()
    if rss is None:
        span.memory_status = "unavailable"
        return
    span.process_rss_kb = rss
    if span.rss_at_enter_kb is None:
        span.process_rss_delta_kb = None
    else:
        span.process_rss_delta_kb = int(rss - span.rss_at_enter_kb)
    span.memory_status = "available"
    gpu = _sample_gpu_allocated_bytes()
    if gpu is not None:
        span.gpu_allocated_bytes = gpu


def _sample_rss_kb() -> int | None:
    try:
        rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        if sys.platform == "darwin":
            rss = rss // 1024
        if rss < 0:
            return None
        return rss
    except Exception as exc:  # noqa: BLE001 — sampling must not raise
        logger.debug(
            "Process RSS unavailable",
            extra={"memory_status": "unavailable", "error_type": type(exc).__name__},
        )
        return None


def _sample_gpu_allocated_bytes() -> int | None:
    torch = sys.modules.get("torch")
    if torch is None:
        return None
    try:
        cuda = getattr(torch, "cuda", None)
        if cuda is None or not bool(cuda.is_available()):
            return None
        allocated = cuda.memory_allocated()
        return int(allocated)
    except Exception as exc:  # noqa: BLE001 — sampling must not raise
        logger.debug(
            "GPU memory unavailable",
            extra={"memory_status": "unavailable", "error_type": type(exc).__name__},
        )
        return None


def _summary_status(root: MutableSpan, failure_count: int) -> str:
    if root.status == "cancelled" or root.failure_code == "operation_cancelled":
        return "cancelled"
    if root.budget_code or root.status == "budget_exceeded":
        return "budget_exceeded"
    if failure_count > 0 or root.status == "failed":
        return "failed"
    return "ok"


def _walk(span: MutableSpan) -> list[MutableSpan]:
    nodes = [span]
    for child in span.children:
        nodes.extend(_walk(child))
    return nodes


def _tree_from(root: MutableSpan, children: list[MutableSpan]) -> MutableSpan:
    if root.children:
        return root
    root.children = list(children)
    return root


def _run_span(span: MutableSpan | None) -> MutableSpan | None:
    current = span
    while current is not None:
        if current.kind == "run":
            return current
        current = current.parent
    return None


def _merge_usage(current: str, incoming: str) -> str:
    if not incoming:
        return current or "unavailable"
    if current == incoming:
        return current
    if current == "unavailable":
        return incoming
    if incoming == "unavailable":
        return "partial" if current == "available" else current
    return "partial"


def _is_secret_name(normalized: str) -> bool:
    return normalized in FORBIDDEN_LOG_FIELD_NAMES or normalized.endswith("_api_key")


def _redact_value(value: Any) -> Any:
    if isinstance(value, str) and len(value) > _STRING_LOG_MAX:
        return {"redacted": True, "length": len(value)}
    if isinstance(value, dict):
        return redact_log_fields(value)
    return value


def _clip(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:64]


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    return None


logger.info(
    "Operation trace module loaded",
    extra={"cancelled_run_max": _CANCELLED_RUN_MAX, "string_log_max": _STRING_LOG_MAX},
)
