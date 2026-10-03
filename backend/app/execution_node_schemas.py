"""Non-playable ExecutionNode documents for trusted LAN inference peers.

These models never carry note events, shell argv, or secrets. This module
does not import FastAPI, torch, stores, video, film, agent, or adaptive
engine modules.
"""

from __future__ import annotations

import ipaddress
import logging
import re
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

logger = logging.getLogger(__name__)

NODE_SCHEMA = "execution.node.v1"
REGISTRATION_SCHEMA = "execution.node.registration.v1"
HEARTBEAT_SCHEMA = "execution.node.heartbeat.v1"
CATALOG_SCHEMA = "execution.node.catalog.v1"
TASK_SCHEMA = "execution.task.v1"
TASK_RESULT_SCHEMA = "execution.task.result.v1"

NODE_ID_PATTERN = re.compile(r"^node_[0-9a-f]{16}$")
TASK_ID_PATTERN = re.compile(r"^task_[0-9a-f]{16}$")
NODE_HEX_PATTERN = re.compile(r"^[0-9a-f]{16}$")

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
    }
)

_METADATA_HOSTS = frozenset(
    {
        "169.254.169.254",
        "metadata.google.internal",
        "metadata",
    }
)
_BUILTIN_HOSTNAMES = frozenset({"localhost", "execution-node.fake"})

ExecutionNodeRole = Literal["worker"]
DeviceClass = Literal["cpu", "igpu", "dgpu", "unknown"]
NodeAvailability = Literal["available", "busy", "draining", "unavailable"]
HeartbeatAvailability = Literal["available", "busy", "draining"]
HealthStatus = Literal[
    "ready",
    "unconfigured",
    "unavailable",
    "degraded",
    "loading",
    "out_of_memory",
    "unsupported_device",
]
TaskOperation = Literal["complete_text"]
TaskResultStatus = Literal["completed", "cancelled", "failed"]
ModelLocalityPublic = Literal["local", "remote"]

_HTTP_STATUS = {
    "execution_nodes_disabled": 404,
    "execution_node_unauthorized": 401,
    "execution_node_token_missing": 503,
    "execution_node_not_found": 404,
    "execution_node_conflict": 409,
    "execution_node_unavailable": 503,
    "execution_node_forbidden_payload": 422,
    "execution_node_address_rejected": 422,
    "execution_node_task_not_found": 404,
    "execution_node_busy": 429,
}


class ExecutionNodeError(Exception):
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


def map_execution_node_error_to_http(exc: ExecutionNodeError) -> tuple[int, dict[str, Any]]:
    """Map domain errors to ``(status, detail)`` for HTTPException."""
    detail: dict[str, Any] = {"code": exc.code, "message": exc.message}
    safe: dict[str, Any] = {}
    for key, value in exc.details.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe[key] = value
        elif isinstance(value, list) and all(isinstance(item, str) for item in value):
            safe[key] = value[:16]
    if safe:
        detail["details"] = safe
    return int(exc.http_status), detail


def _log_schema_rejection(model_name: str, code: str) -> None:
    logger.debug(
        "Execution node schema rejected",
        extra={"model_name": model_name, "code": code},
    )


def scan_forbidden_execution_node_keys(payload: Any) -> list[str]:
    """Return forbidden key names found anywhere in ``payload``."""
    found: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_text = str(key)
            if key_text in FORBIDDEN_PAYLOAD_KEYS:
                found.append(key_text)
            found.extend(scan_forbidden_execution_node_keys(value))
    elif isinstance(payload, list):
        for item in payload:
            found.extend(scan_forbidden_execution_node_keys(item))
    return found


def reject_execution_node_forbidden_payload(payload: Any, *, model_name: str) -> None:
    """Raise ``execution_node_forbidden_payload`` when a key is forbidden."""
    found = scan_forbidden_execution_node_keys(payload)
    if not found:
        return
    _log_schema_rejection(model_name, "execution_node_forbidden_payload")
    raise ExecutionNodeError(
        "execution_node_forbidden_payload",
        "Execution node documents cannot carry that field.",
        details={"field_name": found[0]},
    )


