"""Token sampling: temperature → top-k → top-p → multinomial (or greedy)."""

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
            "PyTorch is required for sampling",
        ) from exc
    return torch, F


def sample_logits(
    logits: Any,
    *,
    temperature: float = 1.0,
    top_k: int | None = None,
    top_p: float | None = None,
    greedy: bool = False,
) -> Any:
    """Sample a single token id from last-step logits ``[..., V]``.

    Order: temperature scale → top-k filter → top-p filter → multinomial.
    ``greedy`` or ``temperature <= 0`` → argmax (deterministic).
    """
    torch, F = _require_torch()
    if logits.dim() == 1:
        step = logits
    else:
        step = logits[..., -1, :] if logits.dim() == 3 else logits

    use_greedy = greedy or temperature <= 0
    if use_greedy:
        token = step.argmax(dim=-1)
        logger.debug("sample_logits greedy", extra={"token_id": int(token.reshape(-1)[0].item())})
        return token

    temp = max(float(temperature), 1e-5)
    scaled = step / temp

    if top_k is not None and top_k > 0:
        k = min(int(top_k), scaled.size(-1))
        values, _ = torch.topk(scaled, k)
        cutoff = values[..., -1, None]
        scaled = scaled.masked_fill(scaled < cutoff, float("-inf"))

    if top_p is not None and 0.0 < top_p < 1.0:
        sorted_logits, sorted_idx = torch.sort(scaled, descending=True)
        probs = F.softmax(sorted_logits, dim=-1)
        cumulative = torch.cumsum(probs, dim=-1)
        mask = cumulative > top_p
        # keep at least one token
        mask[..., 1:] = mask[..., :-1].clone()
        mask[..., 0] = False
        sorted_logits = sorted_logits.masked_fill(mask, float("-inf"))
        # scatter back
        scaled = torch.full_like(scaled, float("-inf")).scatter(-1, sorted_idx, sorted_logits)

    probs = F.softmax(scaled, dim=-1)
    token = torch.multinomial(probs, num_samples=1).squeeze(-1)
    logger.debug(
        "sample_logits multinomial",
        extra={
            "temperature": temp,
            "top_k": top_k,
            "top_p": top_p,
            "token_id": int(token.reshape(-1)[0].item()),
        },
    )
    return token
