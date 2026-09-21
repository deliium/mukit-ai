"""Decoder-only Music Transformer LM (lazy torch)."""

from __future__ import annotations

import logging
import math
from typing import Any

from app.music_transformer.errors import MusicTransformerDependencyError
from app.music_transformer.schemas import MusicTransformerConfigV1, tiny_test_config


logger = logging.getLogger(__name__)


def _require_torch():
    try:
        import torch
        import torch.nn as nn
    except ImportError as exc:
        raise MusicTransformerDependencyError(
            "torch_unavailable",
            "PyTorch is required for MusicTransformerLM",
            details={"hint": "pip install -r backend/requirements-music-transformer.txt"},
        ) from exc
    return torch, nn


def build_model_classes():
    torch, nn = _require_torch()
    from app.music_transformer.blocks import build_blocks_module

    TransformerBlock = build_blocks_module()

    class MusicTransformerLM(nn.Module):
        """GPT-style pre-norm causal LM over tokenizer.v1 token ids."""

        def __init__(self, config: MusicTransformerConfigV1) -> None:
            super().__init__()
            self.config = config
            d_ff = config.resolved_d_ff()
            self.tok_emb = nn.Embedding(config.vocab_size, config.d_model, padding_idx=config.pad_id)
            if config.pos_encoding == "learned":
                self.pos_emb = nn.Embedding(config.max_seq_len, config.d_model)
                self.register_buffer("_sin_pos", None, persistent=False)
            else:
                self.pos_emb = None
                pe = _sinusoidal_positions(config.max_seq_len, config.d_model)
                self.register_buffer("_sin_pos", pe, persistent=False)
            self.drop = nn.Dropout(config.dropout)
            self.blocks = nn.ModuleList(
                [
                    TransformerBlock(config.d_model, config.n_heads, d_ff, config.dropout)
                    for _ in range(config.n_layers)
                ]
            )
            self.ln_f = nn.LayerNorm(config.d_model)
            self.lm_head = nn.Linear(config.d_model, config.vocab_size, bias=False)
            if config.tie_embeddings:
                self.lm_head.weight = self.tok_emb.weight
            self.apply(self._init_weights)
            n_params = sum(p.numel() for p in self.parameters())
            logger.info(
                "MusicTransformerLM initialized",
                extra={
                    "n_layers": config.n_layers,
                    "d_model": config.d_model,
                    "n_heads": config.n_heads,
                    "vocab_size": config.vocab_size,
                    "max_seq_len": config.max_seq_len,
                    "param_count": n_params,
                    "tie_embeddings": config.tie_embeddings,
                    "pos_encoding": config.pos_encoding,
                },
            )

        @staticmethod
        def _init_weights(module: nn.Module) -> None:
            if isinstance(module, nn.Linear):
                nn.init.normal_(module.weight, mean=0.0, std=0.02)
                if module.bias is not None:
                    nn.init.zeros_(module.bias)
            elif isinstance(module, nn.Embedding):
                nn.init.normal_(module.weight, mean=0.0, std=0.02)

        def forward(
            self,
            input_ids: Any,
            attention_mask: Any | None = None,
        ) -> Any:
            del attention_mask  # causal mask is internal; pad handled via ignore_index in loss
            torch_mod, _ = _require_torch()
            bsz, seq_len = input_ids.shape
            if seq_len > self.config.max_seq_len:
                raise ValueError(
                    f"sequence length {seq_len} exceeds max_seq_len {self.config.max_seq_len}"
                )
            tok = self.tok_emb(input_ids)
            if self.pos_emb is not None:
                positions = torch_mod.arange(seq_len, device=input_ids.device)
                pos = self.pos_emb(positions)[None, :, :]
            else:
                assert self._sin_pos is not None
                pos = self._sin_pos[:seq_len].unsqueeze(0).to(dtype=tok.dtype, device=tok.device)
            x = self.drop(tok + pos)
            for block in self.blocks:
                x = block(x)
            x = self.ln_f(x)
            logits = self.lm_head(x)
            logger.debug(
                "MusicTransformerLM forward",
                extra={"batch": bsz, "seq_len": seq_len, "vocab": logits.shape[-1]},
            )
            return logits

    return MusicTransformerLM


_MusicTransformerLM = None


def MusicTransformerLM(config: MusicTransformerConfigV1):
    global _MusicTransformerLM
    if _MusicTransformerLM is None:
        _MusicTransformerLM = build_model_classes()
    return _MusicTransformerLM(config)


def create_tiny_model(*, vocab_size: int):
    """Factory for CPU tests / fixtures."""
    return MusicTransformerLM(tiny_test_config(vocab_size=vocab_size))


def _sinusoidal_positions(max_len: int, d_model: int):
    torch, _ = _require_torch()
    pe = torch.zeros(max_len, d_model)
    position = torch.arange(0, max_len, dtype=torch.float).unsqueeze(1)
    div_term = torch.exp(torch.arange(0, d_model, 2).float() * (-math.log(10000.0) / d_model))
    pe[:, 0::2] = torch.sin(position * div_term)
    pe[:, 1::2] = torch.cos(position * div_term)
    return pe


def count_parameters(model: Any) -> int:
    return int(sum(p.numel() for p in model.parameters()))
