"""Optimizer and LR scheduler factories for offline training."""

from __future__ import annotations

import logging
from typing import Any

from app.music_transformer.schemas import MusicTransformerTrainConfigV1, SchedulerKind


logger = logging.getLogger(__name__)


def build_optimizer(model: Any, train_config: MusicTransformerTrainConfigV1) -> Any:
    import torch

    if train_config.optimizer != "adamw":
        raise ValueError(f"Unsupported optimizer: {train_config.optimizer}")
    opt = torch.optim.AdamW(
        model.parameters(),
        lr=train_config.lr,
        weight_decay=train_config.weight_decay,
    )
    logger.info(
        "Optimizer created",
        extra={
            "optimizer": train_config.optimizer,
            "lr": train_config.lr,
            "weight_decay": train_config.weight_decay,
        },
    )
    return opt


def build_scheduler(
    optimizer: Any,
    train_config: MusicTransformerTrainConfigV1,
) -> Any | None:
    import torch

    kind: SchedulerKind = train_config.scheduler
    if kind == "none":
        return None
    total_steps = max(1, train_config.steps)
    warmup = min(train_config.warmup_steps, total_steps - 1) if total_steps > 1 else 0
    if kind == "linear_warmup":

        def lr_lambda(step: int) -> float:
            if warmup > 0 and step < warmup:
                return float(step + 1) / float(warmup)
            return max(0.0, float(total_steps - step) / float(max(1, total_steps - warmup)))

        sched = torch.optim.lr_scheduler.LambdaLR(optimizer, lr_lambda)
    elif kind == "cosine":
        if warmup > 0:

            def lr_lambda(step: int) -> float:
                if step < warmup:
                    return float(step + 1) / float(warmup)
                progress = float(step - warmup) / float(max(1, total_steps - warmup))
                import math

                return 0.5 * (1.0 + math.cos(math.pi * progress))

            sched = torch.optim.lr_scheduler.LambdaLR(optimizer, lr_lambda)
        else:
            sched = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=total_steps)
    else:
        raise ValueError(f"Unsupported scheduler: {kind}")
    logger.info(
        "Scheduler created",
        extra={"scheduler": kind, "warmup_steps": warmup, "total_steps": total_steps},
    )
    return sched


def current_lr(optimizer: Any) -> float:
    try:
        return float(optimizer.param_groups[0]["lr"])
    except (IndexError, KeyError, TypeError):
        return 0.0
