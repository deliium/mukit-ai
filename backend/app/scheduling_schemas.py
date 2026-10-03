"""Non-playable AI job scheduling documents.

These models never carry note events, prompts, or shell commands. This module
does not import FastAPI, torch, stores, video, film, agent, adaptive engine, or
embedding modules.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

logger = logging.getLogger(__name__)

POLICY_SCHEMA = "scheduling.policy.v1"
JOB_SCHEMA = "scheduling.job.v1"
CANDIDATE_SCHEMA = "scheduling.candidate.v1"
DECISION_SCHEMA = "scheduling.decision.v1"
ATTEMPT_SCHEMA = "scheduling.attempt.v1"

NODE_ID_PATTERN = re.compile(r"^node_[0-9a-f]{16}$")

SchedulingPolicyMode = Literal[
    "prefer_local",
    "fastest_available",
    "memory_safe",
    "fixed_node",
]
PrivacyClass = Literal["private", "allow_public"]
TrustBoundary = Literal["controller_local", "trusted_lan", "public_cloud"]
DeviceClass = Literal["cpu", "igpu", "dgpu", "unknown"]
CandidateAvailability = Literal["available", "busy", "draining", "unavailable"]
Locality = Literal["local", "remote"]

CONTROLLER_LOCAL_RUNTIMES = frozenset(
    {
        "local_openai_compatible",
        "fake",
        "stub",
        "fake_symbolic",
        "music_transformer",
        "plugin",
        "personal_composer",
        "model_lab",
    }
)

TRUST_RANK: dict[str, int] = {
    "controller_local": 0,
    "trusted_lan": 1,
    "public_cloud": 2,
}

FORBIDDEN_PAYLOAD_KEYS = frozenset(
    {
        "events",
        "notes",
        "pitch",
        "composition",
        "composition_json",
        "api_key",
        "authorization",
        "shell",
        "command",
        "argv",
        "subprocess",
        "prompt",
        "input_text",
        "output_text",
    }
)

_HTTP_STATUS = {
    "scheduling_disabled": 404,
    "scheduling_forbidden_payload": 422,
    "scheduling_no_eligible_candidate": 503,
    "scheduling_fixed_node_unavailable": 503,
    "scheduling_trust_refused": 403,
    "scheduling_conflict": 409,
    "scheduling_invalid_policy": 422,
}


class SchedulingError(Exception):
    """Domain error mapped to a structured HTTP detail."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message[:200]
        self.http_status = int(http_status if http_status is not None else _HTTP_STATUS.get(code, 422))
        self.details = details or {}


def map_scheduling_error_to_http(exc: SchedulingError) -> tuple[int, dict[str, Any]]:
    """Map domain errors to ``(status, detail)`` for HTTPException."""
    detail: dict[str, Any] = {"code": exc.code, "message": exc.message}
    safe: dict[str, Any] = {}
    for key, value in exc.details.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe[key] = value
        elif isinstance(value, list) and all(isinstance(item, (str, int)) for item in value):
            safe[key] = value[:16]
    if safe:
        detail["details"] = safe
    return int(exc.http_status), detail


def _log_schema_rejection(model_name: str, code: str) -> None:
    logger.debug(
        "Scheduling schema rejected",
        extra={"model_name": model_name, "code": code},
    )


def scan_forbidden_scheduling_keys(payload: Any) -> list[str]:
    """Return forbidden key names found anywhere in ``payload``."""
    found: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_text = str(key)
            if key_text in FORBIDDEN_PAYLOAD_KEYS:
                found.append(key_text)
            found.extend(scan_forbidden_scheduling_keys(value))
    elif isinstance(payload, list):
        for item in payload:
            found.extend(scan_forbidden_scheduling_keys(item))
    return found


def reject_scheduling_forbidden_payload(payload: Any, *, model_name: str) -> None:
    """Raise ``scheduling_forbidden_payload`` when a key is forbidden."""
    found = scan_forbidden_scheduling_keys(payload)
    if not found:
        return
    _log_schema_rejection(model_name, "scheduling_forbidden_payload")
    raise SchedulingError(
        "scheduling_forbidden_payload",
        "Scheduling documents cannot carry that field.",
        details={"field_name": found[0]},
    )


def _reject(model_name: str, code: str, message: str) -> None:
    _log_schema_rejection(model_name, code)
    raise ValueError(code if not message else f"{code}: {message}")


def trust_boundary_for_runtime(
    *,
    runtime: str,
    locality: str | None,
    has_execution_node_id: bool = False,
) -> TrustBoundary:
    """Map a registry/runtime row to a trust boundary. First match wins."""
    runtime_text = (runtime or "").strip()
    if runtime_text == "execution_node" or has_execution_node_id:
        return "trusted_lan"
    if runtime_text in CONTROLLER_LOCAL_RUNTIMES and not has_execution_node_id:
        return "controller_local"
    if (locality or "").strip() == "remote":
        return "public_cloud"
    return "controller_local"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _reject_forbidden_keys(cls, value: Any) -> Any:
        if isinstance(value, dict):
            try:
                reject_scheduling_forbidden_payload(value, model_name=cls.__name__)
            except SchedulingError as exc:
                raise ValueError(exc.code) from exc
        return value


