"""Versioned benchmark run files under ``EVAL_BENCHMARK_ROOT``."""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.composition_schemas import CompositionV2
from app.workflow_eval.pipelines import ArmOutcome
from app.workflow_eval.schemas import (
    ArmId,
    ArmRollupV1,
    BenchmarkRunV1,
    CaseResultV1,
    WorkflowBenchmarkV1,
    canonical_sha256,
)
from app.workflow_eval_settings import WorkflowEvalSettings, load_workflow_eval_settings

logger = logging.getLogger(__name__)


class WorkflowEvalStoreError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _settings(settings: WorkflowEvalSettings | None) -> WorkflowEvalSettings:
    return settings or load_workflow_eval_settings()


def _under_root(root: Path, relative: Path) -> Path:
    root_resolved = root.resolve()
    target = (root_resolved / relative).resolve()
    if os.path.commonpath([str(root_resolved), str(target)]) != str(root_resolved):
        logger.warning(
            "benchmark_path_escape",
            extra={"code": "benchmark_path_escape"},
        )
        raise WorkflowEvalStoreError("benchmark_path_escape")
    return target


def _rate(passed: int, applicable: int) -> float | None:
    if applicable <= 0:
        return None
    return passed / applicable


def rollup_for_arm(arm: ArmId, outcomes: list[ArmOutcome]) -> ArmRollupV1:
    rows = [item for item in outcomes if item.arm == arm]
    scored = len(rows)
    invalid = sum(1 for item in rows if item.invalid_composition)

    def _passes(name: str) -> tuple[int, int]:
        passed = 0
        applicable = 0
        for item in rows:
            reading = item.metrics.reading(name)
            if reading is None or reading.status == "not_applicable":
                continue
            if reading.status == "unavailable":
                continue
            applicable += 1
            if reading.status == "pass":
                passed += 1
        return passed, applicable

    hard_p, hard_n = _passes("hard_constraint_compliance")
    struct_p, struct_n = _passes("structural_compliance")
    range_p, range_n = _passes("instrument_range_correctness")
    pres_p, pres_n = _passes("revision_preservation")

    def _mean(name: str) -> float | None:
        values: list[float] = []
        for item in rows:
            reading = item.metrics.reading(name)
            if reading is None or reading.status != "measured":
                continue
            if isinstance(reading.value, bool) or not isinstance(reading.value, (int, float)):
                continue
            values.append(float(reading.value))
        if not values:
            return None
        return sum(values) / len(values)

    return ArmRollupV1(
        arm=arm,
        cases_scored=scored,
        invalid_cases=invalid,
        invalid_composition_rate=(invalid / scored) if scored else 0.0,
        hard_constraint_pass_rate=_rate(hard_p, hard_n),
        structural_pass_rate=_rate(struct_p, struct_n),
        instrument_range_pass_rate=_rate(range_p, range_n),
        revision_preservation_pass_rate=_rate(pres_p, pres_n),
        motif_recurrence_mean=_mean("motif_recurrence"),
        section_contrast_mean=_mean("section_contrast"),
        tonal_consistency_mean=_mean("tonal_consistency"),
        generation_latency_ms_mean=_mean("generation_latency_ms"),
        process_rss_kb_mean=_mean("process_rss_kb"),
        remote_cost_micros_mean=_mean("remote_cost_micros"),
    )


def composition_sha256(composition: CompositionV2 | None) -> str | None:
    if composition is None:
        return None
    return canonical_sha256(composition.model_dump(mode="json"))


