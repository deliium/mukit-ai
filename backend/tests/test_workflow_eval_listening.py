"""Listening packets reject metric keys and stay blinded."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.workflow_eval.listening import ListeningError, pack_listening, unblind, write_judgment
from app.workflow_eval.pipelines import ArmOutcome
from app.workflow_eval.schemas import (
    CaseMetricsV1,
    ListeningPacketV1,
    MetricReadingV1,
    load_benchmark_suite,
)
from app.workflow_eval.store import write_run
from app.workflow_eval_settings import load_workflow_eval_settings
from app.composition_schemas import CompositionV2

_SUITE = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "fixtures"
    / "workflow_benchmark"
    / "suite.v1.json"
)
_EXPRESSIVE = (
    Path(__file__).resolve().parents[1] / "app" / "fixtures" / "composition_v2_expressive.json"
)


def test_packet_schema_rejects_metric_keys() -> None:
    body = {
        "schema_version": "workflow.listening_packet.v1",
        "benchmark_id": "musical-workflows",
        "benchmark_version": "1.0.0",
        "suite_sha256": "a" * 64,
        "case_id": "melody-line",
        "mode": "abc",
        "packet_sha256": "b" * 64,
        "hard_constraint_compliance": True,
        "clips": [
            {"label": "A", "composition": {"schema_version": "composition.v2"}},
            {"label": "B", "composition": {"schema_version": "composition.v2"}},
            {"label": "C", "composition": {"schema_version": "composition.v2"}},
        ],
    }
    with pytest.raises(ValidationError):
        ListeningPacketV1.model_validate(body)


def test_pack_omits_metrics_and_unblind_waits_for_judgment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EVAL_BENCHMARK_ROOT", str(tmp_path / "benchmarks"))
    monkeypatch.delenv("DATASET_ROOT", raising=False)
    suite, digest = load_benchmark_suite(_SUITE)
    settings = load_workflow_eval_settings()
    composition = CompositionV2.model_validate_json(_EXPRESSIVE.read_text(encoding="utf-8"))
    metrics = CaseMetricsV1(
        metrics=[
            MetricReadingV1(name="invalid_composition", kind="hard", status="pass", value=False),
        ]
    )
    outcomes = []
    for arm in ("v3_direct", "v4_multi_agent", "v4_iterative_revision"):
        outcomes.append(
            ArmOutcome(
                case_id="melody-line",
                arm=arm,  # type: ignore[arg-type]
                composition=composition,
                metrics=metrics,
                seed_status="ignored_by_pipeline",
                profile_conditioning="not_wired",
                reference_conditioning="not_wired",
                generation_latency_ms=1,
                invalid_composition=False,
                remote_cost_status="unavailable",
            )
        )
    run = write_run(
        suite,
        digest,
        outcomes,
        arms=["v3_direct", "v4_multi_agent", "v4_iterative_revision"],
        case_ids=["melody-line"],
        settings=settings,
    )
    packet_dir = pack_listening(run.run_id, "melody-line", "abc", settings=settings)
    packet = json.loads((packet_dir / "packet.json").read_text(encoding="utf-8"))
    assert {clip["label"] for clip in packet["clips"]} == {"A", "B", "C"}
    assert "hard_constraint_compliance" not in json.dumps(packet)
    assert "v3_direct" not in json.dumps(packet)
    assert (packet_dir / "label_map.json").is_file()
    with pytest.raises(ListeningError) as missing:
        unblind(packet_dir)
    assert missing.value.code == "judgment_missing"
    write_judgment(packet_dir, "A")
    revealed = unblind(packet_dir)
    assert revealed["judgment"]["winner"] == "A"
    assert "hard_constraint_compliance" not in json.dumps(revealed)
