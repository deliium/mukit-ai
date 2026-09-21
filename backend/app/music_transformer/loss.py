"""Language-modeling loss (ignore PAD)."""

from __future__ import annotations

import logging
from typing import Any

from app.music_transformer.errors import MusicTransformerDependencyError


logger = logging.getLogger(__name__)


def _require_torch():
    try:
        import torch
        import torch.nn.functional as F
    except ImportError as exc:
        raise MusicTransformerDependencyError(
            "torch_unavailable",
            "PyTorch is required for language_modeling_loss",
        ) from exc
    return torch, F


def language_modeling_loss(
    logits: Any,
    labels: Any,
    *,
    ignore_index: int = 0,
) -> Any:
    """Next-token CE: shift labels by 1 relative to logits positions.

    ``logits``: [B, T, V], ``labels``: [B, T] where labels[:, t] is the target
    for logits[:, t] predicting token t+1 — callers should pass
    input_ids[:, :-1] → logits and input_ids[:, 1:] as labels, **or**
    full-length tensors where we shift internally.
    """
    torch, F = _require_torch()
    # Support both already-shifted and full-length.
    if logits.size(1) == labels.size(1):
        shift_logits = logits[:, :-1, :].contiguous()
        shift_labels = labels[:, 1:].contiguous()
    else:
        shift_logits = logits.contiguous()
        shift_labels = labels.contiguous()
    loss = F.cross_entropy(
        shift_logits.view(-1, shift_logits.size(-1)),
        shift_labels.view(-1),
        ignore_index=ignore_index,
    )
    with torch.no_grad():
        preds = shift_logits.argmax(dim=-1)
        mask = shift_labels != ignore_index
        correct = ((preds == shift_labels) & mask).sum().item()
        total = int(mask.sum().item())
        acc = (correct / total) if total else 0.0
    logger.debug(
        "LM loss computed",
        extra={"loss": float(loss.detach().cpu()), "token_accuracy": acc, "non_pad": total},
    )
    return loss, acc
