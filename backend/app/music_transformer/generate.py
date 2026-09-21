"""Autoregressive id generation with sampling + constraints."""

from __future__ import annotations

import logging
from typing import Any, Literal

from app.music_transformer.constraints import (
    DecodingConstraint,
    apply_constraints,
    default_constraints,
    ensure_bos_prefix,
)
from app.music_transformer.errors import MusicTransformerDependencyError, MusicTransformerGenerateError
from app.music_transformer.sampling import sample_logits
from app.music_transformer.schemas import MusicTransformerSampleConfigV1
from app.tokenizer import special_tokens as st
from app.tokenizer.vocab import Vocab


logger = logging.getLogger(__name__)

StopReason = Literal["eos", "max_new_tokens"]


def _require_torch():
    try:
        import torch
    except ImportError as exc:
        raise MusicTransformerDependencyError(
            "torch_unavailable",
            "PyTorch is required for generate_ids",
        ) from exc
    return torch


def generate_ids(
    model: Any,
    prompt_ids: list[int],
    vocab: Vocab,
    sample_config: MusicTransformerSampleConfigV1,
    *,
    device: str = "cpu",
    constraints: list[DecodingConstraint] | None = None,
) -> tuple[list[int], StopReason]:
    """Continue from ``prompt_ids`` until EOS or max_new_tokens."""
    torch = _require_torch()
    if sample_config.require_bos:
        prompt_ids = ensure_bos_prefix(prompt_ids, vocab)
    if not prompt_ids:
        raise MusicTransformerGenerateError("empty_prompt", "Prompt token list is empty")

    active = constraints if constraints is not None else default_constraints(
        ban_pad=sample_config.ban_pad,
        family_transition_hook=sample_config.family_transition_hook,
    )
    eos_id = vocab.token_to_id[st.EOS]
    max_ctx = int(getattr(model.config, "max_seq_len", 512))
    ids = list(prompt_ids)
    stop: StopReason = "max_new_tokens"
    masked_steps = 0

    temp = sample_config.temperature
    if temp < 0:
        logger.warning("temperature < 0; clamped to 0 (greedy)", extra={"temperature": temp})
        temp = 0.0

    model.eval()
    with torch.no_grad():
        for step in range(sample_config.max_new_tokens):
            ctx = ids[-max_ctx:]
            x = torch.tensor([ctx], dtype=torch.long, device=device)
            logits = model(x)
            step_logits = logits[0, -1, :].clone()
            before = step_logits.clone()
            step_logits = apply_constraints(ids, step_logits, vocab, active)
            if not torch.equal(before, step_logits):
                masked_steps += 1
            # If all -inf, fall back to UNK
            if torch.isneginf(step_logits).all():
                unk = vocab.token_to_id.get(st.UNK, eos_id)
                next_id = unk
            else:
                next_t = sample_logits(
                    step_logits,
                    temperature=temp,
                    top_k=sample_config.top_k,
                    top_p=sample_config.top_p,
                    greedy=sample_config.greedy or temp <= 0,
                )
                next_id = int(next_t.item())
            ids.append(next_id)
            if next_id == eos_id:
                stop = "eos"
                break

    generated = len(ids) - len(prompt_ids)
    logger.info(
        "generate_ids finished",
        extra={
            "prompt_tokens": len(prompt_ids),
            "generated_tokens": generated,
            "stop_reason": stop,
            "masked_logit_steps": masked_steps,
            "greedy": sample_config.greedy or temp <= 0,
        },
    )
    logger.debug(
        "generate_ids token prefix",
        extra={"token_id_prefix": ids[:64]},
    )
    return ids, stop
