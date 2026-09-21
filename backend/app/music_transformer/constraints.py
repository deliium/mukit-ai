"""Constrained decoding hooks for symbolic token generation.

Constraints filter logits before sampling. They must never invent pitch
tokens to “fix” bars — only ban illegal / unwanted next ids.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

from app.music_transformer.errors import MusicTransformerDependencyError
from app.tokenizer import special_tokens as st
from app.tokenizer.vocab import Vocab


logger = logging.getLogger(__name__)


def _require_torch():
    try:
        import torch
    except ImportError as exc:
        raise MusicTransformerDependencyError(
            "torch_unavailable",
            "PyTorch is required for decoding constraints",
        ) from exc
    return torch


@runtime_checkable
class DecodingConstraint(Protocol):
    name: str

    def filter_logits(self, prefix_ids: list[int], logits: Any, vocab: Vocab) -> Any:
        ...


class BanPadInContent:
    """PAD (id 0) is not sampleable during free generation."""

    name = "ban_pad"

    def filter_logits(self, prefix_ids: list[int], logits: Any, vocab: Vocab) -> Any:
        del prefix_ids
        pad_id = vocab.token_to_id.get(st.PAD, 0)
        out = logits.clone()
        out[..., pad_id] = float("-inf")
        return out


class RequireBosPrefix:
    """No-op filter; used as a prompt guard (checked before generate)."""

    name = "require_bos"

    def filter_logits(self, prefix_ids: list[int], logits: Any, vocab: Vocab) -> Any:
        del prefix_ids, vocab
        return logits


class FamilyTransitionHook:
    """Heuristic: ban VEL_* / DUR_* when the previous content token is not PITCH_*.

    Limitations: does not enforce full REMI grammar; only reduces orphan
    velocity/duration samples. Never inserts pitch tokens.
    """

    name = "family_transition"

    def filter_logits(self, prefix_ids: list[int], logits: Any, vocab: Vocab) -> Any:
        if not prefix_ids:
            return logits
        last = vocab.id_to_token.get(prefix_ids[-1], "")
        # After PITCH, VEL and DUR are allowed; otherwise ban them.
        if last.startswith(st.PITCH_PREFIX):
            return logits
        out = logits.clone()
        for token, tid in vocab.token_to_id.items():
            if token.startswith(st.VEL_PREFIX) or token.startswith(st.DUR_PREFIX):
                out[..., tid] = float("-inf")
        return out


def default_constraints(
    *,
    ban_pad: bool = True,
    family_transition_hook: bool = True,
) -> list[DecodingConstraint]:
    hooks: list[DecodingConstraint] = []
    if ban_pad:
        hooks.append(BanPadInContent())
    if family_transition_hook:
        hooks.append(FamilyTransitionHook())
    logger.debug(
        "Decoding constraints active",
        extra={"names": [h.name for h in hooks]},
    )
    return hooks


def apply_constraints(
    prefix_ids: list[int],
    logits: Any,
    vocab: Vocab,
    constraints: list[DecodingConstraint],
) -> Any:
    out = logits
    for constraint in constraints:
        out = constraint.filter_logits(prefix_ids, out, vocab)
    return out


def ensure_bos_prefix(prefix_ids: list[int], vocab: Vocab) -> list[int]:
    bos_id = vocab.token_to_id[st.BOS]
    if not prefix_ids:
        return [bos_id]
    if prefix_ids[0] != bos_id:
        return [bos_id, *prefix_ids]
    return list(prefix_ids)
