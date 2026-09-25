"""operation.span.v1 and operation.summary.v1 contracts.

In-process traces only. These models never carry prompts, compositions, or PCM.
"""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

logger = logging.getLogger(__name__)

OPERATION_SPAN_SCHEMA: Literal["operation.span.v1"] = "operation.span.v1"
OPERATION_SUMMARY_SCHEMA: Literal["operation.summary.v1"] = "operation.summary.v1"

SpanKind = Literal["run", "agent", "model", "render"]
SpanStatus = Literal["ok", "failed", "cancelled", "budget_exceeded"]
OperationUsageStatus = Literal["available", "partial", "unavailable"]
MemoryStatus = Literal["available", "unavailable"]

_ID_MAX = 64
_RUNTIME_MAX = 8


class OperationSpanV1(BaseModel):
    """One closed operation span. Built at close, then logged."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["operation.span.v1"] = OPERATION_SPAN_SCHEMA
    run_id: str = Field(..., min_length=1, max_length=_ID_MAX)
    span_id: str = Field(..., min_length=1, max_length=_ID_MAX)
    parent_span_id: str | None = Field(default=None, max_length=_ID_MAX)
    kind: SpanKind
    status: SpanStatus
    duration_ms: int = Field(..., ge=0)
    agent_id: str | None = Field(default=None, max_length=_ID_MAX)
    model_id: str | None = Field(default=None, max_length=_ID_MAX)
    runtime: str | None = Field(default=None, max_length=_ID_MAX)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    usage_status: OperationUsageStatus = "unavailable"
    local_inference_ms: int | None = Field(default=None, ge=0)
    process_rss_kb: int | None = Field(default=None, ge=0)
    process_rss_delta_kb: int | None = None
    gpu_allocated_bytes: int | None = Field(default=None, ge=0)
    memory_status: MemoryStatus = "unavailable"
    retry_count: int = Field(default=0, ge=0)
    revision_count: int | None = Field(default=None, ge=0, le=8)
    failure_code: str | None = Field(default=None, max_length=_ID_MAX)
    provider_reported_cost_micros: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _kind_rules(self) -> OperationSpanV1:
        if self.kind == "run" and self.parent_span_id is not None:
            raise ValueError("run span parent_span_id must be null")
        if self.kind != "run" and self.revision_count is not None:
            raise ValueError("revision_count is run-span only")
        if self.kind == "agent" and not self.agent_id:
            raise ValueError("agent span requires agent_id")
        return self


class OperationSummaryV1(BaseModel):
    """Small response summary. No composition and no prompt text."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["operation.summary.v1"] = OPERATION_SUMMARY_SCHEMA
    run_id: str = Field(..., min_length=1, max_length=_ID_MAX)
    status: SpanStatus
    duration_ms: int = Field(..., ge=0)
    model_call_count: int = Field(default=0, ge=0)
    revision_count: int = Field(default=0, ge=0, le=8)
    failure_count: int = Field(default=0, ge=0)
    retry_count: int = Field(default=0, ge=0)
    stop_reason: str | None = Field(default=None, max_length=_ID_MAX)
    budget_code: str | None = Field(default=None, max_length=_ID_MAX)
    runtimes: list[str] = Field(default_factory=list, max_length=_RUNTIME_MAX)
    usage_status: OperationUsageStatus = "unavailable"
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    local_inference_ms: int | None = Field(default=None, ge=0)


logger.info(
    "Operation trace schemas loaded",
    extra={"span_schema": OPERATION_SPAN_SCHEMA, "summary_schema": OPERATION_SUMMARY_SCHEMA},
)
