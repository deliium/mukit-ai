"""Autonomous composer counters on a temp database under the benchmark root.

Uses ``prepare_run`` and ``retry_stage``. Does not call neural render and does
not write ``PROJECT_DB_PATH``.
"""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path

from app.autonomous_composer_schemas import CREATIVE_BRIEF_SCHEMA_VERSION, CreativeBriefV1, NarrativeBeat
from app.composition_schemas import CompositionV2
from app.services.autonomous_composer import (
    execute_autonomous_run,
    prepare_run,
    retry_stage,
)
from app.services.autonomous_composer_store import get_run
from app.services.composition_validator import validate_composition_integrity
from app.services.generation_constraints import validate_generation_constraints
from app.services.project_store import get_project
from app.workflow_eval.metrics import score_composition, with_observed_runtime
from app.workflow_eval.pipelines import ArmOutcome, _finish, _invalid_outcome
from app.workflow_eval.scaffold import case_constraints, prompt_parameters
from app.workflow_eval.schemas import BenchmarkCaseV1

logger = logging.getLogger(__name__)

_AUTO_COUNTERS = frozenset(
    {
        "agent_calls",
        "revision_passes",
        "failed_stages",
        "recovered_stages",
        "time_to_valid_ms",
    }
)


def _brief(case: BenchmarkCaseV1) -> CreativeBriefV1:
    prompt = prompt_parameters(case)
    return CreativeBriefV1(
        schema_version=CREATIVE_BRIEF_SCHEMA_VERSION,
        title=case.id,
        duration_seconds=60,
        narrative=[NarrativeBeat(intent="establish_theme", text="State the theme once.")],
        instrumentation=list(prompt.instruments),
        opening_key=prompt.key or "C major",
        time_signature=prompt.time_signature,
        tempo_min=prompt.tempo_min,
        tempo_max=prompt.tempo_max,
        mood=prompt.mood,
        genre=prompt.genre,
    )


def _head_composition(project_id: str, db_path: Path) -> CompositionV2 | None:
    record = get_project(project_id, db_path=db_path)
    raw = record.composition_json
    if not raw:
        return None
    try:
        return CompositionV2.model_validate_json(raw)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Autonomous head composition invalid",
            extra={"error_type": type(exc).__name__, "case_id": project_id[:16]},
        )
        return None


def _passes_hard(composition: CompositionV2, case: BenchmarkCaseV1) -> bool:
    integrity = validate_composition_integrity(composition, profile="canonical")
    if any(item.code == "schema_invalid" for item in integrity.errors):
        return False
    if any(item.code == "event_out_of_range" for item in integrity.errors):
        return False
    report = validate_generation_constraints(composition, case_constraints(case))
    return len(report.errors) == 0


def _remove_db(path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(path) + suffix) if suffix else path
        if candidate.is_file():
            candidate.unlink()
            logger.debug("Temp database removed", extra={"suffix": suffix or "db"})


async def run_autonomous_arm(case: BenchmarkCaseV1, *, benchmark_root: Path) -> ArmOutcome:
    """Start one composer run, retry one recoverable failure, then score the head."""
    started = time.perf_counter()
    logger.info("Arm start", extra={"case_id": case.id, "arm": "v4_autonomous"})
    tmp_dir = benchmark_root / "_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    db_path = tmp_dir / f"autonomous-{uuid.uuid4().hex}.sqlite"
    recovered = 0
    try:
        brief = _brief(case)
        record = prepare_run(
            brief,
            project_id=None,
            operation_run_id=uuid.uuid4().hex,
            include_rendering=False,
            seed=case.seed,
            expected_working_version=None,
            expected_head_revision_id=None,
            expected_source_fingerprint=None,
            db_path=db_path,
        )
        await execute_autonomous_run(record.id, db_path=db_path)
        latest = get_run(record.id, db_path=db_path)
        failed = next(
            (
                stage
                for stage in latest.stages
                if stage.status == "failed" and stage.recoverable
            ),
            None,
        )
        if failed is not None:
            retry_stage(record.id, failed.stage_id, db_path=db_path)
            await execute_autonomous_run(record.id, db_path=db_path)
            after = get_run(record.id, db_path=db_path)
            retried = next(stage for stage in after.stages if stage.stage_id == failed.stage_id)
            if retried.status == "completed":
                recovered = 1
            latest = after
        composition = _head_composition(latest.project_id, db_path)
        elapsed = int((time.perf_counter() - started) * 1000)
        valid = composition is not None and _passes_hard(composition, case)
        time_to_valid = elapsed if valid else None
        seed_status = (
            "honored"
            if latest.seed is not None and int(latest.seed) == int(case.seed)
            else ("ignored_by_pipeline" if latest.seed is None else "unknown")
        )
        failed_stages = sum(1 for stage in latest.stages if stage.status == "failed")
        try:
            metrics = score_composition(
                composition,
                case_constraints(case) if composition is not None else None,
                preservation_applicable=False,
            )
        except Exception as exc:  # noqa: BLE001
            return _invalid_outcome(
                case,
                "v4_autonomous",
                latency_ms=elapsed,
                seed_status=seed_status,
                profile="not_wired",
                reference="not_wired",
                error_type=type(exc).__name__,
            )
        if composition is None:
            metrics = score_composition(None, None)
        metrics = with_observed_runtime(
            metrics,
            generation_latency_ms=elapsed,
            process_rss_kb=None,
            remote_cost_micros=(
                latest.provider_reported_cost_micros
                if isinstance(latest.provider_reported_cost_micros, int)
                else None
            ),
            model_calls=None,
            agent_calls=int(latest.agent_operation_count),
            revision_passes=int(latest.revision_pass_count),
            failed_stages=failed_stages,
            recovered_stages=recovered,
            time_to_valid_ms=time_to_valid,
            applicable_counters=_AUTO_COUNTERS,
        )
        if not valid:
            metrics = metrics.model_copy(
                update={
                    "metrics": [
                        item.model_copy(update={"status": "fail", "value": True})
                        if item.name == "invalid_composition"
                        else item
                        for item in metrics.metrics
                    ]
                }
            )
        logger.info(
            "Autonomous counters",
            extra={
                "case_id": case.id,
                "agent_calls": latest.agent_operation_count,
                "revision_passes": latest.revision_pass_count,
                "failed_stages": failed_stages,
                "recovered_stages": recovered,
                "time_to_valid_ms": time_to_valid,
            },
        )
        cost_status = (
            "unavailable" if latest.provider_reported_cost_micros is None else "available"
        )
        return _finish(
            case,
            "v4_autonomous",
            composition if valid or composition is not None else None,
            metrics,
            seed_status=seed_status,  # type: ignore[arg-type]
            profile="not_wired",
            reference="not_wired",
            latency_ms=elapsed,
            remote_cost_status=cost_status,
        )
    except Exception as exc:  # noqa: BLE001
        elapsed = int((time.perf_counter() - started) * 1000)
        return _invalid_outcome(
            case,
            "v4_autonomous",
            latency_ms=elapsed,
            seed_status="unknown",
            profile="not_wired",
            reference="not_wired",
            error_type=type(exc).__name__,
        )
    finally:
        _remove_db(db_path)
        logger.debug("Autonomous temp database cleanup finished", extra={"case_id": case.id})
