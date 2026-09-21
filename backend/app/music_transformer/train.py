"""Offline training loop for MusicTransformerLM."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.music_transformer.checkpoint import (
    build_checkpoint_card,
    default_training_card,
    save_checkpoint,
)
from app.music_transformer.data import (
    collect_input_paths,
    iter_batches,
    load_encoded_corpus,
    read_dataset_version_id,
)
from app.music_transformer.device import resolve_device
from app.music_transformer.errors import MusicTransformerTrainError
from app.music_transformer.loss import language_modeling_loss
from app.music_transformer.model import MusicTransformerLM
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
    """Run a short LM train loop and write a versioned checkpoint."""
    torch = _require_torch()
    settings = load_music_transformer_settings()
    seed_everything(train_config.seed)
    tok_cfg = tokenizer_config or default_tokenizer_config()
    vocab = build_vocab(tok_cfg)
    arch = architecture or train_config.architecture
    if arch is None:
        arch = MusicTransformerConfigV1(
            vocab_size=vocab.size,
            max_seq_len=train_config.max_seq_len,
        )
    if arch.vocab_size != vocab.size:
        logger.info(
            "Aligning architecture vocab_size to tokenizer",
            extra={"from": arch.vocab_size, "to": vocab.size},
        )
        arch = arch.model_copy(update={"vocab_size": vocab.size})

    resolved = resolve_device(device, settings=settings)
    ds_dir = dataset_dir or (
        Path(train_config.dataset_dir) if train_config.dataset_dir else None
    )
    paths = collect_input_paths(
        dataset_dir=ds_dir,
        inputs=inputs,
        inputs_glob=train_config.inputs_glob,
    )
    examples = load_encoded_corpus(paths, config=tok_cfg, max_seq_len=train_config.max_seq_len)
    model = MusicTransformerLM(arch).to(resolved)
    optimizer = torch.optim.AdamW(model.parameters(), lr=train_config.lr)

    model.train()
    step = 0
    last_loss = 0.0
    data_cycle = list(
        iter_batches(
            examples,
            batch_size=train_config.batch_size,
            pad_id=arch.pad_id,
            max_seq_len=train_config.max_seq_len,
        )
    )
    if not data_cycle:
        raise MusicTransformerTrainError("train_data_empty", "No batches produced")

    while step < train_config.steps:
        for batch in data_cycle:
            if step >= train_config.steps:
                break
            input_ids = torch.tensor(batch.input_ids, dtype=torch.long, device=resolved)
            labels = torch.tensor(batch.labels, dtype=torch.long, device=resolved)
            logits = model(input_ids)
            loss, acc = language_modeling_loss(logits, labels, ignore_index=arch.pad_id)
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            if train_config.grad_clip is not None and train_config.grad_clip > 0:
                torch.nn.utils.clip_grad_norm_(model.parameters(), train_config.grad_clip)
            optimizer.step()
            last_loss = float(loss.detach().cpu())
            step += 1
            if step % train_config.log_every == 0 or step == 1:
                logger.info(
                    "Train step",
                    extra={
                        "step": step,
                        "loss": round(last_loss, 6),
                        "token_accuracy": round(float(acc), 4),
                        "device": resolved,
                    },
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
    )
    card = build_checkpoint_card(
        arch,
        training=training_card,
        tokenizer_config=tok_cfg,
        dataset_version_id=read_dataset_version_id(ds_dir),
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
