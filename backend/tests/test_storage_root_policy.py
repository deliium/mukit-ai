"""Storage roots must not collide with the dataset or the project database."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from app.audio_recovery_settings import load_audio_recovery_settings
from app.mix_analysis_settings import load_mix_analysis_settings
from app.mix_plan_settings import load_mix_plan_settings
from app.neural_audio_settings import load_neural_audio_settings
from app.storage_root_policy import StorageRootError, reject_storage_root
from app.workflow_eval_settings import WorkflowEvalConfigError, load_workflow_eval_settings


def _tree(tmp_path: Path) -> tuple[Path, Path, Path]:
    dataset = tmp_path / "datasets"
    dataset.mkdir()
    nested = dataset / "corpus"
    nested.mkdir()
    db_path = tmp_path / "data" / "projects.db"
    db_path.parent.mkdir()
    db_path.write_text("", encoding="utf-8")
    return dataset, nested, db_path


def test_reject_storage_root_reason_codes(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    dataset, nested, db_path = _tree(tmp_path)
    sibling = tmp_path / "data" / "audio_recovery_assets"
    caplog.set_level(logging.DEBUG)

    reject_storage_root(sibling, dataset_root=dataset, project_db=db_path)

    cases = (
        (dataset, "dataset_root"),
        (nested, "inside_dataset_root"),
        (db_path, "project_db_path"),
        (db_path.parent, "contains_project_db"),
    )
    for root, reason in cases:
        with pytest.raises(StorageRootError) as captured:
            reject_storage_root(root, dataset_root=dataset, project_db=db_path)
        assert captured.value.code == "storage_root_rejected"
        assert captured.value.reason == reason

    warnings = [record for record in caplog.records if record.message == "storage_root_rejected"]
    assert [record.reason for record in warnings] == [reason for _root, reason in cases]
    rendered = caplog.text
    assert str(tmp_path) not in rendered
    assert str(dataset) not in rendered


def test_default_sibling_roots_load(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    _dataset, _nested, db_path = _tree(tmp_path)
    env = {"PROJECT_DB_PATH": str(db_path)}
    caplog.set_level(logging.INFO)

    recovery = load_audio_recovery_settings(env)
    neural = load_neural_audio_settings(env)
    analysis = load_mix_analysis_settings(env)
    plan = load_mix_plan_settings(env)
    assert recovery.asset_root.name == "audio_recovery_assets"
    assert neural.render_root.name == "neural_audio_renders"
    assert analysis.report_root.name == "mix_analysis_reports"
    assert plan.plan_root.name == "mix_plans"
    assert recovery.asset_root.parent == db_path.parent

    accepted = [
        record
        for record in caplog.records
        if record.message == "storage_root_accepted"
    ]
    names = {record.settings for record in accepted}
    assert names == {"audio_recovery", "neural_audio", "mix_analysis", "mix_plan"}
    assert all(record.basename for record in accepted)
    assert str(tmp_path) not in caplog.text


@pytest.mark.parametrize(
    ("loader", "env_key"),
    [
        (load_audio_recovery_settings, "AUDIO_RECOVERY_ASSET_ROOT"),
        (load_neural_audio_settings, "NEURAL_AUDIO_RENDER_ROOT"),
        (load_mix_analysis_settings, "MIX_ANALYSIS_ROOT"),
        (load_mix_plan_settings, "MIX_PLAN_ROOT"),
    ],
)
def test_configured_roots_are_rejected(
    tmp_path: Path,
    loader,
    env_key: str,
) -> None:
    dataset, nested, db_path = _tree(tmp_path)
    base = {"PROJECT_DB_PATH": str(db_path), "DATASET_ROOT": str(dataset)}
    for root, reason in (
        (dataset, "dataset_root"),
        (nested, "inside_dataset_root"),
        (db_path, "project_db_path"),
        (db_path.parent, "contains_project_db"),
    ):
        with pytest.raises(StorageRootError) as captured:
            loader({**base, env_key: str(root)})
        assert captured.value.reason == reason


def test_workflow_eval_maps_policy_reason(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    dataset, nested, db_path = _tree(tmp_path)
    caplog.set_level(logging.WARNING)
    env = {
        "DATASET_ROOT": str(dataset),
        "PROJECT_DB_PATH": str(db_path),
        "EVAL_BENCHMARK_ROOT": str(nested),
    }
    with pytest.raises(WorkflowEvalConfigError) as captured:
        load_workflow_eval_settings(env)
    assert captured.value.code == "benchmark_root_rejected"
    mapped = [
        record
        for record in caplog.records
        if record.message == "benchmark_root_rejected"
    ]
    assert mapped
    assert mapped[0].reason == "inside_dataset_root"
    assert str(nested) not in caplog.text

    legal = tmp_path / "benchmarks"
    loaded = load_workflow_eval_settings({**env, "EVAL_BENCHMARK_ROOT": str(legal)})
    assert loaded.benchmark_root.name == "benchmarks"
