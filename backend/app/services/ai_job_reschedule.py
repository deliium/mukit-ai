"""Bounded reschedule after ExecutionNode / schedule-path invoke failure.

Never escalates trust_boundary. Never reschedules ``operation_cancelled``.
Does not write composition events.
"""

from __future__ import annotations

import logging
from typing import Mapping

from app.ai_runtime.capabilities import default_capability_for_operation
from app.ai_runtime.errors import ModelUnavailableError
from app.ai_runtime.operations import AiOperation
from app.ai_runtime.registry import get_model
from app.ai_runtime.types import ResolvedModel
from app.scheduling_schemas import (
    TRUST_RANK,
    SchedulingJobV1,
    SchedulingPolicyV1,
    trust_boundary_for_runtime,
)
from app.services.ai_job_scheduler import schedule_ai_job
from app.services.scheduling_candidates import build_scheduling_candidates
from app.services.scheduling_policy_store import get_policy

logger = logging.getLogger(__name__)

_RESCHEDULE_CODES = frozenset({"model_unavailable", "execution_node_busy", "execution_node_unavailable"})


def should_wrap_for_reschedule(resolved: ResolvedModel) -> bool:
    """True when invoke may retry on another eligible candidate."""
    return (
        resolved.resolution_path == "schedule"
        or resolved.descriptor.runtime == "execution_node"
    )


def is_reschedulable_failure(exc: BaseException) -> bool:
    if not isinstance(exc, ModelUnavailableError):
        return False
    code = getattr(exc, "code", None) or "model_unavailable"
    if code == "operation_cancelled":
        return False
    return code in _RESCHEDULE_CODES or code == "model_unavailable"


def max_attempts_for(resolved: ResolvedModel, policy: SchedulingPolicyV1 | None = None) -> int:
    active = policy or get_policy()
    return int(active.max_attempts)


def failed_node_id(resolved: ResolvedModel) -> str | None:
    if resolved.schedule_node_id:
        return resolved.schedule_node_id
    limits = resolved.descriptor.limits or {}
    node_id = limits.get("execution_node_id")
    return str(node_id) if isinstance(node_id, str) and node_id.startswith("node_") else None


def resolve_reschedule_attempt(
    previous: ResolvedModel,
    *,
    exclude_node_ids: list[str],
    exclude_model_ids: list[str],
    attempt_index: int,
    env: Mapping[str, str] | None = None,
    privacy_class: str = "private",
) -> ResolvedModel:
    """Pick the next candidate without exceeding the previous trust boundary."""
    policy = get_policy(env=env)
    operation = previous.operation
    if not isinstance(operation, AiOperation):
        operation = AiOperation(str(operation))

    job = SchedulingJobV1(
        operation=str(operation),
        required_capability=str(default_capability_for_operation(operation)),
        privacy_class=privacy_class if privacy_class in {"private", "allow_public"} else "private",
        exclude_node_ids=exclude_node_ids[:32],
        exclude_model_ids=exclude_model_ids[:64],
    )
    # Keep the current registry; node availability is refreshed via list_nodes.
    candidates = build_scheduling_candidates(env=env, reload=False)
    decision = schedule_ai_job(job, candidates, policy, attempt_index=attempt_index)
    if not decision.selected_model_id:
        logger.warning(
            "reschedule found no eligible candidate",
            extra={
                "operation": str(operation),
                "attempt_index": attempt_index,
                "reason_codes": decision.reason_codes,
            },
        )
        raise ModelUnavailableError(
            f"No eligible reschedule candidate for {operation}",
            code="model_unavailable",
        )

    prior_boundary = previous.schedule_trust_boundary or _boundary_for_resolved(previous)
    prior_rank = TRUST_RANK.get(str(prior_boundary), 0)
    next_rank = TRUST_RANK.get(str(decision.trust_boundary or "controller_local"), 99)
    if next_rank > prior_rank:
        logger.warning(
            "reschedule refused trust escalation",
            extra={
                "prior_trust": prior_boundary,
                "next_trust": decision.trust_boundary,
                "attempt_index": attempt_index,
            },
        )
        raise ModelUnavailableError(
            "Reschedule would escalate trust boundary",
            code="model_unavailable",
        )

    descriptor = get_model(decision.selected_model_id, env=env)
    logger.info(
        "reschedule selected",
        extra={
            "operation": str(operation),
            "selected_model_id": descriptor.id,
            "selected_node_id": decision.selected_node_id,
            "trust_boundary": decision.trust_boundary,
            "attempt_index": attempt_index,
            "reason_codes": decision.reason_codes,
        },
    )
    return ResolvedModel(
        descriptor=descriptor,
        operation=previous.operation,
        resolution_path="schedule",
        requested_model_id=previous.requested_model_id,
        resolved_model_id=descriptor.id,
        fallback_applied=False,
        generation_parameters=previous.generation_parameters,
        schedule_policy=decision.policy_mode,
        schedule_reason_codes=tuple(decision.reason_codes),
        schedule_attempt=attempt_index,
        schedule_trust_boundary=decision.trust_boundary,
        schedule_node_id=decision.selected_node_id,
    )


def _boundary_for_resolved(resolved: ResolvedModel) -> str:
    limits = resolved.descriptor.limits or {}
    return trust_boundary_for_runtime(
        runtime=str(resolved.descriptor.runtime),
        locality=str(resolved.descriptor.locality),
        has_execution_node_id=bool(limits.get("execution_node_id")),
    )
