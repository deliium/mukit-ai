"""Fan-out orchestration for ensemble arbitration preview.

Accepts a client-supplied ``composition.plan.v1`` + hard constraints. Does not
call the hybrid LLM planner. Preference ranking is in-process only (never
``score_pending`` / ``POST /preferences/rank``).
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Mapping

from app.ai_runtime.capabilities import ModelCapability
from app.ai_runtime.errors import ModelNotFoundError, ModelUnavailableError
from app.ai_runtime.registry import get_model
from app.composition_plan_schemas import CompositionPlan
from app.ensemble_arbitration_schemas import (
    EnsembleArbitrationError,
    EnsembleArbitrationRequestV1,
    EnsembleArbitrationStatusV1,
    EnsembleArbitrationV1,
    EnsemblePolicyV1,
    EnsembleStrategiesCatalogV1,
    parse_ensemble_arbitration_request,
    reject_ensemble_payload,
)
from app.ensemble_arbitration_settings import (
    EnsembleArbitrationSettings,
    load_ensemble_arbitration_settings,
    min_models,
)
from app.services.ensemble_arbitration_pipeline import (
    EnsembleGenerateAttempt,
    build_arbitration_report,
)
from app.services.generation_constraints import GenerationConstraints
from app.services.preference_store import effective_ranking, get_ranker
from app.services.symbolic_composition_generate import (
    SymbolicCompositionGenerateError,
    generate_symbolic_composition,
)

logger = logging.getLogger(__name__)


def status_document(
    settings: EnsembleArbitrationSettings | None = None,
) -> EnsembleArbitrationStatusV1:
    cfg = settings or load_ensemble_arbitration_settings()
    return EnsembleArbitrationStatusV1(
        enabled=cfg.enabled,
        max_models=cfg.max_models,
        max_wall_ms=cfg.max_wall_ms,
        allow_parallel=cfg.allow_parallel,
        fake=cfg.fake,
        musical_quality_claim=False,
    )


def strategies_catalog() -> EnsembleStrategiesCatalogV1:
    return EnsembleStrategiesCatalogV1()


def _resolve_model_ready(model_id: str) -> tuple[bool, str | None, str | None]:
    """Return (ready, runtime, model_version). Unready → (False, None, None)."""
    try:
        descriptor = get_model(model_id)
    except ModelNotFoundError:
        return False, None, None
    except ModelUnavailableError:
        return False, None, None
    if descriptor.primary_capability != ModelCapability.SYMBOLIC_COMPOSER:
        return False, None, None
    if descriptor.status != "ready":
        return False, descriptor.runtime, descriptor.model_version
    return True, descriptor.runtime, descriptor.model_version


def _run_one_attempt(
    *,
    plan: CompositionPlan,
    model_id: str,
    seed: int,
    attempt_ordinal: int,
    strategy: str,
) -> EnsembleGenerateAttempt:
    ready, runtime, model_version = _resolve_model_ready(model_id)
    if not ready:
        logger.debug(
            "Ensemble model unready",
            extra={"model_id": model_id, "attempt_ordinal": attempt_ordinal},
        )
        return EnsembleGenerateAttempt(
            model_id=model_id,
            seed=seed,
            attempt_ordinal=attempt_ordinal,
            strategy=strategy,  # type: ignore[arg-type]
            composition=None,
            runtime=runtime,
            model_version=model_version,
            generate_error_code="ensemble_model_unready",
            generate_error_message=f"Model {model_id} is not a ready symbolic_composer.",
        )
    try:
        result = generate_symbolic_composition(
            plan,
            seed=seed,
            prefer_fake=None,
            model_id=model_id,
        )
        return EnsembleGenerateAttempt(
            model_id=model_id,
            seed=seed if result.seed is None else int(result.seed),
            attempt_ordinal=attempt_ordinal,
            strategy=strategy,  # type: ignore[arg-type]
            composition=result.composition,
            engine="symbolic_composer",
            runtime=runtime or result.report.get("runtime"),
            model_version=model_version or result.report.get("model_version"),
        )
    except SymbolicCompositionGenerateError as exc:
        code = getattr(exc, "code", None) or "symbolic_generate_failed"
        return EnsembleGenerateAttempt(
            model_id=model_id,
            seed=seed,
            attempt_ordinal=attempt_ordinal,
            strategy=strategy,  # type: ignore[arg-type]
            composition=None,
            runtime=runtime,
            model_version=model_version,
            generate_error_code=str(code)[:80],
            generate_error_message=str(exc)[:200],
        )
    except Exception as exc:  # noqa: BLE001 — map to generate reject
        logger.warning(
            "Ensemble generate attempt failed",
            extra={
                "model_id": model_id,
                "attempt_ordinal": attempt_ordinal,
                "error_type": type(exc).__name__,
            },
        )
        return EnsembleGenerateAttempt(
            model_id=model_id,
            seed=seed,
            attempt_ordinal=attempt_ordinal,
            strategy=strategy,  # type: ignore[arg-type]
            composition=None,
            runtime=runtime,
            model_version=model_version,
            generate_error_code="symbolic_generate_failed",
            generate_error_message=str(exc)[:200],
        )


def _assert_enabled(settings: EnsembleArbitrationSettings) -> None:
    if not settings.enabled:
        raise EnsembleArbitrationError(
            "ensemble_arbitration_disabled",
            "Ensemble arbitration is disabled.",
            http_status=503,
        )


def _assert_policy_arity(policy: EnsemblePolicyV1, settings: EnsembleArbitrationSettings) -> None:
    count = len(policy.model_ids)
    if count < min_models() or count > settings.max_models:
        raise EnsembleArbitrationError(
            "ensemble_model_limit",
            f"Ensemble requires between {min_models()} and {settings.max_models} models.",
            details={"model_count": count, "max_models": settings.max_models},
        )
    if policy.strategy != "parallel_once":
        raise EnsembleArbitrationError(
            "ensemble_strategy_unknown",
            f"Unknown ensemble strategy: {policy.strategy}.",
        )
    if policy.execution == "parallel" and not settings.allow_parallel:
        raise EnsembleArbitrationError(
            "ensemble_selection_invalid",
            "Parallel ensemble execution is not allowed on this deployment.",
            details={"field_name": "execution"},
        )


def _load_ranking_gates(
    *,
    db_path: Path | str | None,
) -> tuple[bool, Any]:
    ranking_on = effective_ranking(db_path=db_path)
    ranker = get_ranker(db_path=db_path) if ranking_on else None
    logger.debug(
        "Ensemble ranking gates loaded",
        extra={"ranking_applied_gate": ranking_on, "has_ranker": ranker is not None},
    )
    return ranking_on, ranker


def run_ensemble_arbitration_preview(
    request: EnsembleArbitrationRequestV1 | Mapping[str, Any],
    *,
    settings: EnsembleArbitrationSettings | None = None,
    env: Mapping[str, str] | None = None,
    db_path: Path | str | None = None,
    planner_hook: Any | None = None,
) -> EnsembleArbitrationV1:
    """Fan out symbolic generate, validate, critique, rank, and select.

    ``planner_hook`` exists only so tests can assert the hybrid LLM planner is
    never invoked (default None; calling it is a bug).
    """
    if planner_hook is not None:
        raise EnsembleArbitrationError(
            "ensemble_payload_refused",
            "Ensemble preview must not invoke a planner hook.",
        )

    cfg = settings or load_ensemble_arbitration_settings(env)
    _assert_enabled(cfg)

    if isinstance(request, Mapping):
        reject_ensemble_payload(request)
        body = parse_ensemble_arbitration_request(dict(request))
    else:
        body = request

    policy = body.policy
    _assert_policy_arity(policy, cfg)

    plan = body.plan
    constraints: GenerationConstraints = body.constraints.to_generation_constraints()
    wall_budget = int(policy.max_wall_ms or cfg.max_wall_ms)
    wall_budget = min(wall_budget, cfg.max_wall_ms)

    logger.info(
        "Ensemble arbitration preview started",
        extra={
            "model_count": len(policy.model_ids),
            "strategy": policy.strategy,
            "selection_mode": policy.selection_mode,
            "execution": policy.execution,
            "bar_count": plan.form.bar_count,
        },
    )

    started = time.perf_counter()
    attempts: list[EnsembleGenerateAttempt] = []
    budget_exceeded = False

    work = [
        (ordinal, model_id, int(policy.base_seed) + int(ordinal))
        for ordinal, model_id in enumerate(policy.model_ids)
    ]

    if policy.execution == "parallel" and cfg.allow_parallel:
        with ThreadPoolExecutor(max_workers=len(work)) as pool:
            futures = {
                pool.submit(
                    _run_one_attempt,
                    plan=plan,
                    model_id=model_id,
                    seed=seed,
                    attempt_ordinal=ordinal,
                    strategy=policy.strategy,
                ): ordinal
                for ordinal, model_id, seed in work
            }
            by_ordinal: dict[int, EnsembleGenerateAttempt] = {}
            for future in as_completed(futures):
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                if elapsed_ms > wall_budget:
                    budget_exceeded = True
                attempt = future.result()
                by_ordinal[attempt.attempt_ordinal] = attempt
            attempts = [by_ordinal[i] for i in sorted(by_ordinal)]
    else:
        for ordinal, model_id, seed in work:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            if elapsed_ms > wall_budget:
                budget_exceeded = True
                logger.warning(
                    "Ensemble wall budget exceeded; stopping further attempts",
                    extra={
                        "wall_ms": elapsed_ms,
                        "budget_ms": wall_budget,
                        "completed_attempts": len(attempts),
                    },
                )
                # Record remaining as generate rejects for transparency.
                for rest_ordinal, rest_id, rest_seed in work[ordinal:]:
                    attempts.append(
                        EnsembleGenerateAttempt(
                            model_id=rest_id,
                            seed=rest_seed,
                            attempt_ordinal=rest_ordinal,
                            strategy=policy.strategy,  # type: ignore[arg-type]
                            composition=None,
                            generate_error_code="ensemble_budget_exceeded",
                            generate_error_message="Wall budget exceeded before attempt.",
                        )
                    )
                break
            attempts.append(
                _run_one_attempt(
                    plan=plan,
                    model_id=model_id,
                    seed=seed,
                    attempt_ordinal=ordinal,
                    strategy=policy.strategy,
                )
            )

    ranking_enabled, ranker_model = _load_ranking_gates(db_path=db_path)
    wall_ms = int((time.perf_counter() - started) * 1000)
    report = build_arbitration_report(
        policy=policy,
        attempts=attempts,
        constraints=constraints,
        ranking_enabled=ranking_enabled,
        ranker_model=ranker_model,
        wall_ms=wall_ms,
    )

    if not report.candidates:
        raise EnsembleArbitrationError(
            "ensemble_no_survivors",
            "No ensemble candidates survived hard validation.",
            details={
                "rejected_count": len(report.rejected_attempts),
                "rejected_attempts": [
                    item.model_dump(mode="json") for item in report.rejected_attempts
                ],
            },
        )

    if budget_exceeded and not report.candidates:
        raise EnsembleArbitrationError(
            "ensemble_budget_exceeded",
            "Ensemble wall budget exceeded before any survivor.",
            details={"wall_ms": wall_ms, "budget_ms": wall_budget},
        )

    logger.info(
        "Ensemble arbitration preview completed",
        extra={
            "model_count": len(policy.model_ids),
            "survivor_count": len(report.candidates),
            "rejected_count": len(report.rejected_attempts),
            "wall_ms": wall_ms,
            "ranking_applied": report.ranking_applied,
            "selection_mode": policy.selection_mode,
        },
    )
    return report


def preview_from_raw(
    payload: Mapping[str, Any],
    *,
    settings: EnsembleArbitrationSettings | None = None,
    env: Mapping[str, str] | None = None,
    db_path: Path | str | None = None,
) -> EnsembleArbitrationV1:
    """Convenience entry used by the HTTP router."""
    return run_ensemble_arbitration_preview(
        payload,
        settings=settings,
        env=env,
        db_path=db_path,
    )


__all__ = [
    "preview_from_raw",
    "run_ensemble_arbitration_preview",
    "status_document",
    "strategies_catalog",
]
