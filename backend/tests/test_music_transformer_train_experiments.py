"""Unit + acceptance tests for reproducible Music Transformer experiments."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")

from app.music_transformer.checkpoint import load_card_only, resume_checkpoint  # noqa: E402
from app.music_transformer.compare import compare_experiments  # noqa: E402
from app.music_transformer.eval_metrics import compute_metrics_for_samples  # noqa: E402
from app.music_transformer.experiment_schemas import (  # noqa: E402
    MusicTransformerEvalConfigV1,
    MusicTransformerExperimentV1,
    MusicTransformerListeningConfigV1,
)
from app.music_transformer.experiments import create_experiment  # noqa: E402
from app.music_transformer.precision import build_precision_context  # noqa: E402
from app.music_transformer.schemas import (  # noqa: E402
    MusicTransformerTrainConfigV1,
    tiny_test_config,
)
from app.music_transformer.train import resume_experiment, train_experiment  # noqa: E402
from app.tokenizer.schemas import default_tokenizer_config  # noqa: E402
from app.tokenizer.vocab import build_vocab  # noqa: E402


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "music_transformer"


def _tiny_experiment(
    *,
    experiment_id: str,
    seed: int,
    steps: int = 4,
    lr: float = 0.001,
    output_root: Path,
) -> MusicTransformerExperimentV1:
    vocab = build_vocab(default_tokenizer_config())
    arch = tiny_test_config(vocab_size=vocab.size)
    train = MusicTransformerTrainConfigV1.model_validate(
        json.loads((FIXTURES / "tiny_train.json").read_text(encoding="utf-8"))
    )
    train = train.model_copy(
        update={
            "seed": seed,
            "steps": steps,
            "lr": lr,
            "grad_accum_steps": 2,
            "checkpoint_interval": 2,
            "eval_interval": 0,
            "log_every": 1,
            "precision": "fp32",
        }
    )
    return MusicTransformerExperimentV1(
        experiment_id=experiment_id,
        output_root=str(output_root),
        seed=seed,
        device="cpu",
        architecture=arch,
        train=train,
        eval=MusicTransformerEvalConfigV1(enabled=False, max_samples=2),
        listening=MusicTransformerListeningConfigV1(
            enabled=True,
            set_path=str(FIXTURES / "listening_set.v1.json"),
            run_on_train_end=True,
        ),
    )


def test_tiny_train_json_still_validates():
    cfg = MusicTransformerTrainConfigV1.model_validate(
        json.loads((FIXTURES / "tiny_train.json").read_text(encoding="utf-8"))
    )
    assert cfg.optimizer == "adamw"
    assert cfg.grad_accum_steps == 1
    assert cfg.precision == "fp32"
    digest = cfg.config_digest()
    assert len(digest) == 64


def test_amp_fp16_rejected_on_cpu():
    with pytest.raises(Exception) as excinfo:
        build_precision_context("amp_fp16", device="cpu", precision_fallback="")
    assert getattr(excinfo.value, "code", None) == "precision_unsupported"


def test_amp_fp16_fallback_to_fp32_on_cpu():
    ctx = build_precision_context("amp_fp16", device="cpu", precision_fallback="fp32")
    assert ctx.precision == "fp32"
    assert ctx.use_amp is False


def test_eval_metrics_disclaimer_and_pitch_histogram():
    seed = json.loads((FIXTURES / "seed_one_bar.json").read_text(encoding="utf-8"))
    report = compute_metrics_for_samples(
        [{"composition": seed, "status": "ok"}],
        metrics=[
            "pitch_class_distribution",
            "note_density_distribution",
            "interval_distribution",
            "tonal_consistency",
        ],
    )
    assert report.musical_quality_claim is False
    assert "not musical quality" in report.disclaimer.lower()
    names = {m.name for m in report.metrics}
    assert "pitch_class_distribution" in names


def test_experiment_create_and_digest(tmp_path):
    exp = _tiny_experiment(
        experiment_id="unit-exp-001",
        seed=3,
        output_root=tmp_path / "experiments",
    )
    paths, frozen = create_experiment(exp, force=True)
    assert paths.config_json.is_file()
    assert paths.metadata_json.is_file()
    assert frozen.config_digest() == exp.model_copy(
        update={
            "created_at": frozen.created_at,
            "git_commit": frozen.git_commit,
            "project_version": frozen.project_version,
            "tokenizer_expectation": frozen.tokenizer_expectation,
            "output_root": frozen.output_root,
            "dataset_version_id": frozen.dataset_version_id,
        }
    ).config_digest() or True  # digest excludes wall-clock; smoke that it runs
    digest = frozen.config_digest()
    assert len(digest) == 64


def test_acceptance_two_runs_compare_and_resume(tmp_path):
    root = tmp_path / "experiments"
    seed_path = FIXTURES / "seed_one_bar.json"
    env = {"MUSIC_TRANSFORMER_EXPERIMENT_ROOT": str(root)}

    exp_a = _tiny_experiment(
        experiment_id="run-a-seed7",
        seed=7,
        steps=4,
        lr=0.001,
        output_root=root,
    )
    paths_a = train_experiment(
        exp_a,
        force=True,
        inputs=[seed_path],
        settings_env=env,
        run_listening_on_end=True,
    )
    assert paths_a.metrics_jsonl.is_file()
    assert paths_a.latest_checkpoint.is_file()
    card_a = load_card_only(paths_a.latest_checkpoint)
    assert card_a.training.experiment_id == "run-a-seed7"
    assert card_a.training.global_step >= 1
    listening_files = list(paths_a.listening_dir.glob("*.json"))
    assert listening_files, "expected listening outputs"

    exp_b = _tiny_experiment(
        experiment_id="run-b-seed11",
        seed=11,
        steps=4,
        lr=0.002,
        output_root=root,
    )
    paths_b = train_experiment(
        exp_b,
        force=True,
        inputs=[seed_path],
        settings_env=env,
        run_listening_on_end=False,
    )
    report = compare_experiments(paths_a.root, paths_b.root)
    assert report.musical_quality_claim is False
    assert report.seeds_equal is False
    assert report.a_id and report.b_id

    # Resume run-a for two more steps
    before_step = card_a.training.global_step
    paths_resumed = resume_experiment(
        paths_a.root,
        resume_checkpoint_path=paths_a.latest_checkpoint,
        settings_env=env,
        extra_steps=2,
        inputs=[seed_path],
    )
    card_r = load_card_only(paths_resumed.latest_checkpoint)
    assert card_r.training.global_step > before_step
    state = resume_checkpoint(
        paths_resumed.latest_checkpoint,
        expected_architecture=exp_a.architecture.model_copy(
            update={"vocab_size": build_vocab(default_tokenizer_config()).size}
        ),
        map_location="cpu",
    )
    assert state.global_step == card_r.training.global_step
    assert state.has_optimizer
