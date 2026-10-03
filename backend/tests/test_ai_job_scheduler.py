"""Acceptance matrix for the pure AI job scheduler."""

from __future__ import annotations

from app.scheduling_schemas import (
    SchedulingCandidateV1,
    SchedulingJobV1,
    SchedulingPolicyV1,
)
from app.services.ai_job_scheduler import schedule_ai_job

NODE_B = "node_bbbbbbbbbbbbbbbb"


def _candidate_a() -> SchedulingCandidateV1:
    return SchedulingCandidateV1.model_validate(
        {
            "model_id": "local:llama-planner",
            "runtime": "local_openai_compatible",
            "primary_capability": "language_planner",
            "secondary_capabilities": [],
            "supported_operations": ["generate_planner", "generate"],
            "trust_boundary": "controller_local",
            "device_class": "igpu",
            "memory_available_mb": 4096,
            "memory_total_mb": 8192,
            "estimated_latency_ms": 40,
            "availability": "available",
            "status": "ready",
            "locality": "local",
        }
    )


def _candidate_b(**overrides) -> SchedulingCandidateV1:
    payload = {
        "model_id": "node:bbbbbbbbbbbbbbbb:symbolic",
        "runtime": "execution_node",
        "primary_capability": "symbolic_composer",
        "secondary_capabilities": ["language_planner"],
        "supported_operations": ["generate_composer", "generate_planner", "generate"],
        "trust_boundary": "trusted_lan",
        "node_id": NODE_B,
        "device_class": "dgpu",
        "memory_available_mb": 48000,
        "memory_total_mb": 65536,
        "estimated_latency_ms": 25,
        "availability": "available",
        "status": "ready",
        "locality": "remote",
    }
    payload.update(overrides)
    return SchedulingCandidateV1.model_validate(payload)


def _candidate_c() -> SchedulingCandidateV1:
    return SchedulingCandidateV1.model_validate(
        {
            "model_id": "openai:gpt",
            "runtime": "openai_compatible",
            "primary_capability": "language_planner",
            "secondary_capabilities": [],
            "supported_operations": ["generate_planner", "generate"],
            "trust_boundary": "public_cloud",
            "device_class": "unknown",
            "estimated_latency_ms": 120,
            "availability": "available",
            "status": "ready",
            "locality": "remote",
        }
    )


def _fixtures() -> list[SchedulingCandidateV1]:
    return [_candidate_a(), _candidate_b(), _candidate_c()]


def _planner_job(**overrides) -> SchedulingJobV1:
    payload = {
        "operation": "generate_planner",
        "required_capability": "language_planner",
        "privacy_class": "private",
    }
    payload.update(overrides)
    return SchedulingJobV1.model_validate(payload)


def test_prefer_local_private_planner_selects_controller_local() -> None:
    decision = schedule_ai_job(
        _planner_job(),
        _fixtures(),
        SchedulingPolicyV1(mode="prefer_local", allow_public_cloud=False),
    )
    assert decision.selected_model_id == "local:llama-planner"
    assert decision.trust_boundary == "controller_local"
    assert decision.selected_model_id != "openai:gpt"


def test_composer_dgpu_memory_selects_desktop_node() -> None:
    job = SchedulingJobV1.model_validate(
        {
            "operation": "generate_composer",
            "required_capability": "symbolic_composer",
            "estimated_memory_mb": 16000,
            "prefer_device_class": "dgpu",
        }
    )
    decision = schedule_ai_job(
        job,
        _fixtures(),
        SchedulingPolicyV1(mode="prefer_local", allow_public_cloud=False),
    )
    assert decision.selected_model_id == "node:bbbbbbbbbbbbbbbb:symbolic"
    assert decision.selected_node_id == NODE_B


def test_fastest_available_selects_lowest_latency_non_public() -> None:
    decision = schedule_ai_job(
        _planner_job(),
        _fixtures(),
        SchedulingPolicyV1(mode="fastest_available", allow_public_cloud=False),
    )
    assert decision.selected_model_id == "node:bbbbbbbbbbbbbbbb:symbolic"
    assert decision.selected_model_id != "openai:gpt"


