"""HTTP routes for capability-aware AI job scheduling policy and preview.

Flag off → 404. Preview never invokes a model and never logs prompts.
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException

from app.scheduling_schemas import (
    SchedulingDecisionV1,
    SchedulingError,
    SchedulingPolicyPut,
    SchedulingPolicyV1,
    SchedulingPreviewRequest,
    map_scheduling_error_to_http,
)
from app.scheduling_settings import scheduling_enabled
from app.services import scheduling_policy_store as store
from app.services.ai_job_scheduler import schedule_ai_job
from app.services.scheduling_candidates import build_scheduling_candidates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai/scheduling", tags=["ai-scheduling"])


def _raise_scheduling(exc: SchedulingError) -> None:
    status, detail = map_scheduling_error_to_http(exc)
    raise HTTPException(status_code=status, detail=detail) from exc


def _require_enabled() -> None:
    if not scheduling_enabled():
        raise SchedulingError(
            "scheduling_disabled",
            "AI job scheduling is disabled.",
        )


def _log_route(method: str, path: str, status: int, started: float) -> None:
    logger.info(
        "ai scheduling route",
        extra={
            "method": method,
            "path": path,
            "status": status,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        },
    )


@router.get("/policy", response_model=SchedulingPolicyV1)
def get_scheduling_policy() -> SchedulingPolicyV1:
    started = time.perf_counter()
    status = 200
    try:
        try:
            _require_enabled()
        except SchedulingError as exc:
            status = exc.http_status
            _raise_scheduling(exc)
        policy = store.get_policy()
        logger.debug(
            "scheduling policy get",
            extra={
                "mode": policy.mode,
                "allow_public_cloud": policy.allow_public_cloud,
                "document_revision": policy.document_revision,
            },
        )
        return policy
    except HTTPException as exc:
        status = exc.status_code
        raise
    finally:
        _log_route("GET", "/ai/scheduling/policy", status, started)


@router.put("/policy", response_model=SchedulingPolicyV1)
def put_scheduling_policy(body: SchedulingPolicyPut) -> SchedulingPolicyV1:
    started = time.perf_counter()
    status = 200
    try:
        try:
            _require_enabled()
            next_policy = SchedulingPolicyV1(
                mode=body.mode,
                allow_public_cloud=body.allow_public_cloud,
                fixed_node_id=body.fixed_node_id,
                fixed_model_id=body.fixed_model_id,
                max_attempts=body.max_attempts,
                document_revision=body.document_revision,
            )
            # Client sends the next revision; expected is previous.
            expected = body.document_revision - 1
            if expected < 1:
                raise SchedulingError(
                    "scheduling_invalid_policy",
                    "document_revision must be at least 2 for the first store write.",
                )
            return store.put_policy(next_policy, expected_revision=expected)
        except SchedulingError as exc:
            status = exc.http_status
            _raise_scheduling(exc)
    except HTTPException as exc:
        status = exc.status_code
        raise
    finally:
        _log_route("PUT", "/ai/scheduling/policy", status, started)


@router.post("/preview", response_model=SchedulingDecisionV1)
def preview_scheduling_decision(body: SchedulingPreviewRequest) -> SchedulingDecisionV1:
    started = time.perf_counter()
    status = 200
    try:
        try:
            _require_enabled()
            policy = body.policy or store.get_policy()
            candidates = body.candidates
            if candidates is None:
                candidates = build_scheduling_candidates()
            decision = schedule_ai_job(body.job, candidates, policy)
            logger.info(
                "scheduling preview",
                extra={
                    "operation": body.job.operation,
                    "required_capability": body.job.required_capability,
                    "policy_mode": decision.policy_mode,
                    "selected_model_id": decision.selected_model_id,
                    "selected_node_id": decision.selected_node_id,
                    "trust_boundary": decision.trust_boundary,
                    "eligible_count": decision.eligible_count,
                    "reason_codes": decision.reason_codes,
                    "privacy_class": body.job.privacy_class,
                },
            )
            return decision
        except SchedulingError as exc:
            status = exc.http_status
            _raise_scheduling(exc)
    except HTTPException as exc:
        status = exc.status_code
        raise
    finally:
        _log_route("POST", "/ai/scheduling/preview", status, started)