def node_hex_from_node_id(node_id: str) -> str:
    """Return the 16 hex chars from ``node_<hex>``."""
    if not NODE_ID_PATTERN.fullmatch(node_id):
        raise ExecutionNodeError(
            "execution_node_not_found",
            "Node id is not valid.",
            details={"node_id": node_id[:32]},
        )
    return node_id.removeprefix("node_")


def parse_execution_node_model_id(model_id: str) -> tuple[str, str]:
    """Parse ``node:<16hex>:<remainder>`` into ``(node_hex, local_model_id)``."""
    if not model_id.startswith("node:"):
        raise ExecutionNodeError(
            "execution_node_not_found",
            "Model id is not an execution-node model.",
            details={"model_id": model_id[:64]},
        )
    rest = model_id[len("node:") :]
    if len(rest) < 17 or rest[16] != ":":
        raise ExecutionNodeError(
            "execution_node_not_found",
            "Model id is not an execution-node model.",
            details={"model_id": model_id[:64]},
        )
    node_hex = rest[:16]
    local_id = rest[17:]
    if not NODE_HEX_PATTERN.fullmatch(node_hex) or local_id == "":
        raise ExecutionNodeError(
            "execution_node_not_found",
            "Model id is not an execution-node model.",
            details={"model_id": model_id[:64]},
        )
    return node_hex, local_id


def build_execution_node_model_id(node_id: str, local_model_id: str) -> str:
    """Build the controller catalog id for a worker-local model."""
    node_hex = node_hex_from_node_id(node_id)
    return f"node:{node_hex}:{local_model_id}"


def _ip_is_allowed(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
    extra_networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network],
) -> bool:
    if address.is_loopback:
        return True
    if address.is_link_local or address.is_multicast or address.is_unspecified:
        return False
    if address.is_private:
        # RFC1918 / unique-local; still refuse link-local (already handled).
        return True
    for network in extra_networks:
        if address in network:
            return True
    return False


def validate_execution_node_address(
    address: str,
    *,
    allow_hostname: bool = False,
    extra_cidrs: list[str] | None = None,
) -> str:
    """Validate a worker address URI against the SSRF allowlist.

    Accepts ``http://`` / ``https://`` with host[:port] only. Loopback and
    RFC1918 are allowed. Link-local, cloud metadata, userinfo, and (by
    default) non-builtin hostnames are refused.
    """
    text = (address or "").strip()
    if not text or len(text) > 256:
        _log_schema_rejection("address", "execution_node_address_rejected")
        raise ExecutionNodeError(
            "execution_node_address_rejected",
            "Worker address is not allowed.",
        )
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"}:
        _log_schema_rejection("address", "execution_node_address_rejected")
        raise ExecutionNodeError(
            "execution_node_address_rejected",
            "Worker address is not allowed.",
            details={"reason": "scheme"},
        )
    if (
        parsed.username is not None
        or parsed.password is not None
        or "@" in text.split("://", 1)[-1].split("/", 1)[0]
    ):
        _log_schema_rejection("address", "execution_node_address_rejected")
        raise ExecutionNodeError(
            "execution_node_address_rejected",
            "Worker address is not allowed.",
            details={"reason": "userinfo"},
        )
    if parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
        _log_schema_rejection("address", "execution_node_address_rejected")
        raise ExecutionNodeError(
            "execution_node_address_rejected",
            "Worker address is not allowed.",
            details={"reason": "path"},
        )
    host = parsed.hostname
    if host is None or host == "":
        _log_schema_rejection("address", "execution_node_address_rejected")
        raise ExecutionNodeError(
            "execution_node_address_rejected",
            "Worker address is not allowed.",
            details={"reason": "host"},
        )
    host_lower = host.lower().rstrip(".")
    if host_lower in _METADATA_HOSTS:
        _log_schema_rejection("address", "execution_node_address_rejected")
        raise ExecutionNodeError(
            "execution_node_address_rejected",
            "Worker address is not allowed.",
            details={"reason": "metadata"},
        )

    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for cidr in extra_cidrs or []:
        try:
            networks.append(ipaddress.ip_network(cidr, strict=False))
        except ValueError:
            _log_schema_rejection("address", "execution_node_address_rejected")
            raise ExecutionNodeError(
                "execution_node_address_rejected",
                "Worker address is not allowed.",
                details={"reason": "cidr"},
            ) from None

    try:
        ip = ipaddress.ip_address(host_lower)
    except ValueError:
        ip = None

    if ip is not None:
        if str(ip) in _METADATA_HOSTS or not _ip_is_allowed(ip, networks):
            _log_schema_rejection("address", "execution_node_address_rejected")
            raise ExecutionNodeError(
                "execution_node_address_rejected",
                "Worker address is not allowed.",
                details={"reason": "ip"},
            )
        return text.rstrip("/")

    if host_lower in _BUILTIN_HOSTNAMES or allow_hostname:
        return text.rstrip("/")

    _log_schema_rejection("address", "execution_node_address_rejected")
    raise ExecutionNodeError(
        "execution_node_address_rejected",
        "Worker address is not allowed.",
        details={"reason": "hostname"},
    )


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _reject_forbidden_keys(cls, value: Any) -> Any:
        if isinstance(value, dict):
            try:
                reject_execution_node_forbidden_payload(value, model_name=cls.__name__)
            except ExecutionNodeError as exc:
                raise ValueError(exc.code) from exc
        return value


