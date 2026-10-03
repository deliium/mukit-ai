"""Pure deterministic AI job scheduler over candidate snapshots.

No FastAPI, SQLite, httpx, or LLM imports. Never writes composition events.
"""

from __future__ import annotations

import logging
from typing import Sequence

from app.scheduling_schemas import (
    TRUST_RANK,
    SchedulingCandidateV1,
    SchedulingDecisionV1,
    SchedulingJobV1,
    SchedulingPolicyV1,
)

logger = logging.getLogger(__name__)

_MISSING_LATENCY = 1_000_000_000


def schedule_ai_job(
    job: SchedulingJobV1,
    candidates: Sequence[SchedulingCandidateV1],
    policy: SchedulingPolicyV1,
    *,
    attempt_index: int = 1,
) -> SchedulingDecisionV1:
    """Filter and total-order candidates for one placement attempt."""
    logger.debug(
        "schedule_ai_job start",
        extra={
            "operation": job.operation,
            "required_capability": job.required_capability,
            "policy_mode": policy.mode,
            "candidate_count": len(candidates),
            "attempt_index": attempt_index,
            "privacy_class": job.privacy_class,
        },
    )

    reasons: list[str] = []
    if job.priority != 50:
        reasons.append("priority_ignored_v1")
    if job.exclude_node_ids or job.exclude_model_ids:
        reasons.append("excluded_failed_node")

    eligible = [
        candidate
        for candidate in candidates
        if _is_eligible(job, candidate, policy, reasons)
    ]

    if policy.mode == "fixed_node":
        eligible = _filter_fixed_node(eligible, policy)
        if not eligible:
            decision = SchedulingDecisionV1(
                selected_model_id=None,
                selected_node_id=None,
                policy_mode=policy.mode,
                trust_boundary=None,
                reason_codes=_finalize_reasons(reasons, ["fixed_node_unavailable"]),
                eligible_count=0,
                attempt_index=attempt_index,
            )
            logger.info(
                "schedule_ai_job fixed_node unavailable",
                extra={
                    "policy_mode": policy.mode,
                    "eligible_count": 0,
                    "attempt_index": attempt_index,
                    "reason_codes": decision.reason_codes,
                },
            )
            return decision

    if not eligible:
        decision = SchedulingDecisionV1(
            selected_model_id=None,
            selected_node_id=None,
            policy_mode=policy.mode,
            trust_boundary=None,
            reason_codes=_finalize_reasons(reasons, ["no_eligible_candidate"]),
            eligible_count=0,
            attempt_index=attempt_index,
        )
        logger.info(
            "schedule_ai_job no eligible candidate",
            extra={
                "policy_mode": policy.mode,
                "operation": job.operation,
                "eligible_count": 0,
                "attempt_index": attempt_index,
                "reason_codes": decision.reason_codes,
            },
        )
        return decision

    ordered = sorted(eligible, key=lambda item: _sort_key(job, item, policy))
    selected = ordered[0]
    mode_reason = {
        "prefer_local": "prefer_local",
        "fastest_available": "lowest_latency",
        "memory_safe": "memory_headroom",
        "fixed_node": "fixed_node",
    }[policy.mode]
    decision = SchedulingDecisionV1(
        selected_model_id=selected.model_id,
        selected_node_id=selected.node_id,
        policy_mode=policy.mode,
        trust_boundary=selected.trust_boundary,
        reason_codes=_finalize_reasons(reasons, [mode_reason, "capability_match"]),
        eligible_count=len(eligible),
        attempt_index=attempt_index,
    )
    logger.info(
        "schedule_ai_job selected",
        extra={
            "policy_mode": policy.mode,
            "operation": job.operation,
            "required_capability": job.required_capability,
            "selected_model_id": decision.selected_model_id,
            "selected_node_id": decision.selected_node_id,
            "trust_boundary": decision.trust_boundary,
            "eligible_count": decision.eligible_count,
            "attempt_index": attempt_index,
            "reason_codes": decision.reason_codes,
            "privacy_class": job.privacy_class,
        },
    )
    return decision


def _finalize_reasons(collected: list[str], suffix: list[str]) -> list[str]:
    seen: list[str] = []
    for code in [*collected, *suffix]:
        if code not in seen:
            seen.append(code)
    return seen[:16] or ["no_eligible_candidate"]


def _is_eligible(
    job: SchedulingJobV1,
    candidate: SchedulingCandidateV1,
    policy: SchedulingPolicyV1,
    reasons: list[str],
) -> bool:
    if candidate.status != "ready":
        return False
    if candidate.availability != "available":
        return False
    if not candidate.supported_operations:
        return False
    if job.operation not in candidate.supported_operations:
        return False
    caps = {candidate.primary_capability, *candidate.secondary_capabilities}
    if job.required_capability not in caps:
        return False

    block_public = job.privacy_class == "private" or not policy.allow_public_cloud
    if block_public and candidate.trust_boundary == "public_cloud":
        if "trust_filtered_public" not in reasons:
            reasons.append("trust_filtered_public")
        return False

    if job.estimated_memory_mb is not None:
        if candidate.memory_available_mb is None:
            return False
        if candidate.memory_available_mb < job.estimated_memory_mb:
            return False

    if job.prefer_device_class is not None:
        if candidate.device_class != job.prefer_device_class:
            return False

    if candidate.node_id and candidate.node_id in job.exclude_node_ids:
        return False
    if candidate.model_id in job.exclude_model_ids:
        return False

    return True


def _filter_fixed_node(
    eligible: list[SchedulingCandidateV1],
    policy: SchedulingPolicyV1,
) -> list[SchedulingCandidateV1]:
    filtered = [
        item for item in eligible if item.node_id == policy.fixed_node_id
    ]
    if policy.fixed_model_id:
        filtered = [item for item in filtered if item.model_id == policy.fixed_model_id]
    return filtered


def _sort_key(
    job: SchedulingJobV1,
    candidate: SchedulingCandidateV1,
    policy: SchedulingPolicyV1,
) -> tuple:
    trust = TRUST_RANK.get(candidate.trust_boundary, 99)
    latency = (
        candidate.estimated_latency_ms
        if candidate.estimated_latency_ms is not None
        else _MISSING_LATENCY
    )
    memory_available = (
        candidate.memory_available_mb
        if candidate.memory_available_mb is not None
        else -1
    )

    if policy.mode == "prefer_local":
        return (trust, latency, -memory_available, candidate.model_id)
    if policy.mode == "fastest_available":
        return (latency, trust, candidate.model_id)
    if policy.mode == "memory_safe":
        estimated = job.estimated_memory_mb if job.estimated_memory_mb is not None else 0
        if candidate.memory_available_mb is None:
            headroom_key = -_MISSING_LATENCY
        else:
            headroom_key = candidate.memory_available_mb - estimated
        # Descending headroom → negate for ascending sort.
        return (-headroom_key if candidate.memory_available_mb is not None else _MISSING_LATENCY, trust, latency, candidate.model_id)
    # fixed_node: capability already matched; prefer latency then model id.
    primary_match = 0 if candidate.primary_capability == job.required_capability else 1
    return (primary_match, latency, candidate.model_id)
