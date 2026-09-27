"""Hard-metric regression against a baseline of the same suite version."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.workflow_eval.schemas import (
    BenchmarkRunV1,
    RegressionMetricV1,
    RegressionReportV1,
    WorkflowBenchmarkV1,
)
from app.workflow_eval.store import WorkflowEvalStoreError, load_baseline_for_run, read_run
from app.workflow_eval_settings import WorkflowEvalSettings

logger = logging.getLogger(__name__)

_HARD_GATES = (
    ("invalid_composition_rate", "invalid_composition_rate_max_rise", "rise"),
    ("hard_constraint_pass_rate", "hard_constraint_pass_max_drop", "drop"),
    ("structural_pass_rate", "structural_pass_max_drop", "drop"),
    ("instrument_range_pass_rate", "instrument_range_pass_max_drop", "drop"),
    ("revision_preservation_pass_rate", "revision_preservation_pass_max_drop", "drop"),
)
_OBSERVED = (
    "motif_recurrence_mean",
    "section_contrast_mean",
    "tonal_consistency_mean",
    "generation_latency_ms_mean",
    "process_rss_kb_mean",
    "remote_cost_micros_mean",
)


def _pair(run: BenchmarkRunV1, arm: str):
    return next((item for item in run.rollups if item.arm == arm), None)


def _compare_hard(
    name: str,
    baseline: float | None,
    current: float | None,
    *,
    tolerance: float,
    direction: str,
) -> RegressionMetricV1:
    if baseline is None or current is None:
        return RegressionMetricV1(
            name=name,
            status="not_applicable",
            gate=True,
            baseline=baseline,
            current=current,
        )
    if direction == "rise":
        failed = current - baseline > tolerance
    else:
        failed = baseline - current > tolerance
    return RegressionMetricV1(
        name=name,
        status="fail" if failed else "pass",
        gate=True,
        baseline=baseline,
        current=current,
    )


def regress(
    run_id: str,
    suite: WorkflowBenchmarkV1,
    *,
    settings: WorkflowEvalSettings | None = None,
) -> tuple[RegressionReportV1, int]:
    """Compare a run to the baseline. Exit 0 pass, 1 regression, 2 missing or digest mismatch."""
    run, run_dir = read_run(run_id, settings)
    baseline = load_baseline_for_run(run, settings)
    if baseline is None:
        logger.warning("baseline_missing", extra={"code": "baseline_missing", "run_id_prefix": run_id[:12]})
        report = RegressionReportV1(
            benchmark_id=run.benchmark_id,
            benchmark_version=run.benchmark_version,
            suite_sha256=run.suite_sha256,
            run_id=run.run_id,
            status="baseline_missing",
        )
        _write(run_dir, report)
        return report, 2
    if (
        baseline.benchmark_version != run.benchmark_version
        or baseline.suite_sha256 != run.suite_sha256
        or baseline.suite_sha256 != suite_digest_of(suite)
        or baseline.benchmark_version != suite.benchmark_version
    ):
        logger.warning(
            "suite_digest_mismatch",
            extra={"code": "suite_digest_mismatch", "run_id_prefix": run_id[:12]},
        )
        report = RegressionReportV1(
            benchmark_id=run.benchmark_id,
            benchmark_version=run.benchmark_version,
            suite_sha256=run.suite_sha256,
            run_id=run.run_id,
            baseline_run_id=baseline.run_id,
            status="suite_digest_mismatch",
        )
        _write(run_dir, report)
        return report, 2

    metrics: list[RegressionMetricV1] = []
    tolerances = suite.tolerances
    arms = sorted(set(run.arms) & set(baseline.arms))
    failing: list[str] = []
    for arm in arms:
        current_row = _pair(run, arm)
        baseline_row = _pair(baseline, arm)
        if current_row is None or baseline_row is None:
            continue
        for name, tolerance_field, direction in _HARD_GATES:
            item = _compare_hard(
                f"{arm}.{name}",
                getattr(baseline_row, name),
                getattr(current_row, name),
                tolerance=float(getattr(tolerances, tolerance_field)),
                direction=direction,
            )
            metrics.append(item)
            if item.status == "fail":
                failing.append(item.name)
        for name in _OBSERVED:
            metrics.append(
                RegressionMetricV1(
                    name=f"{arm}.{name}",
                    status="not_applicable",
                    gate=False,
                    baseline=getattr(baseline_row, name),
                    current=getattr(current_row, name),
                )
            )
    status = "fail" if failing else "pass"
    logger.info(
        "Regression finish",
        extra={"status": status, "failing_metrics": failing},
    )
    report = RegressionReportV1(
        benchmark_id=run.benchmark_id,
        benchmark_version=run.benchmark_version,
        suite_sha256=run.suite_sha256,
        run_id=run.run_id,
        baseline_run_id=baseline.run_id,
        status=status,
        metrics=metrics,
    )
    _write(run_dir, report)
    return report, 1 if status == "fail" else 0


def suite_digest_of(suite: WorkflowBenchmarkV1) -> str:
    from app.workflow_eval.schemas import suite_digest

    return suite_digest(suite)


def _write(run_dir: Path, report: RegressionReportV1) -> None:
    path = run_dir / "regression.json"
    path.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    logger.debug("Regression report written", extra={"run_id_prefix": report.run_id[:12]})


def regress_result_code(code: str) -> int:
    if code in {"baseline_missing", "suite_digest_mismatch"}:
        return 2
    if code == "fail":
        return 1
    if code == "pass":
        return 0
    raise WorkflowEvalStoreError("regression_status")
