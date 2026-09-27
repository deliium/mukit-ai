"""Fake-mode comparison of one brief and autonomous counters."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from app.ai_agents import registry as agent_registry
from app.ai_runtime import registry as model_registry
from app.db import reset_database_initialization_cache
from app.workflow_eval.cli import main
from app.workflow_eval.schemas import load_benchmark_suite
from app.workflow_eval.store import find_run_dir, read_case_result

_SUITE = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "fixtures"
    / "workflow_benchmark"
    / "suite.v1.json"
)
_INSTRUCTION = "Keep one singing piano line in a short rising question that settles."


@pytest.fixture
def fake_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    monkeypatch.setenv("EVAL_BENCHMARK_ROOT", str(tmp_path / "benchmarks"))
    monkeypatch.setenv("PROJECT_DB_PATH", str(tmp_path / "projects.db"))
    monkeypatch.delenv("DATASET_ROOT", raising=False)
    monkeypatch.delenv("FAKE_CRITIC_REVISE_PASSES", raising=False)
    monkeypatch.delenv("MUSIC_TRANSFORMER_CHECKPOINT", raising=False)
    reset_database_initialization_cache()
    agent_registry.clear_registry_for_tests()
    model_registry.clear_registry_for_tests()
    yield
    agent_registry.clear_registry_for_tests()
    model_registry.clear_registry_for_tests()


def test_fake_melody_line_three_arms(fake_env: None, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture) -> None:
    suite, digest = load_benchmark_suite(_SUITE)
    caplog.set_level(logging.INFO, logger="app.workflow_eval")
    code = main(
        [
            "run",
            "--suite",
            str(_SUITE),
            "--cases",
            "melody-line",
            "--arms",
            "v3_direct,v4_multi_agent,v4_iterative_revision",
        ]
    )
    captured = capsys.readouterr()
    assert code == 0, captured.err
    run_id = captured.out.strip().splitlines()[-1]
    run_dir = find_run_dir(run_id)
    run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert run["benchmark_version"] == "1.0.0"
    assert run["suite_sha256"] == digest
    assert run["musical_quality_claim"] is False
    assert run["benchmark_id"] == suite.benchmark_id
    for arm in ("v3_direct", "v4_multi_agent", "v4_iterative_revision"):
        result = read_case_result(run_dir, "melody-line", arm)
        assert result.seed_status in {"honored", "ignored_by_pipeline", "unknown"}
        assert result.metrics.reading("agent_calls") is not None or arm == "v3_direct"
        assert result.metrics.reading("invalid_composition") is not None
    v3 = read_case_result(run_dir, "melody-line", "v3_direct")
    assert v3.seed_status == "honored"
    spine = read_case_result(run_dir, "melody-line", "v4_multi_agent")
    assert spine.seed_status == "ignored_by_pipeline"
    assert spine.profile_conditioning == "not_wired"
    assert spine.reference_conditioning == "not_wired"
    text = " ".join(record.getMessage() for record in caplog.records)
    extra = " ".join(str(getattr(record, "case_id", "")) for record in caplog.records)
    assert "melody-line" in extra or "melody-line" in text
    assert _INSTRUCTION not in text
    joined = " ".join(str(record.__dict__) for record in caplog.records if record.name.startswith("app.workflow_eval"))
    assert _INSTRUCTION not in joined
    assert "melody-line" in joined


def test_autonomous_sparse_texture_counters(fake_env: None, capsys: pytest.CaptureFixture) -> None:
    code = main(
        [
            "run",
            "--suite",
            str(_SUITE),
            "--cases",
            "sparse-texture",
            "--arms",
            "v4_autonomous",
        ]
    )
    captured = capsys.readouterr()
    assert code == 0, captured.err
    run_id = captured.out.strip().splitlines()[-1]
    result = read_case_result(find_run_dir(run_id), "sparse-texture", "v4_autonomous")
    for name in (
        "agent_calls",
        "revision_passes",
        "failed_stages",
        "recovered_stages",
        "time_to_valid_ms",
    ):
        assert result.metrics.reading(name) is not None
