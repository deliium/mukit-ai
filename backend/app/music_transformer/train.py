"""Offline training loop for MusicTransformerLM.

Supports both the simple smoke path (``train_model`` → single ``.pt``) and the
filesystem experiment runner (resume, AMP, grad accum, metrics, early stop).
Never imports FastAPI; never writes to ``PROJECT_DB_PATH``.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Sequence

from app.music_transformer.checkpoint import (
    build_checkpoint_card,
    default_training_card,
    resume_checkpoint,
    save_checkpoint,
)
from app.music_transformer.data import (
    EncodedExample,
    collect_input_paths,
    count_non_pad_tokens,
    iter_batches,
    load_encoded_corpus,
    read_dataset_version_id,
    split_train_val_examples,
)
from app.music_transformer.device import resolve_device
from app.music_transformer.errors import MusicTransformerTrainError
from app.music_transformer.experiment_schemas import MusicTransformerExperimentV1
from app.music_transformer.experiments import (
    ExperimentPaths,
    auto_experiment_id,
    checkpoint_step_path,
    create_experiment,
    load_experiment,
)
from app.music_transformer.loss import language_modeling_loss
from app.music_transformer.metrics import MetricsRecorder, measure_memory_mb
from app.music_transformer.model import MusicTransformerLM
from app.music_transformer.optim import build_optimizer, build_scheduler, current_lr
from app.music_transformer.precision import PrecisionContext, build_precision_context
from app.music_transformer.reproducibility import seed_everything
from app.music_transformer.schemas import (
    MusicTransformerConfigV1,
    MusicTransformerTrainConfigV1,
)
from app.music_transformer.settings import load_music_transformer_settings
from app.tokenizer.schemas import TokenizerConfigV1, default_tokenizer_config
from app.tokenizer.vocab import build_vocab


logger = logging.getLogger(__name__)


def _require_torch():
    import torch

    return torch


def train_model(
    train_config: MusicTransformerTrainConfigV1,
    *,
    architecture: MusicTransformerConfigV1 | None = None,
    tokenizer_config: TokenizerConfigV1 | None = None,
    dataset_dir: Path | None = None,
    inputs: list[Path] | None = None,
    out_checkpoint: Path,
    device: str | None = None,
) -> Path:
    """Backward-compatible smoke train: write a single checkpoint path."""
    torch = _require_torch()
    settings = load_music_transformer_settings()
    seed_everything(train_config.seed)
    tok_cfg = tokenizer_config or default_tokenizer_config()
    vocab = build_vocab(tok_cfg)
    arch = _resolve_architecture(architecture, train_config, vocab.size)
    resolved = resolve_device(device, settings=settings)
    ds_dir = dataset_dir or (
        Path(train_config.dataset_dir) if train_config.dataset_dir else None
    )
    train_examples, _val = _load_corpora(
        train_config,
        tok_cfg=tok_cfg,
        dataset_dir=ds_dir,
        inputs=inputs,
        val_inputs=None,
    )
    model = MusicTransformerLM(arch).to(resolved)
    optimizer = build_optimizer(model, train_config)
    precision = build_precision_context(
        train_config.precision,
        device=resolved,
        precision_fallback=train_config.precision_fallback or "",
        torch_module=torch,
    )
    step, last_loss = _run_train_loop(
        model=model,
        optimizer=optimizer,
        scheduler=None,
        precision=precision,
        train_examples=train_examples,
        val_examples=[],
        arch=arch,
        train_config=train_config,
        device=resolved,
        start_step=0,
        metrics=None,
        experiment_id=None,
        paths=None,
        tok_cfg=tok_cfg,
        dataset_version_id=read_dataset_version_id(ds_dir) or train_config.dataset_version_id,
        dataset_name=ds_dir.name if ds_dir else None,
    )
    training_card = default_training_card(
        seed=train_config.seed,
        steps=step,
        batch_size=train_config.batch_size,
        lr=train_config.lr,
        max_seq_len=train_config.max_seq_len,
        device=resolved,
        epochs=train_config.epochs,
        final_loss=last_loss,
        global_step=step,
        optimizer=train_config.optimizer,
        scheduler=train_config.scheduler,
        precision=precision.precision,
        grad_accum_steps=train_config.grad_accum_steps,
        weight_decay=train_config.weight_decay,
        warmup_steps=train_config.warmup_steps,
    )
    card = build_checkpoint_card(
        arch,
        training=training_card,
        tokenizer_config=tok_cfg,
        dataset_version_id=read_dataset_version_id(ds_dir) or train_config.dataset_version_id,
        dataset_name=ds_dir.name if ds_dir else None,
    )
    out = Path(out_checkpoint)
    save_checkpoint(out, model, card, optimizer=optimizer)
    logger.info(
        "Training complete",
        extra={
            "steps": step,
            "final_loss": round(last_loss, 6),
            "checkpoint_basename": out.name,
        },
    )
    return out


def train_experiment(
    experiment: MusicTransformerExperimentV1,
    *,
    force: bool = False,
    resume_path: Path | None = None,
    tokenizer_config: TokenizerConfigV1 | None = None,
    inputs: list[Path] | None = None,
    val_inputs: list[Path] | None = None,
    settings_env: dict[str, str] | None = None,
    run_listening_on_end: bool | None = None,
) -> ExperimentPaths:
    """Create/load experiment dir and run the full trainer."""
    torch = _require_torch()
    tok_cfg = tokenizer_config or default_tokenizer_config()
    vocab = build_vocab(tok_cfg)
    arch = experiment.architecture
    if arch.vocab_size != vocab.size:
        arch = arch.model_copy(update={"vocab_size": vocab.size})
        experiment = experiment.model_copy(update={"architecture": arch})

    if not experiment.experiment_id:
        digest = experiment.train.config_digest()
        experiment = experiment.model_copy(
            update={"experiment_id": auto_experiment_id(config_digest=digest)}
        )

    if resume_path is not None:
        paths, frozen = load_experiment(
            experiment.experiment_id
            if experiment.output_root is None
            else Path(experiment.output_root) / experiment.experiment_id,
            settings_env=settings_env,
        )
        # Prefer frozen architecture from disk
        arch = frozen.architecture
        if arch.vocab_size != vocab.size:
            arch = arch.model_copy(update={"vocab_size": vocab.size})
    else:
        paths, frozen = create_experiment(
            experiment,
            force=force,
            tokenizer_config=tok_cfg,
            settings_env=settings_env,
        )
    seed_everything(frozen.seed)
    settings = load_music_transformer_settings(settings_env)
    resolved = resolve_device(frozen.device or settings.device, settings=settings)
    train_cfg = frozen.train
    ds_dir = Path(train_cfg.dataset_dir) if train_cfg.dataset_dir else None
    train_examples, val_examples = _load_corpora(
        train_cfg,
        tok_cfg=tok_cfg,
        dataset_dir=ds_dir,
        inputs=inputs,
        val_inputs=val_inputs,
    )
    model = MusicTransformerLM(arch).to(resolved)
    optimizer = build_optimizer(model, train_cfg)
    scheduler = build_scheduler(optimizer, train_cfg)
    precision = build_precision_context(
        train_cfg.precision,
        device=resolved,
        precision_fallback=train_cfg.precision_fallback or "",
        torch_module=torch,
    )

    start_step = 0
    start_epoch = 0
    if resume_path is not None:
        state = resume_checkpoint(
            resume_path,
            expected_architecture=arch,
            expected_tokenizer=frozen.tokenizer_expectation,
            expected_experiment_id=frozen.experiment_id,
            tokenizer_config=tok_cfg,
            map_location=resolved,
        )
        model.load_state_dict(state.payload["model_state_dict"])
        if state.has_optimizer:
            optimizer.load_state_dict(state.payload["optimizer_state_dict"])
        if state.has_scheduler and scheduler is not None:
            scheduler.load_state_dict(state.payload["scheduler_state_dict"])
        if state.has_scaler and precision.scaler is not None:
            precision.scaler.load_state_dict(state.payload["scaler_state_dict"])
        start_step = state.global_step
        start_epoch = state.epoch
        logger.info(
            "Resumed experiment training",
            extra={
                "experiment_id": frozen.experiment_id,
                "global_step": start_step,
                "epoch": start_epoch,
            },
        )

    metrics = MetricsRecorder(
        experiment_id=frozen.experiment_id,
        metrics_jsonl=paths.metrics_jsonl,
        metrics_summary=paths.metrics_summary,
    )
    _run_train_loop(
        model=model,
        optimizer=optimizer,
        scheduler=scheduler,
        precision=precision,
        train_examples=train_examples,
        val_examples=val_examples,
        arch=arch,
        train_config=train_cfg,
        device=resolved,
        start_step=start_step,
        start_epoch=start_epoch,
        metrics=metrics,
        experiment_id=frozen.experiment_id,
        paths=paths,
        tok_cfg=tok_cfg,
        dataset_version_id=frozen.dataset_version_id
        or read_dataset_version_id(ds_dir)
        or train_cfg.dataset_version_id,
        dataset_name=frozen.dataset_name or (ds_dir.name if ds_dir else None),
        eval_config=frozen.eval if frozen.eval.enabled else None,
    )
    metrics.finalize()

    should_listen = (
        run_listening_on_end
        if run_listening_on_end is not None
        else (frozen.listening.enabled and frozen.listening.run_on_train_end)
    )
    if should_listen and paths.latest_checkpoint.is_file():
        try:
            from app.music_transformer.listening import run_listening_set

            run_listening_set(
                paths.latest_checkpoint,
                frozen.listening,
                paths.listening_dir,
                device=resolved,
                tokenizer_config=tok_cfg,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Listening set skipped after train",
                extra={
                    "experiment_id": frozen.experiment_id,
                    "error_type": type(exc).__name__,
                },
            )
    return paths


def resume_experiment(
    experiment_id_or_path: str | Path,
    *,
    resume_checkpoint_path: Path | None = None,
    tokenizer_config: TokenizerConfigV1 | None = None,
    settings_env: dict[str, str] | None = None,
    extra_steps: int | None = None,
    inputs: list[Path] | None = None,
    val_inputs: list[Path] | None = None,
) -> ExperimentPaths:
    paths, experiment = load_experiment(experiment_id_or_path, settings_env=settings_env)
    ckpt = resume_checkpoint_path or paths.latest_checkpoint
    if not Path(ckpt).is_file():
        raise MusicTransformerTrainError(
            "checkpoint_missing",
            f"Resume checkpoint missing: {Path(ckpt).name}",
        )
    if extra_steps is not None and extra_steps > 0:
        # Continue for additional optimizer steps beyond current card
        from app.music_transformer.checkpoint import load_card_only

        card = load_card_only(ckpt)
        new_total = max(experiment.train.steps, card.training.global_step + extra_steps)
        experiment = experiment.model_copy(
            update={"train": experiment.train.model_copy(update={"steps": new_total})}
        )
        # Persist updated step budget into frozen config for transparency
        paths.config_json.write_text(
            json.dumps(experiment.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return train_experiment(
        experiment,
        resume_path=Path(ckpt),
        tokenizer_config=tokenizer_config,
        settings_env=settings_env,
        inputs=inputs,
        val_inputs=val_inputs,
    )


def _resolve_architecture(
    architecture: MusicTransformerConfigV1 | None,
    train_config: MusicTransformerTrainConfigV1,
    vocab_size: int,
) -> MusicTransformerConfigV1:
    arch = architecture or train_config.architecture
    if arch is None:
        arch = MusicTransformerConfigV1(
            vocab_size=vocab_size,
            max_seq_len=train_config.max_seq_len,
        )
    if arch.vocab_size != vocab_size:
        logger.info(
            "Aligning architecture vocab_size to tokenizer",
            extra={"from": arch.vocab_size, "to": vocab_size},
        )
        arch = arch.model_copy(update={"vocab_size": vocab_size})
    return arch


def _load_corpora(
    train_config: MusicTransformerTrainConfigV1,
    *,
    tok_cfg: TokenizerConfigV1,
    dataset_dir: Path | None,
    inputs: Sequence[Path] | None,
    val_inputs: Sequence[Path] | None,
) -> tuple[list[EncodedExample], list[EncodedExample]]:
    train_paths = collect_input_paths(
        dataset_dir=dataset_dir,
        inputs=inputs,
        inputs_glob=train_config.inputs_glob,
        split="train",
    )
    # If split manifest missing, collect_input_paths already fell back to all examples
    if not train_paths and inputs:
        train_paths = list(inputs)
    train_examples = load_encoded_corpus(
        train_paths,
        config=tok_cfg,
        max_seq_len=train_config.max_seq_len,
    )

    val_paths = collect_input_paths(
        dataset_dir=dataset_dir,
        inputs=val_inputs,
        inputs_glob=train_config.val_inputs_glob,
        split="validation",
    )
    val_examples: list[EncodedExample] = []
    if val_paths:
        try:
            val_examples = load_encoded_corpus(
                val_paths,
                config=tok_cfg,
                max_seq_len=train_config.max_seq_len,
            )
        except MusicTransformerTrainError:
            logger.warning("Validation corpus empty after encode", extra={"val_empty": True})
            val_examples = []
    elif train_config.val_fraction:
        train_examples, val_examples = split_train_val_examples(
            train_examples,
            val_fraction=train_config.val_fraction,
            seed=train_config.seed,
        )
    else:
        logger.warning(
            "No validation split; skipping val metrics",
            extra={"code": "val_empty"},
        )
    return train_examples, val_examples


def _run_train_loop(
    *,
    model: Any,
    optimizer: Any,
    scheduler: Any | None,
    precision: PrecisionContext,
    train_examples: list[EncodedExample],
    val_examples: list[EncodedExample],
    arch: MusicTransformerConfigV1,
    train_config: MusicTransformerTrainConfigV1,
    device: str,
    start_step: int,
    metrics: MetricsRecorder | None,
    experiment_id: str | None,
    paths: ExperimentPaths | None,
    tok_cfg: TokenizerConfigV1,
    dataset_version_id: str | None,
    dataset_name: str | None,
    start_epoch: int = 0,
    eval_config: Any | None = None,
) -> tuple[int, float]:
    torch = _require_torch()
    model.train()
    step = start_step
    epoch = start_epoch
    last_loss = 0.0
    accum = max(1, train_config.grad_accum_steps)
    data_cycle = list(
        iter_batches(
            train_examples,
            batch_size=train_config.batch_size,
            pad_id=arch.pad_id,
            max_seq_len=train_config.max_seq_len,
        )
    )
    if not data_cycle:
        raise MusicTransformerTrainError("train_data_empty", "No batches produced")

    early = train_config.early_stopping
    patience_left = early.patience if early.enabled else None
    best_val = None
    micro = 0
    optimizer.zero_grad(set_to_none=True)

    while step < train_config.steps:
        epoch += 1
        for batch in data_cycle:
            if step >= train_config.steps:
                break
            t0 = time.perf_counter()
            input_ids = torch.tensor(batch.input_ids, dtype=torch.long, device=device)
            labels = torch.tensor(batch.labels, dtype=torch.long, device=device)
            logger.debug(
                "Train batch shapes",
                extra={
                    "step": step + 1,
                    "batch": input_ids.shape[0],
                    "seq": input_ids.shape[1],
                },
            )
            if precision.use_amp:
                with torch.autocast(
                    device_type=precision.device_type,
                    dtype=precision.amp_dtype,
                ):
                    logits = model(input_ids)
                    loss, acc = language_modeling_loss(
                        logits, labels, ignore_index=arch.pad_id
                    )
                    loss = loss / accum
            else:
                logits = model(input_ids)
                loss, acc = language_modeling_loss(logits, labels, ignore_index=arch.pad_id)
                loss = loss / accum

            if precision.scaler is not None:
                precision.scaler.scale(loss).backward()
            else:
                loss.backward()

            micro += 1
            tokens = count_non_pad_tokens(batch.labels, pad_id=arch.pad_id)
            last_loss = float(loss.detach().cpu()) * accum

            if micro % accum == 0:
                if precision.scaler is not None:
                    precision.scaler.unscale_(optimizer)
                if train_config.grad_clip is not None and train_config.grad_clip > 0:
                    torch.nn.utils.clip_grad_norm_(model.parameters(), train_config.grad_clip)
                if precision.scaler is not None:
                    precision.scaler.step(optimizer)
                    precision.scaler.update()
                else:
                    optimizer.step()
                if scheduler is not None:
                    scheduler.step()
                optimizer.zero_grad(set_to_none=True)
                step += 1
                elapsed = time.perf_counter() - t0
                lr = current_lr(optimizer)
                mem = measure_memory_mb(device)
                if metrics is not None and (
                    step % train_config.log_every == 0 or step == start_step + 1
                ):
                    metrics.record(
                        global_step=step,
                        split="train",
                        loss=last_loss,
                        token_accuracy=float(acc),
                        lr=lr,
                        tokens_processed=tokens,
                        elapsed_sec=elapsed,
                        mem_mb=mem,
                        epoch=epoch,
                    )
                elif step % train_config.log_every == 0 or step == 1:
                    logger.info(
                        "Train step",
                        extra={
                            "step": step,
                            "loss": round(last_loss, 6),
                            "token_accuracy": round(float(acc), 4),
                            "lr": round(lr, 8),
                            "device": device,
                            "experiment_id": experiment_id,
                            "grad_accum_steps": accum,
                        },
                    )

                do_ckpt = (
                    paths is not None
                    and train_config.checkpoint_interval > 0
                    and step % train_config.checkpoint_interval == 0
                )
                do_eval = (
                    train_config.eval_interval > 0
                    and step % train_config.eval_interval == 0
                )
                val_loss = None
                if do_eval and val_examples:
                    val_loss = _eval_val_loss(
                        model,
                        val_examples,
                        arch=arch,
                        train_config=train_config,
                        device=device,
                        precision=precision,
                    )
                    if metrics is not None:
                        metrics.record(
                            global_step=step,
                            split="val",
                            val_loss=val_loss,
                            lr=lr,
                            epoch=epoch,
                        )
                    if early.enabled and val_loss is not None:
                        improved = best_val is None or (
                            best_val - val_loss > early.min_delta
                        )
                        if improved:
                            best_val = val_loss
                            patience_left = early.patience
                        else:
                            assert patience_left is not None
                            patience_left -= 1
                            if patience_left <= 0:
                                logger.info(
                                    "Early stopping triggered",
                                    extra={
                                        "experiment_id": experiment_id,
                                        "global_step": step,
                                        "val_loss": round(val_loss, 6),
                                    },
                                )
                                if metrics is not None:
                                    metrics.mark_early_stopped()
                                _save_train_checkpoint(
                                    model,
                                    optimizer,
                                    scheduler,
                                    precision,
                                    arch=arch,
                                    train_config=train_config,
                                    device=device,
                                    step=step,
                                    epoch=epoch,
                                    last_loss=last_loss,
                                    experiment_id=experiment_id,
                                    paths=paths,
                                    tok_cfg=tok_cfg,
                                    dataset_version_id=dataset_version_id,
                                    dataset_name=dataset_name,
                                )
                                return step, last_loss

                if do_ckpt:
                    _save_train_checkpoint(
                        model,
                        optimizer,
                        scheduler,
                        precision,
                        arch=arch,
                        train_config=train_config,
                        device=device,
                        step=step,
                        epoch=epoch,
                        last_loss=last_loss,
                        experiment_id=experiment_id,
                        paths=paths,
                        tok_cfg=tok_cfg,
                        dataset_version_id=dataset_version_id,
                        dataset_name=dataset_name,
                    )
                if do_eval and eval_config is not None and paths is not None:
                    try:
                        from app.music_transformer.evaluate import evaluate_checkpoint

                        latest = paths.latest_checkpoint
                        if latest.is_file():
                            evaluate_checkpoint(
                                latest,
                                eval_config=eval_config,
                                out_dir=paths.eval_dir,
                                global_step=step,
                                experiment_id=experiment_id,
                                val_examples=val_examples,
                                device=device,
                                tokenizer_config=tok_cfg,
                            )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "Inline eval failed",
                            extra={
                                "experiment_id": experiment_id,
                                "error_type": type(exc).__name__,
                            },
                        )

    # Flush leftover micro-batches that never hit accum boundary
    if micro % accum != 0:
        if precision.scaler is not None:
            precision.scaler.unscale_(optimizer)
        if train_config.grad_clip is not None and train_config.grad_clip > 0:
            torch.nn.utils.clip_grad_norm_(model.parameters(), train_config.grad_clip)
        if precision.scaler is not None:
            precision.scaler.step(optimizer)
            precision.scaler.update()
        else:
            optimizer.step()
        if scheduler is not None:
            scheduler.step()
        optimizer.zero_grad(set_to_none=True)
        step = min(step + 1, train_config.steps)

    _save_train_checkpoint(
        model,
        optimizer,
        scheduler,
        precision,
        arch=arch,
        train_config=train_config,
        device=device,
        step=step,
        epoch=epoch,
        last_loss=last_loss,
        experiment_id=experiment_id,
        paths=paths,
        tok_cfg=tok_cfg,
        dataset_version_id=dataset_version_id,
        dataset_name=dataset_name,
    )
    return step, last_loss


def _eval_val_loss(
    model: Any,
    val_examples: list[EncodedExample],
    *,
    arch: MusicTransformerConfigV1,
    train_config: MusicTransformerTrainConfigV1,
    device: str,
    precision: PrecisionContext,
) -> float:
    torch = _require_torch()
    model.eval()
    total = 0.0
    count = 0
    with torch.no_grad():
        for batch in iter_batches(
            val_examples,
            batch_size=train_config.batch_size,
            pad_id=arch.pad_id,
            max_seq_len=train_config.max_seq_len,
        ):
            input_ids = torch.tensor(batch.input_ids, dtype=torch.long, device=device)
            labels = torch.tensor(batch.labels, dtype=torch.long, device=device)
            if precision.use_amp:
                with torch.autocast(
                    device_type=precision.device_type,
                    dtype=precision.amp_dtype,
                ):
                    logits = model(input_ids)
                    loss, _acc = language_modeling_loss(
                        logits, labels, ignore_index=arch.pad_id
                    )
            else:
                logits = model(input_ids)
                loss, _acc = language_modeling_loss(
                    logits, labels, ignore_index=arch.pad_id
                )
            total += float(loss.detach().cpu())
            count += 1
    model.train()
    return total / max(1, count)


def _save_train_checkpoint(
    model: Any,
    optimizer: Any,
    scheduler: Any | None,
    precision: PrecisionContext,
    *,
    arch: MusicTransformerConfigV1,
    train_config: MusicTransformerTrainConfigV1,
    device: str,
    step: int,
    epoch: int,
    last_loss: float,
    experiment_id: str | None,
    paths: ExperimentPaths | None,
    tok_cfg: TokenizerConfigV1,
    dataset_version_id: str | None,
    dataset_name: str | None,
) -> Path | None:
    if paths is None:
        return None
    training_card = default_training_card(
        seed=train_config.seed,
        steps=step,
        batch_size=train_config.batch_size,
        lr=current_lr(optimizer),
        max_seq_len=train_config.max_seq_len,
        device=device,
        epochs=train_config.epochs,
        final_loss=last_loss,
        experiment_id=experiment_id,
        global_step=step,
        epoch=epoch,
        optimizer=train_config.optimizer,
        scheduler=train_config.scheduler,
        precision=precision.precision,
        grad_accum_steps=train_config.grad_accum_steps,
        weight_decay=train_config.weight_decay,
        warmup_steps=train_config.warmup_steps,
    )
    card = build_checkpoint_card(
        arch,
        training=training_card,
        tokenizer_config=tok_cfg,
        dataset_version_id=dataset_version_id,
        dataset_name=dataset_name,
    )
    out = checkpoint_step_path(paths, step)
    save_checkpoint(
        out,
        model,
        card,
        optimizer=optimizer,
        scheduler=scheduler,
        scaler=precision.scaler,
        latest_link=paths.latest_checkpoint,
    )
    logger.info(
        "Experiment checkpoint saved",
        extra={
            "experiment_id": experiment_id,
            "global_step": step,
            "basename": out.name,
        },
    )
    return out