class ExecutionNodeHardwareV1(_Strict):
    device_class: DeviceClass = "unknown"
    accelerator_name: str | None = Field(default=None, max_length=80)
    cpu_count: int | None = Field(default=None, ge=1, le=4096)

    @field_validator("accelerator_name")
    @classmethod
    def _no_path_separator(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if "/" in value or "\\" in value:
            raise ValueError("execution_node_forbidden_payload")
        return value


class ExecutionNodeHealthV1(_Strict):
    status: HealthStatus
    detail: str | None = Field(default=None, max_length=64)
    latency_ms: int | None = Field(default=None, ge=0, le=600_000)


class ExecutionNodeResourcesV1(_Strict):
    memory_total_mb: int | None = Field(default=None, ge=0, le=16_777_216)
    memory_available_mb: int | None = Field(default=None, ge=0, le=16_777_216)
    active_tasks: int = Field(default=0, ge=0, le=10_000)
    max_concurrency: int = Field(default=1, ge=1, le=64)


class ExecutionNodeInstalledModelV1(_Strict):
    """Public catalog row. Never secrets or weight paths."""

    id: str = Field(..., min_length=1, max_length=160)
    display_name: str = Field(..., min_length=1, max_length=200)
    primary_capability: str = Field(..., min_length=1, max_length=64)
    runtime: str = Field(..., min_length=1, max_length=64)
    locality: ModelLocalityPublic = "local"
    status: HealthStatus = "ready"
    model_version: str | None = Field(default=None, max_length=120)
    supported_operations: list[str] = Field(default_factory=list, max_length=32)


class ExecutionNodeV1(_Strict):
    schema_version: Literal["execution.node.v1"] = NODE_SCHEMA
    node_id: str = Field(..., min_length=21, max_length=21)
    display_name: str = Field(..., min_length=1, max_length=80)
    address: str = Field(..., min_length=1, max_length=256)
    role: ExecutionNodeRole = "worker"
    capabilities: list[str] = Field(..., min_length=1, max_length=16)
    hardware: ExecutionNodeHardwareV1 = Field(default_factory=ExecutionNodeHardwareV1)
    available_runtimes: list[str] = Field(default_factory=list, max_length=16)
    installed_models: list[ExecutionNodeInstalledModelV1] = Field(default_factory=list, max_length=32)
    health: ExecutionNodeHealthV1
    resources: ExecutionNodeResourcesV1 = Field(default_factory=ExecutionNodeResourcesV1)
    availability: NodeAvailability = "unavailable"
    last_heartbeat_at: str | None = Field(default=None, max_length=40)
    document_revision: int = Field(..., ge=1)

    @field_validator("node_id")
    @classmethod
    def _node_id(cls, value: str) -> str:
        if not NODE_ID_PATTERN.fullmatch(value):
            raise ValueError("execution_node_not_found")
        return value

    @field_validator("capabilities", "available_runtimes")
    @classmethod
    def _non_empty_strings(cls, value: list[str]) -> list[str]:
        for item in value:
            if not isinstance(item, str) or not item or len(item) > 64:
                raise ValueError("execution_node_forbidden_payload")
        return value


class ExecutionNodeRegistrationV1(_Strict):
    schema_version: Literal["execution.node.registration.v1"] = REGISTRATION_SCHEMA
    node_id: str = Field(..., min_length=21, max_length=21)
    display_name: str = Field(..., min_length=1, max_length=80)
    address: str = Field(..., min_length=1, max_length=256)
    role: ExecutionNodeRole = "worker"
    capabilities: list[str] = Field(..., min_length=1, max_length=16)
    hardware: ExecutionNodeHardwareV1 = Field(default_factory=ExecutionNodeHardwareV1)
    available_runtimes: list[str] = Field(default_factory=list, max_length=16)
    installed_models: list[ExecutionNodeInstalledModelV1] = Field(default_factory=list, max_length=32)
    resources: ExecutionNodeResourcesV1 = Field(default_factory=ExecutionNodeResourcesV1)
    health: ExecutionNodeHealthV1 | None = None

    @field_validator("node_id")
    @classmethod
    def _node_id(cls, value: str) -> str:
        if not NODE_ID_PATTERN.fullmatch(value):
            raise ValueError("execution_node_not_found")
        return value


class ExecutionNodeHeartbeatV1(_Strict):
    schema_version: Literal["execution.node.heartbeat.v1"] = HEARTBEAT_SCHEMA
    node_id: str = Field(..., min_length=21, max_length=21)
    capabilities: list[str] | None = Field(default=None, max_length=16)
    installed_models: list[ExecutionNodeInstalledModelV1] | None = Field(default=None, max_length=32)
    health: ExecutionNodeHealthV1
    resources: ExecutionNodeResourcesV1 = Field(default_factory=ExecutionNodeResourcesV1)
    availability: HeartbeatAvailability
    document_revision: int = Field(..., ge=1)

    @field_validator("node_id")
    @classmethod
    def _node_id(cls, value: str) -> str:
        if not NODE_ID_PATTERN.fullmatch(value):
            raise ValueError("execution_node_not_found")
        return value


class ExecutionNodeCatalogV1(_Strict):
    schema_version: Literal["execution.node.catalog.v1"] = CATALOG_SCHEMA
    node_id: str = Field(..., min_length=21, max_length=21)
    installed_models: list[ExecutionNodeInstalledModelV1] = Field(default_factory=list, max_length=32)
    generated_at: str | None = Field(default=None, max_length=40)

    @field_validator("node_id")
    @classmethod
    def _node_id(cls, value: str) -> str:
        if not NODE_ID_PATTERN.fullmatch(value):
            raise ValueError("execution_node_not_found")
        return value


class ExecutionTaskV1(_Strict):
    schema_version: Literal["execution.task.v1"] = TASK_SCHEMA
    task_id: str = Field(..., min_length=21, max_length=21)
    operation: TaskOperation = "complete_text"
    model_id: str = Field(..., min_length=1, max_length=160)
    input_text: str = Field(..., min_length=1, max_length=500_000)
    purpose: str | None = Field(default=None, max_length=64)
    operation_run_id: str | None = Field(default=None, max_length=80)
    timeout_seconds: int | None = Field(default=None, ge=1, le=600)

    @field_validator("task_id")
    @classmethod
    def _task_id(cls, value: str) -> str:
        if not TASK_ID_PATTERN.fullmatch(value):
            raise ValueError("execution_node_task_not_found")
        return value


class ExecutionTaskResultV1(_Strict):
    schema_version: Literal["execution.task.result.v1"] = TASK_RESULT_SCHEMA
    task_id: str = Field(..., min_length=21, max_length=21)
    status: TaskResultStatus
    output_text: str | None = Field(default=None, max_length=500_000)
    failure_code: str | None = Field(default=None, max_length=64)
    latency_ms: int | None = Field(default=None, ge=0, le=600_000)

    @field_validator("task_id")
    @classmethod
    def _task_id(cls, value: str) -> str:
        if not TASK_ID_PATTERN.fullmatch(value):
            raise ValueError("execution_node_task_not_found")
        return value

    @model_validator(mode="after")
    def _status_fields(self) -> ExecutionTaskResultV1:
        if self.status == "completed" and (self.output_text is None or self.output_text == ""):
            raise ValueError("execution_node_unavailable")
        if self.status == "failed" and not self.failure_code:
            raise ValueError("execution_node_unavailable")
        return self
