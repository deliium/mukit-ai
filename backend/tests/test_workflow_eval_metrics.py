"""Hard-metric scoring on the expressive fixture and preservation cases."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from app.composition_schemas import CompositionV2
from app.workflow_eval.metrics import score_composition
from app.workflow_eval.scaffold import case_constraints
from app.workflow_eval.schemas import load_benchmark_suite

_FIXTURES = Path(__file__).resolve().parents[1] / "app" / "fixtures"
_EXPRESSIVE = _FIXTURES / "composition_v2_expressive.json"
_SUITE = _FIXTURES / "workflow_benchmark" / "suite.v1.json"
_INSTRUCTION = "Keep one singing piano line in a short rising question that settles."


def _case(case_id: str):
    suite, _digest = load_benchmark_suite(_SUITE)
    return next(case for case in suite.cases if case.id == case_id)


def test_expressive_fixture_scores_without_mutation(caplog: pytest.LogCaptureFixture) -> None:
    composition = CompositionV2.model_validate_json(_EXPRESSIVE.read_text(encoding="utf-8"))
    before = composition.model_dump(mode="json")
    constraints = case_constraints(_case("keep-the-rest"))
    caplog.set_level(logging.DEBUG, logger="app.workflow_eval.metrics")
    metrics = score_composition(composition, constraints, preservation_applicable=False)
    assert composition.model_dump(mode="json") == before
    assert metrics.musical_quality_claim is False
    assert metrics.reading("invalid_composition").status == "pass"  # type: ignore[union-attr]
    assert metrics.reading("instrument_range_correctness").status == "pass"  # type: ignore[union-attr]
    assert metrics.reading("revision_preservation").status == "not_applicable"  # type: ignore[union-attr]
    assert metrics.reading("section_contrast").status == "measured"  # type: ignore[union-attr]
    text = " ".join(record.getMessage() for record in caplog.records)
    extra = " ".join(str(record.__dict__) for record in caplog.records)
    assert _INSTRUCTION not in text
    assert _INSTRUCTION not in extra
    assert "musical_quality_claim" in extra or any(
        record.__dict__.get("musical_quality_claim") is False for record in caplog.records
    )


def test_preservation_not_applicable_versus_fingerprint() -> None:
    source = CompositionV2.model_validate_json(_EXPRESSIVE.read_text(encoding="utf-8"))
    constraints = case_constraints(_case("keep-the-rest"))
    unchanged = score_composition(
        source,
        constraints,
        preservation_source=source,
        affected_ranges=[{"start_bar": 1, "end_bar": 1}],
        affected_tracks=["melody-1"],
        preservation_applicable=True,
    )
    assert unchanged.reading("revision_preservation").status == "pass"  # type: ignore[union-attr]

    missing = score_composition(
        source,
        constraints,
        preservation_source=source,
        affected_ranges=[],
        affected_tracks=[],
        preservation_applicable=True,
    )
    assert missing.reading("revision_preservation").status == "not_applicable"  # type: ignore[union-attr]

    dumped = source.model_dump(mode="json")
    dumped["tracks"][0]["events"][-1]["pitch"] = "D5"
    changed = CompositionV2.model_validate(dumped)
    failed = score_composition(
        changed,
        constraints,
        preservation_source=source,
        affected_ranges=[{"start_bar": 1, "end_bar": 1}],
        affected_tracks=["melody-1"],
        preservation_applicable=True,
    )
    assert failed.reading("revision_preservation").status == "fail"  # type: ignore[union-attr]