def write_run(
    suite: WorkflowBenchmarkV1,
    suite_sha256: str,
    outcomes: list[ArmOutcome],
    *,
    arms: list[ArmId],
    case_ids: list[str],
    settings: WorkflowEvalSettings | None = None,
    run_id: str | None = None,
) -> BenchmarkRunV1:
    """Create the versioned directory and ``run.json``. Compositions stay beside case files."""
    active = _settings(settings)
    root = active.benchmark_root
    root.mkdir(parents=True, exist_ok=True)
    run_id = run_id or uuid.uuid4().hex
    relative_dir = Path(suite.benchmark_id) / suite.benchmark_version / run_id
    run_dir = _under_root(root, relative_dir)
    run_dir.mkdir(parents=True, exist_ok=False)
    case_paths: list[str] = []
    for outcome in outcomes:
        rel = Path("cases") / outcome.case_id / f"{outcome.arm}.json"
        target = _under_root(root, relative_dir / rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = composition_sha256(outcome.composition)
        result = CaseResultV1(
            case_id=outcome.case_id,
            arm=outcome.arm,
            metrics=outcome.metrics,
            seed_status=outcome.seed_status,
            profile_conditioning=outcome.profile_conditioning,
            reference_conditioning=outcome.reference_conditioning,
            generation_latency_ms=outcome.generation_latency_ms,
            composition_sha256=digest,
            invalid_composition=outcome.invalid_composition,
        )
        target.write_text(
            json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        case_paths.append(rel.as_posix())
        if outcome.composition is not None:
            sidecar = target.with_suffix(".composition.json")
            sidecar.write_text(
                json.dumps(outcome.composition.model_dump(mode="json"), sort_keys=True),
                encoding="utf-8",
            )
    rollups = [rollup_for_arm(arm, outcomes) for arm in arms]
    run = BenchmarkRunV1(
        run_id=run_id,
        benchmark_id=suite.benchmark_id,
        benchmark_version=suite.benchmark_version,
        suite_sha256=suite_sha256,
        created_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        arms=list(arms),
        case_ids=list(case_ids),
        rollups=rollups,
        case_paths=case_paths,
    )
    run_path = _under_root(root, relative_dir / "run.json")
    run_path.write_text(
        json.dumps(run.model_dump(mode="json"), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    logger.info(
        "Benchmark run stored",
        extra={
            "run_id_prefix": run_id[:12],
            "benchmark_version": suite.benchmark_version,
            "relative_path": relative_dir.as_posix(),
        },
    )
    return run


def find_run_dir(run_id: str, settings: WorkflowEvalSettings | None = None) -> Path:
    active = _settings(settings)
    root = active.benchmark_root.resolve()
    if not root.is_dir():
        raise WorkflowEvalStoreError("run_not_found")
    matches = [
        path.parent
        for path in root.glob("*/*/*/run.json")
        if path.is_file() and path.parent.name == run_id
    ]
    if len(matches) != 1:
        raise WorkflowEvalStoreError("run_not_found")
    return matches[0]


def read_run(run_id: str, settings: WorkflowEvalSettings | None = None) -> tuple[BenchmarkRunV1, Path]:
    """Load ``run.json`` and refuse a truncated case file."""
    run_dir = find_run_dir(run_id, settings)
    run_path = run_dir / "run.json"
    try:
        payload = json.loads(run_path.read_text(encoding="utf-8"))
        run = BenchmarkRunV1.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("Truncated run rejected", extra={"code": "run_truncated", "error_type": type(exc).__name__})
        raise WorkflowEvalStoreError("run_truncated") from exc
    root = _settings(settings).benchmark_root.resolve()
    for relative in run.case_paths:
        target = (run_dir / relative).resolve()
        if os.path.commonpath([str(root), str(target)]) != str(root):
            logger.warning("benchmark_path_escape", extra={"code": "benchmark_path_escape"})
            raise WorkflowEvalStoreError("benchmark_path_escape")
        if not target.is_file():
            logger.warning("Truncated run rejected", extra={"code": "run_truncated"})
            raise WorkflowEvalStoreError("run_truncated")
        try:
            CaseResultV1.model_validate(json.loads(target.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            logger.warning(
                "Truncated run rejected",
                extra={"code": "run_truncated", "error_type": type(exc).__name__},
            )
            raise WorkflowEvalStoreError("run_truncated") from exc
    return run, run_dir


def read_case_result(run_dir: Path, case_id: str, arm: str) -> CaseResultV1:
    path = run_dir / "cases" / case_id / f"{arm}.json"
    return CaseResultV1.model_validate(json.loads(path.read_text(encoding="utf-8")))


def read_case_composition(run_dir: Path, case_id: str, arm: str) -> dict | None:
    path = run_dir / "cases" / case_id / f"{arm}.composition.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def baseline_path(suite: WorkflowBenchmarkV1, settings: WorkflowEvalSettings | None = None) -> Path:
    active = _settings(settings)
    relative = Path("baselines") / suite.benchmark_id / f"{suite.benchmark_version}.json"
    return _under_root(active.benchmark_root, relative)


def set_baseline(
    run_id: str,
    *,
    force: bool = False,
    settings: WorkflowEvalSettings | None = None,
) -> Path:
    """Promote one run file. Replacing an existing baseline requires ``force``."""
    run, _run_dir = read_run(run_id, settings)
    active = _settings(settings)
    relative = Path("baselines") / run.benchmark_id / f"{run.benchmark_version}.json"
    target = _under_root(active.benchmark_root, relative)
    previous_id = None
    if target.is_file():
        if not force:
            raise WorkflowEvalStoreError("baseline_exists")
        try:
            previous = BenchmarkRunV1.model_validate(json.loads(target.read_text(encoding="utf-8")))
            previous_id = previous.run_id
        except (OSError, json.JSONDecodeError, ValueError):
            previous_id = "unreadable"
        logger.info(
            "Baseline replaced",
            extra={
                "previous_run_id_prefix": (previous_id or "")[:12],
                "run_id_prefix": run.run_id[:12],
            },
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(run.model_dump(mode="json"), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    logger.info(
        "Baseline set",
        extra={"run_id_prefix": run.run_id[:12], "benchmark_version": run.benchmark_version},
    )
    return target


def load_baseline_for_run(
    run: BenchmarkRunV1,
    settings: WorkflowEvalSettings | None = None,
) -> BenchmarkRunV1 | None:
    active = _settings(settings)
    relative = Path("baselines") / run.benchmark_id / f"{run.benchmark_version}.json"
    target = _under_root(active.benchmark_root, relative)
    if not target.is_file():
        return None
    return BenchmarkRunV1.model_validate(json.loads(target.read_text(encoding="utf-8")))
