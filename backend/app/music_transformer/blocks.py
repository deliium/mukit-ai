"""Pre-norm Transformer decoder blocks (lazy torch)."""

from __future__ import annotations

import logging
from typing import Any


logger = logging.getLogger(__name__)


def _torch():
    import torch
    import torch.nn as nn
    import torch.nn.functional as F

    return torch, nn, F


def build_blocks_module():
    torch, nn, F = _torch()

    class MultiHeadCausalAttention(nn.Module):
        def __init__(self, d_model: int, n_heads: int, dropout: float) -> None:
            super().__init__()
            assert d_model % n_heads == 0
            self.n_heads = n_heads
            self.head_dim = d_model // n_heads
            self.qkv = nn.Linear(d_model, 3 * d_model, bias=False)
            self.out_proj = nn.Linear(d_model, d_model, bias=False)
            self.dropout = dropout

        def forward(self, x: Any, *, attn_mask: Any | None = None) -> Any:
            # x: [B, T, C]
            bsz, seq_len, _ = x.shape
            qkv = self.qkv(x)
            q, k, v = qkv.chunk(3, dim=-1)
            q = q.view(bsz, seq_len, self.n_heads, self.head_dim).transpose(1, 2)
            k = k.view(bsz, seq_len, self.n_heads, self.head_dim).transpose(1, 2)
            v = v.view(bsz, seq_len, self.n_heads, self.head_dim).transpose(1, 2)
            # scaled_dot_product_attention with is_causal when no custom mask
            if attn_mask is None:
                y = F.scaled_dot_product_attention(
                    q,
                    k,
                    v,
                    attn_mask=None,
                    dropout_p=self.dropout if self.training else 0.0,
                    is_causal=True,
                )
            else:
                y = F.scaled_dot_product_attention(
                    q,
                    k,
                    v,
                    attn_mask=attn_mask,
                    dropout_p=self.dropout if self.training else 0.0,
                    is_causal=False,
                )
            y = y.transpose(1, 2).contiguous().view(bsz, seq_len, -1)
            return self.out_proj(y)

    class FeedForward(nn.Module):
        def __init__(self, d_model: int, d_ff: int, dropout: float) -> None:
            super().__init__()
            self.net = nn.Sequential(
                nn.Linear(d_model, d_ff),
                nn.GELU(),
                nn.Dropout(dropout),
                nn.Linear(d_ff, d_model),
                nn.Dropout(dropout),
            )

        def forward(self, x: Any) -> Any:
            return self.net(x)

    class TransformerBlock(nn.Module):
        def __init__(self, d_model: int, n_heads: int, d_ff: int, dropout: float) -> None:
            super().__init__()
            self.ln1 = nn.LayerNorm(d_model)
            self.attn = MultiHeadCausalAttention(d_model, n_heads, dropout)
            self.ln2 = nn.LayerNorm(d_model)
            self.mlp = FeedForward(d_model, d_ff, dropout)
            self.resid_dropout = nn.Dropout(dropout)

        def forward(self, x: Any) -> Any:
            x = x + self.resid_dropout(self.attn(self.ln1(x)))
            x = x + self.resid_dropout(self.mlp(self.ln2(x)))
            return x

    return TransformerBlock


# Cache constructed class after first torch import.
_TransformerBlock = None


def TransformerBlock(*args, **kwargs):
    global _TransformerBlock
    if _TransformerBlock is None:
        _TransformerBlock = build_blocks_module()
    return _TransformerBlock(*args, **kwargs)
