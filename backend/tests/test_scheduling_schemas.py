"""Schema contracts for scheduling documents."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.scheduling_schemas import (
    SchedulingCandidateV1,
    SchedulingError,
    SchedulingJobV1,
    SchedulingPolicyV1,
    map_scheduling_error_to_http,
    reject_scheduling_forbidden_payload,
    trust_boundary_for_runtime,
)


def _candidate(**overrides) -> dict:
    payload = {
        "model_id": "local:llama",
        "runtime": "local_openai_compatible",
        "primary_capability": "language_planner",
        "secondary_capabilities": [],
        "supported_operations": ["generate_planner"],
        "trust_boundary": "controller_local",
        "device_class": "igpu",
        "memory_available_mb": 4096,
        "memory_total_mb": 8192,
        "estimated_latency_ms": 40,
        "availability": "available",
        "status": "ready",
        "locality": "local",
    }
    payload.update(overrides)
    return payload


def test_policy_accepts_prefer_local_defaults() -> None:
    policy = SchedulingPolicyV1.model_validate({"mode": "prefer_local"})
    assert policy.allow_public_cloud is False
    assert policy.max_attempts == 2
    assert policy.document_revision == 1


def test_fixed_node_requires_fixed_node_id() -> None:
    with pytest.raises(ValidationError, match="scheduling_invalid_policy"):
        SchedulingPolicyV1.model_validate({"mode": "fixed_node"})


def test_fixed_node_accepts_node_id() -> None:
    policy = SchedulingPolicyV1.model_validate(
        {
            "mode": "fixed_node",
            "fixed_node_id": "node_0123456789abcdef",
        }
    )
    assert policy.fixed_node_id == "node_0123456789abcdef"


def test_candidate_accepts_empty_supported_operations() -> None:
    candidate = SchedulingCandidateV1.model_validate(
        _candidate(supported_operations=[])
    )
    assert candidate.supported_operations == []


def test_forbidden_prompt_key_rejected() -> None:
    with pytest.raises(ValidationError, match="scheduling_forbidden_payload"):
        SchedulingJobV1.model_validate(
            {
                "operation": "generate_planner",
                "required_capability": "language_planner",
                "prompt": "secret",
            }
        )


def test_forbidden_events_key_rejected() -> None:
    with pytest.raises(ValidationError, match="scheduling_forbidden_payload"):
        SchedulingCandidateV1.model_validate(_candidate(events=[]))


def test_reject_scheduling_forbidden_payload_raises_domain_error() -> None:
    with pytest.raises(SchedulingError) as exc_info:
        reject_scheduling_forbidden_payload(
            {"input_text": "x"},
            model_name="test",
        )
    assert exc_info.value.code == "scheduling_forbidden_payload"
    status, detail = map_scheduling_error_to_http(exc_info.value)
    assert status == 422
    assert detail["code"] == "scheduling_forbidden_payload"


def test_execution_node_runtime_is_trusted_lan_even_when_locality_remote() -> None:
    assert (
        trust_boundary_for_runtime(
            runtime="execution_node",
            locality="remote",
        )
        == "trusted_lan"
    )


def test_remote_openai_is_public_cloud() -> None:
    assert (
        trust_boundary_for_runtime(
            runtime="openai_compatible",
            locality="remote",
        )
        == "public_cloud"
    )