def test_fixed_node_selects_b_and_refuses_when_unavailable() -> None:
    policy = SchedulingPolicyV1(
        mode="fixed_node",
        fixed_node_id=NODE_B,
        allow_public_cloud=False,
    )
    ok = schedule_ai_job(_planner_job(), _fixtures(), policy)
    assert ok.selected_node_id == NODE_B

    down = [_candidate_a(), _candidate_b(availability="unavailable"), _candidate_c()]
    refused = schedule_ai_job(_planner_job(), down, policy)
    assert refused.selected_model_id is None
    assert "fixed_node_unavailable" in refused.reason_codes


def test_memory_safe_refuses_small_local_and_selects_b() -> None:
    job = _planner_job(estimated_memory_mb=30000)
    decision = schedule_ai_job(
        job,
        _fixtures(),
        SchedulingPolicyV1(mode="memory_safe", allow_public_cloud=False),
    )
    assert decision.selected_model_id == "node:bbbbbbbbbbbbbbbb:symbolic"


def test_reschedule_excludes_failed_node_never_public() -> None:
    job = _planner_job(exclude_node_ids=[NODE_B])
    decision = schedule_ai_job(
        job,
        _fixtures(),
        SchedulingPolicyV1(mode="prefer_local", allow_public_cloud=False),
        attempt_index=2,
    )
    assert decision.selected_model_id == "local:llama-planner"
    assert decision.selected_model_id != "openai:gpt"
    assert "excluded_failed_node" in decision.reason_codes


def test_public_cloud_only_when_both_allow() -> None:
    blocked = schedule_ai_job(
        _planner_job(privacy_class="private"),
        _fixtures(),
        SchedulingPolicyV1(mode="fastest_available", allow_public_cloud=True),
    )
    assert blocked.selected_model_id != "openai:gpt"

    # Make C the fastest eligible under allow_public + allow_public privacy.
    fast_cloud = SchedulingCandidateV1.model_validate(
        {
            **_candidate_c().model_dump(),
            "estimated_latency_ms": 5,
        }
    )
    allowed = schedule_ai_job(
        _planner_job(privacy_class="allow_public"),
        [_candidate_a(), _candidate_b(), fast_cloud],
        SchedulingPolicyV1(mode="fastest_available", allow_public_cloud=True),
    )
    assert allowed.selected_model_id == "openai:gpt"


def test_empty_supported_operations_ineligible() -> None:
    only_empty = SchedulingCandidateV1.model_validate(
        {
            **_candidate_a().model_dump(),
            "supported_operations": [],
        }
    )
    decision = schedule_ai_job(
        _planner_job(),
        [only_empty, _candidate_c()],
        SchedulingPolicyV1(mode="prefer_local", allow_public_cloud=False),
    )
    assert decision.selected_model_id is None
    assert "no_eligible_candidate" in decision.reason_codes


def test_prefer_device_class_is_hard_filter() -> None:
    job = _planner_job(prefer_device_class="dgpu")
    decision = schedule_ai_job(
        job,
        _fixtures(),
        SchedulingPolicyV1(mode="prefer_local", allow_public_cloud=False),
    )
    assert decision.selected_model_id == "node:bbbbbbbbbbbbbbbb:symbolic"


def test_priority_does_not_reorder_candidates() -> None:
    low = schedule_ai_job(
        _planner_job(priority=0),
        _fixtures(),
        SchedulingPolicyV1(mode="prefer_local", allow_public_cloud=False),
    )
    high = schedule_ai_job(
        _planner_job(priority=100),
        _fixtures(),
        SchedulingPolicyV1(mode="prefer_local", allow_public_cloud=False),
    )
    assert low.selected_model_id == high.selected_model_id == "local:llama-planner"
    assert "priority_ignored_v1" in high.reason_codes


def test_busy_candidate_ineligible() -> None:
    busy_b = _candidate_b(availability="busy")
    decision = schedule_ai_job(
        _planner_job(),
        [_candidate_a(), busy_b, _candidate_c()],
        SchedulingPolicyV1(mode="fastest_available", allow_public_cloud=False),
    )
    assert decision.selected_model_id == "local:llama-planner"
