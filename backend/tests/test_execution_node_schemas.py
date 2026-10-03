"""Schema contracts for execution node documents and address allowlist."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.execution_node_schemas import (
    ExecutionNodeCatalogV1,
    ExecutionNodeError,
    ExecutionNodeHeartbeatV1,
    ExecutionNodeRegistrationV1,
    ExecutionNodeV1,
    ExecutionTaskResultV1,
    ExecutionTaskV1,
    build_execution_node_model_id,
    map_execution_node_error_to_http,
    parse_execution_node_model_id,
    validate_execution_node_address,
)

_NODE_ID = "node_0123456789abcdef"
_TASK_ID = "task_fedcba9876543210"


def _model_row(**overrides) -> dict:
    row = {
        "id": "fake:language",
        "display_name": "Fake language",
        "primary_capability": "language_planner",
        "runtime": "fake",
        "locality": "local",
        "status": "ready",
        "supported_operations": ["generate"],
    }
    row.update(overrides)
    return row


def _node(**overrides) -> dict:
    payload = {
        "schema_version": "execution.node.v1",
        "node_id": _NODE_ID,
        "display_name": "Spare box",
        "address": "http://192.168.1.20:8000",
        "role": "worker",
        "capabilities": ["language_planner"],
        "hardware": {"device_class": "cpu", "cpu_count": 8},
        "available_runtimes": ["fake", "local_openai_compatible"],
        "installed_models": [_model_row()],
        "health": {"status": "ready", "detail": "ok", "latency_ms": 12},
        "resources": {"active_tasks": 0, "max_concurrency": 2},
        "availability": "available",
        "last_heartbeat_at": "2026-10-03T06:00:00Z",
        "document_revision": 1,
    }
    payload.update(overrides)
    return payload


def test_node_accepts_rfc1918_worker() -> None:
    node = ExecutionNodeV1.model_validate(_node())
    assert node.node_id == _NODE_ID
    assert node.installed_models[0].id == "fake:language"
    assert node.availability == "available"


def test_registration_and_heartbeat_round_trip_shapes() -> None:
    registration = ExecutionNodeRegistrationV1.model_validate(
        {
            "schema_version": "execution.node.registration.v1",
            "node_id": _NODE_ID,
            "display_name": "Spare box",
            "address": "http://10.0.0.5:9000",
            "capabilities": ["language_planner"],
            "installed_models": [_model_row()],
            "resources": {"max_concurrency": 1, "active_tasks": 0},
        }
    )
    assert registration.role == "worker"
    heartbeat = ExecutionNodeHeartbeatV1.model_validate(
        {
            "schema_version": "execution.node.heartbeat.v1",
            "node_id": _NODE_ID,
            "health": {"status": "ready"},
            "resources": {"active_tasks": 1, "max_concurrency": 1},
            "availability": "busy",
            "document_revision": 2,
            "installed_models": [_model_row()],
        }
    )
    assert heartbeat.availability == "busy"


def test_catalog_accepts_installed_models() -> None:
    catalog = ExecutionNodeCatalogV1.model_validate(
        {
            "schema_version": "execution.node.catalog.v1",
            "node_id": _NODE_ID,
            "installed_models": [_model_row()],
            "generated_at": "2026-10-03T06:00:00Z",
        }
    )
    assert len(catalog.installed_models) == 1


def test_task_and_result_accept_input_output_text() -> None:
    task = ExecutionTaskV1.model_validate(
        {
            "schema_version": "execution.task.v1",
            "task_id": _TASK_ID,
            "operation": "complete_text",
            "model_id": "fake:language",
            "input_text": "compose a motif",
            "purpose": "generate",
        }
    )
    assert task.input_text.startswith("compose")
    result = ExecutionTaskResultV1.model_validate(
        {
            "schema_version": "execution.task.result.v1",
            "task_id": _TASK_ID,
            "status": "completed",
            "output_text": "fake-execution-node:generate",
            "latency_ms": 40,
        }
    )
    assert result.status == "completed"


@pytest.mark.parametrize(
    "forbidden_key,value",
    [
        ("events", []),
        ("notes", []),
        ("pitch", 60),
        ("composition", {}),
        ("composition_json", "{}"),
        ("api_key", "secret"),
        ("authorization", "Bearer x"),
        ("shell", "bash"),
        ("command", "ls"),
        ("argv", ["ls"]),
        ("subprocess", True),
        ("prompt", "hidden"),
    ],
)
def test_forbidden_keys_rejected(forbidden_key: str, value: object) -> None:
    payload = _node()
    payload[forbidden_key] = value
    with pytest.raises(ValidationError, match="execution_node_forbidden_payload"):
        ExecutionNodeV1.model_validate(payload)


def test_nested_forbidden_key_rejected() -> None:
    payload = _node()
    payload["hardware"] = {"device_class": "cpu", "shell": "bash"}
    with pytest.raises(ValidationError, match="execution_node_forbidden_payload"):
        ExecutionNodeV1.model_validate(payload)


def test_heartbeat_rejects_unavailable_availability() -> None:
    with pytest.raises(ValidationError):
        ExecutionNodeHeartbeatV1.model_validate(
            {
                "schema_version": "execution.node.heartbeat.v1",
                "node_id": _NODE_ID,
                "health": {"status": "ready"},
                "availability": "unavailable",
                "document_revision": 1,
            }
        )


def test_model_id_parse_and_build() -> None:
    built = build_execution_node_model_id(_NODE_ID, "local:llama:q4")
    assert built == "node:0123456789abcdef:local:llama:q4"
    node_hex, local_id = parse_execution_node_model_id(built)
    assert node_hex == "0123456789abcdef"
    assert local_id == "local:llama:q4"


def test_model_id_parse_rejects_bad_shape() -> None:
    with pytest.raises(ExecutionNodeError) as exc:
        parse_execution_node_model_id("openai:gpt")
    assert exc.value.code == "execution_node_not_found"


def test_error_http_map() -> None:
    status, detail = map_execution_node_error_to_http(
        ExecutionNodeError("execution_node_unauthorized", "Denied")
    )
    assert status == 401
    assert detail["code"] == "execution_node_unauthorized"


@pytest.mark.parametrize(
    "address",
    [
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://10.1.2.3:9000",
        "http://192.168.0.10",
        "http://172.16.5.5:8080",
        "http://execution-node.fake",
        "https://[::1]:8443",
    ],
)
def test_address_allowlist_accepts(address: str) -> None:
    assert validate_execution_node_address(address).startswith(("http://", "https://"))


@pytest.mark.parametrize(
    "address",
    [
        "http://user:pass@192.168.1.1:8000",
        "http://169.254.1.1:8000",
        "http://169.254.169.254/",
        "http://metadata.google.internal",
        "ftp://192.168.1.1",
        "http://example.com",
        "http://8.8.8.8",
        "http://192.168.1.1/admin",
    ],
)
def test_address_allowlist_rejects(address: str) -> None:
    with pytest.raises(ExecutionNodeError) as exc:
        validate_execution_node_address(address)
    assert exc.value.code == "execution_node_address_rejected"


def test_address_allowlist_hostname_opt_in() -> None:
    with pytest.raises(ExecutionNodeError):
        validate_execution_node_address("http://gpu-box.lan:8000")
    allowed = validate_execution_node_address(
        "http://gpu-box.lan:8000",
        allow_hostname=True,
    )
    assert allowed == "http://gpu-box.lan:8000"


def test_address_allowlist_extra_cidr() -> None:
    allowed = validate_execution_node_address(
        "http://100.64.0.5:8000",
        extra_cidrs=["100.64.0.0/10"],
    )
    assert allowed == "http://100.64.0.5:8000"
