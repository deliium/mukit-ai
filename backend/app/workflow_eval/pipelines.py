"""V3 and V4 comparison arms.

V3 calls ``generate_music_json``. Spine arms pass a scaffold (or the
preservation source) into ``run_spine_workflow`` inside ``operation_span``.
Seeds and conditioning are recorded from what the callee returns.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.composition_schemas import CompositionV2
from app.embeddings.schemas import EmbedScopeComposition, StyleReferenceRequest
from app.operation_trace import build_summary, operation_span
from app.reference_conditioning_schemas import ReferenceConditioningPolicy
from app.schemas import LLMGenerationOptions, LLMMusicGenerationRequest
from app.services.composer_profile_merge import profile_active_for_fake
from app.services.llm_music_generator import generate_music_json
from app.workflow_eval.metrics import score_composition, with_observed_runtime
from app.workflow_eval.scaffold import (
    case_constraints,
    preservation_applies,
    preservation_source,
    prompt_parameters,
    scaffold_from_case,
)
from app.workflow_eval.schemas import (
    ArmId,
    BenchmarkCaseV1,
    CaseMetricsV1,
    ConditioningStatus,
    SeedStatus,
)

logger = logging.getLogger(__name__)

_SUITE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "workflow_benchmark"
_SPINE_COUNTERS = frozenset({"agent_calls", "revision_passes"})
_SPINE_MODES = {
    "v4_multi_agent": "off",
    "v4_iterative_revision": "balanced",
}


class ArmExecutionError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass
class ArmOutcome:
    case_id: str
    arm: ArmId
    composition: CompositionV2 | None
    metrics: CaseMetricsV1
    seed_status: SeedStatus
    profile_conditioning: ConditioningStatus
    reference_conditioning: ConditioningStatus
    generation_latency_ms: int
    invalid_composition: bool
    remote_cost_status: str


def _fake_mode() -> bool:
    raw = os.environ.get("LLM_FAKE_MODE", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _classify_seed(case_seed: int, returned: Any) -> SeedStatus:
    if returned is None:
        return "ignored_by_pipeline"
    try:
        value = int(returned)
    except (TypeError, ValueError):
        return "unknown"
    if value == int(case_seed):
        return "honored"
    return "unknown"


def _reference_payload(case: BenchmarkCaseV1) -> dict[str, Any] | None:
    if not case.reference_fixture:
        return None
    path = _SUITE_DIR / case.reference_fixture
    return json.loads(path.read_text(encoding="utf-8"))


def generation_request(case: BenchmarkCaseV1) -> LLMMusicGenerationRequest:
    """Build the V3 request. Does not write a profile into the project database."""
    references = None
    policy = None
    payload = _reference_payload(case)
    if payload is not None:
        references = [
            StyleReferenceRequest(
                composition=payload,
                scope=EmbedScopeComposition(),
                dimensions=list(case.reference_dimensions) or None,
            )
        ]
    if case.reference_policy is not None:
        policy = ReferenceConditioningPolicy.model_validate(case.reference_policy)
    return LLMMusicGenerationRequest(
        prompt=prompt_parameters(case),
        options=LLMGenerationOptions(pipeline=case.v3_pipeline, seed=case.seed),
        style_references=references,
        reference_conditioning_policy=policy,
        profile_id=case.profile_id,
        profile_strength=case.profile_strength,
    )


def _profile_status(
    case: BenchmarkCaseV1,
    request: LLMMusicGenerationRequest | None,
    provenance: dict[str, Any],
    *,
    wired: bool,
) -> ConditioningStatus:
    if not wired:
        return "not_wired"
    if not case.profile_id or case.profile_strength == "off":
        return "not_applicable"
    if request is not None and _fake_mode() and profile_active_for_fake(request):
        return "attached"
    if provenance.get("composer_profile_id"):
        return "attached"
    return "skipped_missing_profile"


def _reference_status(
    case: BenchmarkCaseV1,
    request: LLMMusicGenerationRequest | None,
    *,
    wired: bool,
) -> ConditioningStatus:
    if not wired:
        return "not_wired"
    if request is not None and request.style_references:
        return "attached"
    if case.reference_fixture:
        return "not_applicable"
    return "not_applicable"


def _warn_conditioning(case_id: str, profile: str, reference: str) -> None:
    for status in (profile, reference):
        if status in {"not_wired", "skipped_missing_profile"}:
            logger.warning(
                "Conditioning status",
                extra={"case_id": case_id, "code": status},
            )


def _invalid_outcome(
    case: BenchmarkCaseV1,
    arm: ArmId,
    *,
    latency_ms: int,
    seed_status: SeedStatus,
    profile: ConditioningStatus,
    reference: ConditioningStatus,
    error_type: str | None = None,
) -> ArmOutcome:
    if error_type:
        logger.error("Arm failed", extra={"error_type": error_type, "case_id": case.id, "arm": arm})
    metrics = score_composition(None, None)
    metrics = with_observed_runtime(
        metrics,
        generation_latency_ms=latency_ms,
        process_rss_kb=None,
        remote_cost_micros=None,
        model_calls=None,
        agent_calls=None,
        revision_passes=None,
        failed_stages=None,
        recovered_stages=None,
        time_to_valid_ms=None,
        applicable_counters=frozenset(),
    )
    _warn_conditioning(case.id, profile, reference)
    logger.info(
        "Arm finish",
        extra={
            "case_id": case.id,
            "arm": arm,
            "invalid_composition": True,
            "seed_status": seed_status,
            "profile_conditioning": profile,
            "reference_conditioning": reference,
            "generation_latency_ms": latency_ms,
            "remote_cost_status": "unavailable",
        },
    )
    return ArmOutcome(
        case_id=case.id,
        arm=arm,
        composition=None,
        metrics=metrics,
        seed_status=seed_status,
        profile_conditioning=profile,
        reference_conditioning=reference,
        generation_latency_ms=latency_ms,
        invalid_composition=True,
        remote_cost_status="unavailable",
    )


def _finish(
    case: BenchmarkCaseV1,
    arm: ArmId,
    composition: CompositionV2 | None,
    metrics: CaseMetricsV1,
    *,
    seed_status: SeedStatus,
    profile: ConditioningStatus,
    reference: ConditioningStatus,
    latency_ms: int,
    remote_cost_status: str,
) -> ArmOutcome:
    invalid = composition is None or bool(
        (metrics.reading("invalid_composition") or None)
        and metrics.reading("invalid_composition").value  # type: ignore[union-attr]
    )
    _warn_conditioning(case.id, profile, reference)
    logger.info(
        "Arm finish",
        extra={
            "case_id": case.id,
            "arm": arm,
            "invalid_composition": invalid,
            "seed_status": seed_status,
            "profile_conditioning": profile,
            "reference_conditioning": reference,
            "generation_latency_ms": latency_ms,
            "remote_cost_status": remote_cost_status,
        },
    )
    return ArmOutcome(
        case_id=case.id,
        arm=arm,
        composition=composition,
        metrics=metrics,
        seed_status=seed_status,
        profile_conditioning=profile,
        reference_conditioning=reference,
        generation_latency_ms=latency_ms,
        invalid_composition=invalid,
        remote_cost_status=remote_cost_status,
    )


def _targets_from_history(history: Any) -> tuple[list[Any], list[str]]:
    if not history:
        return [], []
    last = history[-1]
    plan = getattr(last, "revision_plan", None)
    if not isinstance(plan, dict):
        return [], []
    ranges = list(plan.get("affected_ranges") or [])
    tracks = [str(item) for item in (plan.get("affected_tracks") or [])]
    return ranges, tracks


async def _run_v3(case: BenchmarkCaseV1) -> ArmOutcome:
    started = time.perf_counter()
    request = generation_request(case)
    logger.info("Arm start", extra={"case_id": case.id, "arm": "v3_direct"})
    try:
        composition, _warnings, _provider, _report, provenance = await generate_music_json(request)
    except Exception as exc:  # noqa: BLE001
        return _invalid_outcome(
            case,
            "v3_direct",
            latency_ms=int((time.perf_counter() - started) * 1000),
            seed_status="unknown",
            profile=_profile_status(case, request, {}, wired=True),
            reference=_reference_status(case, request, wired=True),
            error_type=type(exc).__name__,
        )
    latency = int((time.perf_counter() - started) * 1000)
    provenance = provenance if isinstance(provenance, dict) else {}
    seed_status = _classify_seed(case.seed, provenance.get("seed"))
    profile = _profile_status(case, request, provenance, wired=True)
    reference = _reference_status(case, request, wired=True)
    try:
        metrics = score_composition(
            composition,
            case_constraints(case),
            preservation_applicable=False,
        )
    except Exception as exc:  # noqa: BLE001
        return _invalid_outcome(
            case,
            "v3_direct",
            latency_ms=latency,
            seed_status=seed_status,
            profile=profile,
            reference=reference,
            error_type=type(exc).__name__,
        )
    metrics = with_observed_runtime(
        metrics,
        generation_latency_ms=latency,
        process_rss_kb=None,
        remote_cost_micros=None,
        model_calls=None,
        agent_calls=None,
        revision_passes=None,
        failed_stages=None,
        recovered_stages=None,
        time_to_valid_ms=None,
        applicable_counters=frozenset(),
    )
    return _finish(
        case,
        "v3_direct",
        composition,
        metrics,
        seed_status=seed_status,
        profile=profile,
        reference=reference,
        latency_ms=latency,
        remote_cost_status="unavailable",
    )


async def _run_spine(case: BenchmarkCaseV1, arm: ArmId) -> ArmOutcome:
    from app.ai_agents.workflow import run_spine_workflow

    started = time.perf_counter()
    logger.info("Arm start", extra={"case_id": case.id, "arm": arm})
    mode = _SPINE_MODES[arm]
    try:
        if preservation_applies(case, arm):
            source = preservation_source(case)
            if source is None:
                raise ArmExecutionError("preservation_source_missing")
        else:
            source = scaffold_from_case(case)
    except (ValidationError, ArmExecutionError, OSError, ValueError) as exc:
        return _invalid_outcome(
            case,
            arm,
            latency_ms=int((time.perf_counter() - started) * 1000),
            seed_status="ignored_by_pipeline",
            profile="not_wired",
            reference="not_wired",
            error_type=type(exc).__name__,
        )
    try:
        async with operation_span("run") as span:
            result = await run_spine_workflow(source, revision_mode=mode)
        summary = build_summary(span)
    except Exception as exc:  # noqa: BLE001
        return _invalid_outcome(
            case,
            arm,
            latency_ms=int((time.perf_counter() - started) * 1000),
            seed_status="ignored_by_pipeline",
            profile="not_wired",
            reference="not_wired",
            error_type=type(exc).__name__,
        )
    latency = int((time.perf_counter() - started) * 1000)
    ranges, tracks = _targets_from_history(result.revision_history)
    source_for_score = source if preservation_applies(case, arm) else None
    try:
        metrics = score_composition(
            result.candidate,
            case_constraints(case),
            preservation_source=source_for_score,
            affected_ranges=ranges,
            affected_tracks=tracks,
            preservation_applicable=preservation_applies(case, arm),
        )
    except Exception as exc:  # noqa: BLE001
        return _invalid_outcome(
            case,
            arm,
            latency_ms=latency,
            seed_status="ignored_by_pipeline",
            profile="not_wired",
            reference="not_wired",
            error_type=type(exc).__name__,
        )
    cost = getattr(span, "provider_reported_cost_micros", None)
    rss = getattr(span, "process_rss_kb", None)
    cost_status = "unavailable" if cost is None else "available"
    metrics = with_observed_runtime(
        metrics,
        generation_latency_ms=latency,
        process_rss_kb=rss if isinstance(rss, int) else None,
        remote_cost_micros=cost if isinstance(cost, int) else None,
        model_calls=int(summary.model_call_count),
        agent_calls=len(result.agent_sequence),
        revision_passes=len(result.revision_history),
        failed_stages=None,
        recovered_stages=None,
        time_to_valid_ms=None,
        applicable_counters=_SPINE_COUNTERS,
    )
    return _finish(
        case,
        arm,
        result.candidate,
        metrics,
        seed_status="ignored_by_pipeline",
        profile="not_wired",
        reference="not_wired",
        latency_ms=latency,
        remote_cost_status=cost_status,
    )


async def run_arm(case: BenchmarkCaseV1, arm: ArmId) -> ArmOutcome:
    """Dispatch one comparison arm. Autonomous runs live in ``autonomous_arm``."""
    logger.debug("run_arm entry", extra={"case_id": case.id, "arm": arm})
    if arm == "v3_direct":
        return await _run_v3(case)
    if arm in _SPINE_MODES:
        return await _run_spine(case, arm)
    raise ArmExecutionError("arm_not_comparison")
