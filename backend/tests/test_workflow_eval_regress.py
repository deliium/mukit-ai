"""Regression exit codes for a worsened invalid-composition rate."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.workflow_eval.pipelines import ArmOutcome
from app.workflow_eval.regress import regress
from app.workflow_eval.schemas import CaseMetricsV1, MetricReadingV1, load_benchmark_suite
from app.workflow_eval.store import set_baseline, write_run
from app.workflow_eval_settings import load_workflow_eval_settings

_SUITE = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "fixtures"
    / "workflow_benchmark"
    / "suite.v1.json"
)


def _metrics(*, invalid: bool) -> CaseMetricsV1:
    return CaseMetricsV1(
        metrics=[
            MetricReadingV1(
                name="invalid_composition",
                kind="hard",
                status="fail" if invalid else "pass",
                value=invalid,
            ),
            MetricReadingV1(
                name="hard_constraint_compliance",
                kind="hard",
                status="pass",
                value=True,
            ),
            MetricReadingV1(
                name="structural_compliance",
                kind="hard",
                status="pass",
                value=True,
            ),
            MetricReadingV1(
                name="instrument_range_correctness",
                kind="hard",
                status="pass",
                value=True,
            ),
            MetricReadingV1(
                name="revision_preservation",
                kind="hard",
                status="not_applicable",
                value=None,
            ),
            MetricReadingV1(
                name="motif_recurrence",
                kind="observed",
                status="measured",
                value=0,
            ),
            MetricReadingV1(
                name="generation_latency_ms",
                kind="observed",
                status="measured",
                value=12,
            ),
        ]
    )


def _outcome(invalid: bool) -> ArmOutcome:
    metrics = _metrics(invalid=invalid)
    return ArmOutcome(
        case_id="melody-line",
        arm="v3_direct",
        composition=None,
        metrics=metrics,
        seed_status="honored",
        profile_conditioning="not_applicable",
        reference_conditioning="not_applicable",
        generation_latency_ms=12,
        invalid_composition=invalid,
        remote_cost_status="unavailable",
    )


def test_worse_invalid_rate_exits_nonzero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVAL_BENCHMARK_ROOT", str(tmp_path / "benchmarks"))
    monkeypatch.delenv("DATASET_ROOT", raising=False)
    suite, digest = load_benchmark_suite(_SUITE)
    settings = load_workflow_eval_settings()
    baseline = write_run(
        suite,
        digest,
        [_outcome(False)],
        arms=["v3_direct"],
        case_ids=["melody-line"],
        settings=settings,
    )
    set_baseline(baseline.run_id, settings=settings)
    worse = write_run(
        suite,
        digest,
        [_outcome(True)],
        arms=["v3_direct"],
        case_ids=["melody-line"],
        settings=settings,
    )
    report, code = regress(worse.run_id, suite, settings=settings)
    assert code == 1
    assert report.status == "fail"
    failed = [item.name for item in report.metrics if item.status == "fail" and item.gate]
    assert any(name.endswith("invalid_composition_rate") for name in failed)
    observed = [item for item in report.metrics if item.name.endswith("generation_latency_ms_mean")]
    assert observed
    assert observed[0].gate is False


def test_missing_baseline_exits_two(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVAL_BENCHMARK_ROOT", str(tmp_path / "benchmarks"))
    monkeypatch.delenv("DATASET_ROOT", raising=False)
    suite, digest = load_benchmark_suite(_SUITE)
    settings = load_workflow_eval_settings()
    run = write_run(
        suite,
        digest,
        [_outcome(False)],
        arms=["v3_direct"],
        case_ids=["melody-line"],
        settings=settings,
    )
    report, code = regress(run.run_id, suite, settings=settings)
    assert code == 2
    assert report.status == "baseline_missing"