class SchedulingPolicyV1(_Strict):
    """Named placement mode and public-cloud gate."""

    schema_version: Literal["scheduling.policy.v1"] = POLICY_SCHEMA
    mode: SchedulingPolicyMode = "prefer_local"
    allow_public_cloud: bool = False
    fixed_node_id: str | None = Field(default=None, pattern=r"^node_[0-9a-f]{16}$")
    fixed_model_id: str | None = Field(default=None, min_length=1, max_length=160)
    max_attempts: int = Field(default=2, ge=1, le=4)
    updated_at: str | None = Field(default=None, max_length=40)
    document_revision: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def fixed_node_requires_id(self) -> SchedulingPolicyV1:
        if self.mode == "fixed_node" and not self.fixed_node_id:
            _reject(
                self.__class__.__name__,
                "scheduling_invalid_policy",
                "fixed_node requires fixed_node_id",
            )
        return self


class SchedulingJobV1(_Strict):
    """One AI operation placement request. Never carries a prompt."""

    schema_version: Literal["scheduling.job.v1"] = JOB_SCHEMA
    operation: str = Field(min_length=1, max_length=64)
    required_capability: str = Field(min_length=1, max_length=64)
    privacy_class: PrivacyClass = "private"
    priority: int = Field(default=50, ge=0, le=100)
    estimated_memory_mb: int | None = Field(default=None, ge=0)
    prefer_device_class: Literal["cpu", "igpu", "dgpu"] | None = None
    exclude_node_ids: list[str] = Field(default_factory=list, max_length=32)
    exclude_model_ids: list[str] = Field(default_factory=list, max_length=64)
    purpose: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def validate_excludes(self) -> SchedulingJobV1:
        for node_id in self.exclude_node_ids:
            if not NODE_ID_PATTERN.fullmatch(node_id):
                _reject(self.__class__.__name__, "scheduling_invalid_policy", "exclude node id")
        return self


class SchedulingCandidateV1(_Strict):
    """Snapshot of one registry model, optionally bound to an ExecutionNode."""

    schema_version: Literal["scheduling.candidate.v1"] = CANDIDATE_SCHEMA
    model_id: str = Field(min_length=1, max_length=160)
    runtime: str = Field(min_length=1, max_length=64)
    primary_capability: str = Field(min_length=1, max_length=64)
    secondary_capabilities: list[str] = Field(default_factory=list, max_length=8)
    supported_operations: list[str] = Field(default_factory=list, max_length=32)
    trust_boundary: TrustBoundary
    node_id: str | None = Field(default=None, pattern=r"^node_[0-9a-f]{16}$")
    device_class: DeviceClass = "unknown"
    memory_available_mb: int | None = Field(default=None, ge=0)
    memory_total_mb: int | None = Field(default=None, ge=0)
    estimated_latency_ms: int | None = Field(default=None, ge=0)
    availability: CandidateAvailability = "available"
    status: str = Field(min_length=1, max_length=32)
    locality: Locality = "local"


class SchedulingDecisionV1(_Strict):
    """Pure scheduler output for one attempt."""

    schema_version: Literal["scheduling.decision.v1"] = DECISION_SCHEMA
    selected_model_id: str | None = Field(default=None, min_length=1, max_length=160)
    selected_node_id: str | None = Field(default=None, pattern=r"^node_[0-9a-f]{16}$")
    policy_mode: SchedulingPolicyMode
    trust_boundary: TrustBoundary | None = None
    reason_codes: list[str] = Field(min_length=1, max_length=16)
    eligible_count: int = Field(ge=0)
    attempt_index: int = Field(default=1, ge=1)


class SchedulingAttemptV1(_Strict):
    """One invoke attempt and optional failure code."""

    schema_version: Literal["scheduling.attempt.v1"] = ATTEMPT_SCHEMA
    attempt_index: int = Field(ge=1)
    decision: SchedulingDecisionV1
    failure_code: str | None = Field(default=None, max_length=64)
    finished_ok: bool = False


class SchedulingPolicyPut(_Strict):
    """PUT body including CAS revision."""

    mode: SchedulingPolicyMode
    allow_public_cloud: bool = False
    fixed_node_id: str | None = Field(default=None, pattern=r"^node_[0-9a-f]{16}$")
    fixed_model_id: str | None = Field(default=None, min_length=1, max_length=160)
    max_attempts: int = Field(default=2, ge=1, le=4)
    document_revision: int = Field(ge=1)

    @model_validator(mode="after")
    def fixed_node_requires_id(self) -> SchedulingPolicyPut:
        if self.mode == "fixed_node" and not self.fixed_node_id:
            _reject(
                self.__class__.__name__,
                "scheduling_invalid_policy",
                "fixed_node requires fixed_node_id",
            )
        return self


class SchedulingPreviewRequest(_Strict):
    """Preview a decision without invoking a model."""

    job: SchedulingJobV1
    candidates: list[SchedulingCandidateV1] | None = None
    policy: SchedulingPolicyV1 | None = None
