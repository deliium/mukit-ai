"""Low-rank adapters on Music Transformer attention projections.

``blocks.py`` stays unchanged. This module replaces ``qkv`` and ``out_proj``
with a wrapper that adds ``(alpha / rank) * B(A(x))``. Torch is imported
only when a caller attaches or trains an adapter.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _require_torch():
    try:
        import torch
        import torch.nn as nn
    except ImportError as exc:
        from app.music_transformer.errors import MusicTransformerDependencyError

        raise MusicTransformerDependencyError(
            "torch_unavailable",
            "PyTorch is required for a personal LoRA adapter",
        ) from exc
    return torch, nn


def torch_is_available() -> bool:
    """Return whether torch imports. Does not construct a model."""
    try:
        import torch  # noqa: F401
    except ImportError:
        return False
    return True


def _lora_linear_cls():
    torch, nn = _require_torch()

    class LoRALinear(nn.Module):
        """Frozen base projection plus trainable rank factors A and B."""

        def __init__(self, base: nn.Module, *, rank: int, alpha: int, dropout: float) -> None:
            super().__init__()
            self.base = base
            for parameter in self.base.parameters():
                parameter.requires_grad = False
            in_features = int(base.in_features)
            out_features = int(base.out_features)
            self.lora_A = nn.Linear(in_features, rank, bias=False)
            self.lora_B = nn.Linear(rank, out_features, bias=False)
            nn.init.kaiming_uniform_(self.lora_A.weight)
            nn.init.zeros_(self.lora_B.weight)
            self.scale = float(alpha) / float(rank)
            self.drop = nn.Dropout(dropout) if dropout else None

        def forward(self, x: Any) -> Any:
            adapted = self.lora_A(x)
            if self.drop is not None:
                adapted = self.drop(adapted)
            return self.base(x) + self.scale * self.lora_B(adapted)

    return LoRALinear


def attach_lora(model: Any, *, rank: int, alpha: int, dropout: float = 0.0) -> None:
    """Freeze every base parameter and wrap ``qkv`` and ``out_proj``."""
    logger.info(
        "Attaching personal LoRA",
        extra={"rank": rank, "alpha": alpha, "dropout": dropout, "freeze_base": True},
    )
    linear = _lora_linear_cls()
    wrapped = 0
    for block in model.blocks:
        attention = block.attn
        attention.qkv = linear(attention.qkv, rank=rank, alpha=alpha, dropout=dropout)
        attention.out_proj = linear(attention.out_proj, rank=rank, alpha=alpha, dropout=dropout)
        wrapped += 2
    for name, parameter in model.named_parameters():
        if "lora_A" not in name and "lora_B" not in name:
            parameter.requires_grad = False
    logger.info(
        "Personal LoRA attached",
        extra={"wrapped_modules": wrapped, "trainable": sum(1 for p in model.parameters() if p.requires_grad)},
    )


def lora_state_dict(model: Any) -> dict[str, Any]:
    """Return only adapter tensors. Base weights stay out of the file."""
    torch, _ = _require_torch()
    state: dict[str, Any] = {}
    for name, value in model.state_dict().items():
        if "lora_A" in name or "lora_B" in name:
            state[name] = value.detach().cpu()
    logger.debug("Collected LoRA state", extra={"tensor_count": len(state)})
    del torch
    return state


def measure_next_token(
    model: Any,
    token_rows: list[list[int]],
    *,
    device: str = "cpu",
) -> tuple[float | None, float | None]:
    """Report next-token loss and accuracy. Does not step the optimizer or log ids."""
    torch, nn = _require_torch()
    rows = [row for row in token_rows if len(row) >= 2]
    if not rows:
        logger.info("[FIX] Personal LoRA eval skipped", extra={"token_rows": 0})
        return None, None
    limit = int(model.config.max_seq_len)
    model.eval()
    model.to(device)
    total_loss = 0.0
    correct = 0
    compared = 0
    with torch.no_grad():
        for ids in rows:
            clipped = ids[:limit]
            if len(clipped) < 2:
                continue
            batch = torch.tensor([clipped], dtype=torch.long, device=device)
            logits = model(batch)
            loss = nn.functional.cross_entropy(
                logits[:, :-1, :].reshape(-1, logits.size(-1)),
                batch[:, 1:].reshape(-1),
            )
            predicted = logits[:, :-1, :].argmax(dim=-1)
            target = batch[:, 1:]
            correct += int((predicted == target).sum().item())
            compared += int(target.numel())
            total_loss += float(loss.detach().cpu())
    if compared == 0:
        return None, None
    loss_value = total_loss / len(rows)
    accuracy = correct / compared
    logger.info(
        "[FIX] Personal LoRA eval measured",
        extra={
            "loss": round(loss_value, 6),
            "token_accuracy": round(accuracy, 6),
            "token_count": compared,
        },
    )
    return loss_value, accuracy


def run_next_token_steps(
    model: Any,
    token_rows: list[list[int]],
    *,
    max_steps: int,
    device: str = "cpu",
) -> None:
    """Run ``max_steps`` of next-token loss on CPU. Does not log tensor values."""
    torch, nn = _require_torch()
    model.to(device)
    trainable = [parameter for parameter in model.parameters() if parameter.requires_grad]
    if not trainable:
        logger.error("LoRA step refused", extra={"code": "personal_lora_untrainable"})
        raise RuntimeError("personal_lora_untrainable")
    optimizer = torch.optim.Adam(trainable, lr=1e-3)
    rows = [row for row in token_rows if len(row) >= 2]
    if not rows:
        rows = [[1, 2, 3]]
    limit = int(model.config.max_seq_len)
    model.train()
    for step in range(1, max_steps + 1):
        ids = rows[(step - 1) % len(rows)][:limit]
        if len(ids) < 2:
            ids = [1, 2]
        batch = torch.tensor([ids], dtype=torch.long, device=device)
        optimizer.zero_grad(set_to_none=True)
        logits = model(batch)
        loss = nn.functional.cross_entropy(
            logits[:, :-1, :].reshape(-1, logits.size(-1)),
            batch[:, 1:].reshape(-1),
        )
        loss.backward()
        optimizer.step()
        logger.info(
            "Personal LoRA step finished",
            extra={"step": step, "max_steps": max_steps, "engine": "torch"},
        )
    model.eval()
