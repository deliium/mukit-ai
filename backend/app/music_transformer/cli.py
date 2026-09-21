"""CLI entry: ``python -m app.music_transformer.cli <subcommand>``.

Offline only — train/eval/listen/compare never run inside the web API process.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

from app.ready import configure_logging
from app.music_transformer.errors import MusicTransformerError
from app.music_transformer.experiment_schemas import (
    MusicTransformerEvalConfigV1,
    MusicTransformerExperimentV1,
    MusicTransformerListeningConfigV1,
)
from app.music_transformer.schemas import (
    MusicTransformerConfigV1,
    MusicTransformerSampleConfigV1,
    MusicTransformerTrainConfigV1,
)
from app.tokenizer.schemas import TokenizerConditioningV1, default_tokenizer_config
from app.tokenizer.vocab import build_vocab


logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(
        prog="python -m app.music_transformer.cli",
        description=(
            "Symbolic Music Transformer (offline train / generate / eval / listen / compare; "
            "filesystem only — does not block the web API)."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    train_p = sub.add_parser("train", help="Train LM (simple --out or experiment config)")
    train_p.add_argument("--config", type=Path, default=None, help="Train or experiment YAML/JSON")
    train_p.add_argument("--experiment-config", type=Path, default=None, help="Alias for experiment YAML/JSON")
    train_p.add_argument("--arch-config", type=Path, default=None, help="Architecture YAML/JSON")
    train_p.add_argument("--dataset-dir", type=Path, default=None)
    train_p.add_argument("--inputs", nargs="*", type=Path, default=None)
    train_p.add_argument(
        "--val-inputs",
        nargs="*",
        type=Path,
        default=None,
        help="Explicit validation Composition V2 / example JSON paths (CI fixtures)",
    )
    train_p.add_argument("--out", type=Path, default=None, help="Simple smoke checkpoint path")
    train_p.add_argument("--resume", type=Path, default=None, help="Resume from checkpoint (.pt)")
    train_p.add_argument("--experiment-id", type=str, default=None)
    train_p.add_argument("--force", action="store_true")
    train_p.add_argument("--steps", type=int, default=None)
    train_p.add_argument("--batch-size", type=int, default=None)
    train_p.add_argument("--lr", type=float, default=None)
    train_p.add_argument("--seed", type=int, default=None)
    train_p.add_argument("--max-seq-len", type=int, default=None)
    train_p.add_argument("--device", type=str, default=None)
    train_p.add_argument("--extra-steps", type=int, default=None, help="With --resume: add steps")

    gen_p = sub.add_parser("generate", help="Generate Composition V2 from checkpoint")
    gen_p.add_argument("--checkpoint", required=True, type=Path)
    gen_p.add_argument("--out", required=True, type=Path)
    gen_p.add_argument("--prefix", type=Path, default=None, help="Optional seed V2 JSON")
    gen_p.add_argument("--max-new-tokens", type=int, default=64)
    gen_p.add_argument("--temperature", type=float, default=1.0)
    gen_p.add_argument("--top-k", type=int, default=None)
    gen_p.add_argument("--top-p", type=float, default=None)
    gen_p.add_argument("--greedy", action="store_true")
    gen_p.add_argument("--seed", type=int, default=None)
    gen_p.add_argument("--device", type=str, default=None)
    gen_p.add_argument("--key", type=str, default=None)
    gen_p.add_argument("--genre", type=str, default=None)
    gen_p.add_argument("--mood", type=str, default=None)
    gen_p.add_argument("--instrument-set", type=str, default=None)
    gen_p.add_argument("--section-type", type=str, default=None)
    gen_p.add_argument("--no-require-version", action="store_true")

    insp_p = sub.add_parser("inspect", help="Print checkpoint card summary")
    insp_p.add_argument("--checkpoint", required=True, type=Path)

    card_p = sub.add_parser("export-card", help="Write checkpoint card JSON")
    card_p.add_argument("--checkpoint", required=True, type=Path)
    card_p.add_argument("--out", required=True, type=Path)

    eval_p = sub.add_parser("eval", help="Symbolic eval (not musical quality)")
    eval_p.add_argument("--checkpoint", required=True, type=Path)
    eval_p.add_argument("--out", required=True, type=Path, help="Eval output directory")
    eval_p.add_argument("--eval-config", type=Path, default=None)
    eval_p.add_argument("--max-samples", type=int, default=None)
    eval_p.add_argument("--device", type=str, default=None)
    eval_p.add_argument("--prefix", type=Path, default=None)
    eval_p.add_argument("--experiment-id", type=str, default=None)

    listen_p = sub.add_parser("listen", help="Fixed listening-set generations")
    listen_p.add_argument("--checkpoint", required=True, type=Path)
    listen_p.add_argument("--listening-set", required=True, type=Path)
    listen_p.add_argument("--out", required=True, type=Path)
    listen_p.add_argument("--device", type=str, default=None)

    cmp_p = sub.add_parser("compare", help="Compare two experiments/checkpoints (no quality winner)")
    cmp_p.add_argument("--a", required=True, type=Path)
    cmp_p.add_argument("--b", required=True, type=Path)
    cmp_p.add_argument("--out", required=True, type=Path)

    args = parser.parse_args(argv)
    started = time.perf_counter()
    try:
        if args.command == "train":
            _cmd_train(args)
        elif args.command == "generate":
            _cmd_generate(args)
        elif args.command == "inspect":
            _cmd_inspect(args)
        elif args.command == "export-card":
            _cmd_export_card(args)
        elif args.command == "eval":
            _cmd_eval(args)
        elif args.command == "listen":
            _cmd_listen(args)
        elif args.command == "compare":
            _cmd_compare(args)
        else:
            parser.error(f"Unknown command: {args.command}")
    except MusicTransformerError as exc:
        logger.error(
            "Music Transformer command failed",
            extra={"code": exc.code, "command": args.command},
        )
        print(json.dumps({"ok": False, "code": exc.code, "message": exc.message}), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected Music Transformer failure")
        print(json.dumps({"ok": False, "code": "unexpected", "message": str(exc)}), file=sys.stderr)
        return 2
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "Music Transformer command finished",
        extra={"command": args.command, "elapsed_ms": elapsed_ms},
    )
    return 0


def _cmd_train(args: argparse.Namespace) -> None:
    from app.music_transformer.train import resume_experiment, train_experiment, train_model

    config_path = args.experiment_config or args.config
    if args.resume and not config_path and args.experiment_id:
        paths = resume_experiment(
            args.experiment_id,
            resume_checkpoint_path=args.resume,
            extra_steps=args.extra_steps,
            inputs=list(args.inputs) if args.inputs else None,
            val_inputs=list(args.val_inputs) if args.val_inputs else None,
        )
        print(
            json.dumps(
                {
                    "ok": True,
                    "experiment_id": paths.root.name,
                    "checkpoint": paths.latest_checkpoint.name,
                    "resumed": True,
                },
                indent=2,
            )
        )
        return

    if config_path is not None:
        raw = _load_json_or_yaml(config_path)
        if raw.get("schema_version") == "music_transformer.experiment.v1" or (
            "experiment_id" in raw and "architecture" in raw and "train" in raw
        ):
            experiment = MusicTransformerExperimentV1.model_validate(raw)
            if args.experiment_id:
                experiment = experiment.model_copy(update={"experiment_id": args.experiment_id})
            if args.device:
                experiment = experiment.model_copy(update={"device": args.device})
            if args.seed is not None:
                experiment = experiment.model_copy(
                    update={
                        "seed": args.seed,
                        "train": experiment.train.model_copy(update={"seed": args.seed}),
                    }
                )
            if args.steps is not None:
                experiment = experiment.model_copy(
                    update={"train": experiment.train.model_copy(update={"steps": args.steps})}
                )
            if args.dataset_dir is not None:
                experiment = experiment.model_copy(
                    update={
                        "train": experiment.train.model_copy(
                            update={"dataset_dir": str(args.dataset_dir)}
                        )
                    }
                )
            paths = train_experiment(
                experiment,
                force=bool(args.force),
                resume_path=args.resume,
                inputs=list(args.inputs) if args.inputs else None,
                val_inputs=list(args.val_inputs) if args.val_inputs else None,
            )
            print(
                json.dumps(
                    {
                        "ok": True,
                        "experiment_id": experiment.experiment_id,
                        "checkpoint": paths.latest_checkpoint.name
                        if paths.latest_checkpoint.exists()
                        else None,
                    },
                    indent=2,
                )
            )
            return

    # Simple smoke path (backward compatible)
    if args.out is None:
        raise MusicTransformerError(
            "invalid_config",
            "Simple train requires --out, or pass an experiment config",
        )
    train_cfg = _load_train_config(args, config_path)
    arch = _load_arch_config(args.arch_config, train_cfg)
    tok_cfg = default_tokenizer_config()
    vocab = build_vocab(tok_cfg)
    if arch.vocab_size != vocab.size:
        arch = arch.model_copy(update={"vocab_size": vocab.size})
    out = train_model(
        train_cfg,
        architecture=arch,
        tokenizer_config=tok_cfg,
        dataset_dir=args.dataset_dir,
        inputs=list(args.inputs) if args.inputs else None,
        out_checkpoint=args.out,
        device=args.device,
    )
    print(json.dumps({"ok": True, "checkpoint": out.name}, indent=2))


def _cmd_generate(args: argparse.Namespace) -> None:
    from app.music_transformer.inference import generate_composition

    sample = MusicTransformerSampleConfigV1(
        temperature=args.temperature,
        top_k=args.top_k,
        top_p=args.top_p,
        greedy=bool(args.greedy),
        max_new_tokens=args.max_new_tokens,
    )
    conditioning = TokenizerConditioningV1(
        key=args.key,
        genre=args.genre,
        mood=args.mood,
        instrument_set=args.instrument_set,
        section_type=args.section_type,
    )
    prefix = None
    if args.prefix is not None:
        prefix = json.loads(Path(args.prefix).read_text(encoding="utf-8"))
    composition, report = generate_composition(
        args.checkpoint,
        conditioning=conditioning,
        prefix_composition=prefix,
        sample_config=sample,
        require_tokenizer_version=not args.no_require_version,
        device=args.device,
        seed=args.seed,
    )
    Path(args.out).write_text(
        json.dumps(composition.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "ok": True,
                "out": Path(args.out).name,
                "status": report.status,
                "notes_out": report.notes_out,
                "stop_reason": report.stop_reason,
            },
            indent=2,
        )
    )


def _cmd_inspect(args: argparse.Namespace) -> None:
    from app.music_transformer.checkpoint import load_card_only

    card = load_card_only(args.checkpoint)
    summary = {
        "schema_version": card.schema_version,
        "package_version": card.package_version,
        "architecture_digest_prefix": card.architecture_digest[:12],
        "n_layers": card.architecture.n_layers,
        "d_model": card.architecture.d_model,
        "vocab_size": card.architecture.vocab_size,
        "tokenizer_version": card.tokenizer.expected_tokenizer_version,
        "vocab_hash_prefix": card.tokenizer.vocab_hash[:12],
        "dataset_version_id": card.dataset_version_id,
        "experiment_id": card.training.experiment_id,
        "global_step": card.training.global_step,
        "training_steps": card.training.steps,
        "training_seed": card.training.seed,
        "precision": card.training.precision,
        "device": card.training.device,
        "git_commit": card.training.git_commit,
    }
    print(json.dumps(summary, indent=2))


def _cmd_export_card(args: argparse.Namespace) -> None:
    from app.music_transformer.checkpoint import load_card_only

    card = load_card_only(args.checkpoint)
    Path(args.out).write_text(
        json.dumps(card.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"ok": True, "out": Path(args.out).name}, indent=2))


def _cmd_eval(args: argparse.Namespace) -> None:
    from app.music_transformer.evaluate import evaluate_checkpoint

    if args.eval_config:
        eval_cfg = MusicTransformerEvalConfigV1.model_validate(_load_json_or_yaml(args.eval_config))
    else:
        eval_cfg = MusicTransformerEvalConfigV1()
    if args.max_samples is not None:
        eval_cfg = eval_cfg.model_copy(update={"max_samples": args.max_samples})
    prefix = None
    if args.prefix is not None:
        prefix = json.loads(Path(args.prefix).read_text(encoding="utf-8"))
    report = evaluate_checkpoint(
        args.checkpoint,
        eval_config=eval_cfg,
        out_dir=args.out,
        experiment_id=args.experiment_id,
        device=args.device,
        prefix_composition=prefix,
    )
    print(
        json.dumps(
            {
                "ok": True,
                "sample_count": report.sample_count,
                "musical_quality_claim": report.musical_quality_claim,
                "metric_count": len(report.metrics),
                "disclaimer": report.disclaimer,
            },
            indent=2,
        )
    )


def _cmd_listen(args: argparse.Namespace) -> None:
    from app.music_transformer.listening import run_listening_set

    listening = MusicTransformerListeningConfigV1(
        set_path=str(args.listening_set),
        enabled=True,
    )
    results = run_listening_set(
        args.checkpoint,
        listening,
        args.out,
        device=args.device,
    )
    print(
        json.dumps(
            {
                "ok": True,
                "count": len(results),
                "out": Path(args.out).name,
                "musical_quality_claim": False,
            },
            indent=2,
        )
    )


def _cmd_compare(args: argparse.Namespace) -> None:
    from app.music_transformer.compare import compare_experiments, write_compare_report

    report = compare_experiments(args.a, args.b)
    write_compare_report(report, args.out)
    print(
        json.dumps(
            {
                "ok": True,
                "a_id": report.a_id,
                "b_id": report.b_id,
                "configs_equal": report.configs_equal,
                "seeds_equal": report.seeds_equal,
                "musical_quality_claim": report.musical_quality_claim,
                "out": Path(args.out).name,
            },
            indent=2,
        )
    )


def _load_json_or_yaml(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise MusicTransformerError(
                "invalid_config",
                "PyYAML required to load YAML configs",
            ) from exc
        raw = yaml.safe_load(text)
    else:
        raw = json.loads(text)
    if not isinstance(raw, dict):
        raise MusicTransformerError("invalid_config", "Config root must be an object")
    return raw


def _load_train_config(
    args: argparse.Namespace,
    config_path: Path | None,
) -> MusicTransformerTrainConfigV1:
    if config_path:
        raw = _load_json_or_yaml(config_path)
        cfg = MusicTransformerTrainConfigV1.model_validate(raw)
    else:
        cfg = MusicTransformerTrainConfigV1()
    updates: dict[str, Any] = {}
    if args.steps is not None:
        updates["steps"] = args.steps
    if args.batch_size is not None:
        updates["batch_size"] = args.batch_size
    if args.lr is not None:
        updates["lr"] = args.lr
    if args.seed is not None:
        updates["seed"] = args.seed
    if args.max_seq_len is not None:
        updates["max_seq_len"] = args.max_seq_len
    if args.dataset_dir is not None:
        updates["dataset_dir"] = str(args.dataset_dir)
    return cfg.model_copy(update=updates) if updates else cfg


def _load_arch_config(
    path: Path | None,
    train_cfg: MusicTransformerTrainConfigV1,
) -> MusicTransformerConfigV1:
    if path is not None:
        return MusicTransformerConfigV1.model_validate(_load_json_or_yaml(path))
    if train_cfg.architecture is not None:
        return train_cfg.architecture
    vocab = build_vocab(default_tokenizer_config())
    from app.music_transformer.schemas import tiny_test_config

    return tiny_test_config(vocab_size=vocab.size).model_copy(
        update={"max_seq_len": train_cfg.max_seq_len}
    )


if __name__ == "__main__":
    raise SystemExit(main())
