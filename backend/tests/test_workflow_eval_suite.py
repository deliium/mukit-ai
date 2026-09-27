"""Suite identity, digest, and benchmark-root rejection."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.composition_schemas import CompositionV2
from app.composer_profile_schemas import ComposerProfileV1
from app.workflow_eval.schemas import load_benchmark_suite
from app.workflow_eval_settings import WorkflowEvalConfigError, load_workflow_eval_settings

_SUITE = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "fixtures"
    / "workflow_benchmark"
    / "suite.v1.json"
)


def test_suite_digest_is_stable() -> None:
    first, digest_a = load_benchmark_suite(_SUITE)
    second, digest_b = load_benchmark_suite(_SUITE)
    assert first.benchmark_version == "1.0.0"
    assert first.benchmark_id == "musical-workflows"
    assert len(first.cases) == 8
    melody = next(case for case in first.cases if case.id == "melody-line")
    assert melody.prompt.instruments == ["piano", "bass"]
    assert melody.allow_extra_instrument_families is False
    assert all(case.allow_extra_instrument_families is False for case in first.cases)
    assert digest_a == digest_b
    assert len(digest_a) == 64
    assert second.model_dump(mode="json") == first.model_dump(mode="json")
    root = _SUITE.parent
    ComposerProfileV1.model_validate_json((root / "profile_lyrical.json").read_text(encoding="utf-8"))
    CompositionV2.model_validate_json((root / "reference_piano_phrase.json").read_text(encoding="utf-8"))


def test_benchmark_root_rejects_dataset_and_project_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dataset = tmp_path / "datasets"
    dataset.mkdir()
    db_path = tmp_path / "projects.db"
    db_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("DATASET_ROOT", str(dataset))
    monkeypatch.setenv("EVAL_BENCHMARK_ROOT", str(dataset))
    with pytest.raises(WorkflowEvalConfigError) as dataset_error:
        load_workflow_eval_settings()
    assert dataset_error.value.code == "benchmark_root_rejected"

    monkeypatch.setenv("EVAL_BENCHMARK_ROOT", str(db_path))
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    with pytest.raises(WorkflowEvalConfigError) as db_error:
        load_workflow_eval_settings()
    assert db_error.value.code == "benchmark_root_rejected"
