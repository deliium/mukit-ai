"""Inference adapter: checkpoint → tokens → repair/decode → Composition V2."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.composition_schemas import CompositionV2
from app.music_transformer.checkpoint import load_checkpoint
from app.music_transformer.constraints import default_constraints
from app.music_transformer.device import resolve_device
from app.music_transformer.errors import MusicTransformerGenerateError
from app.music_transformer.generate import generate_ids
from app.music_transformer.model import MusicTransformerLM
from app.music_transformer.reproducibility import seed_everything
from app.music_transformer.schemas import (
    MusicTransformerGenerateReportV1,
    MusicTransformerSampleConfigV1,
)
from app.music_transformer.settings import load_music_transformer_settings
from app.services.composition_validator import validate_composition_integrity
from app.tokenizer.decode import decode_tokens
from app.tokenizer.schemas import (
    TokenizerConditioningV1,
    TokenizerConfigV1,
    default_tokenizer_config,
)
from app.tokenizer.vocab import build_vocab
from app.music_transformer.data import prompt_ids_from_composition
from app.tokenizer.conditioning import emit_conditioning_tokens
from app.tokenizer import special_tokens as st


logger = logging.getLogger(__name__)


def generate_composition(
    checkpoint: Path | str,
    *,
    conditioning: TokenizerConditioningV1 | dict[str, Any] | None = None,
    prefix_composition: CompositionV2 | dict[str, Any] | None = None,
    sample_config: MusicTransformerSampleConfigV1 | None = None,
    require_tokenizer_version: bool = True,
    tokenizer_config: TokenizerConfigV1 | None = None,
    device: str | None = None,
    seed: int | None = None,
) -> tuple[CompositionV2, MusicTransformerGenerateReportV1]:
    """Load checkpoint, generate ids, repair/decode, validate Composition V2."""
    settings = load_music_transformer_settings()
    if seed is not None:
        seed_everything(seed)
    elif sample_config and sample_config.greedy:
        seed_everything(settings.seed)

    tok_cfg = tokenizer_config or default_tokenizer_config()
    vocab = build_vocab(tok_cfg)
    sample_cfg = sample_config or MusicTransformerSampleConfigV1(greedy=True, max_new_tokens=64)
    resolved_device = resolve_device(device, settings=settings)

    payload, card = load_checkpoint(
        checkpoint,
        map_location=resolved_device,
        require_tokenizer_version=require_tokenizer_version,
        tokenizer_config=tok_cfg,
    )
    arch = card.architecture
    model = MusicTransformerLM(arch)
    model.load_state_dict(payload["model_state_dict"])
    model.to(resolved_device)
    model.eval()
    return sample_and_decode(
        model,
        conditioning=conditioning,
        prefix_composition=prefix_composition,
        sample_config=sample_cfg,
        device=resolved_device,
        tokenizer_config=tok_cfg,
        tokenizer_version=card.tokenizer.expected_tokenizer_version,
        vocab_hash_prefix=card.tokenizer.vocab_hash[:12],
    )


def sample_and_decode(
    model,
    *,
    conditioning: TokenizerConditioningV1 | dict[str, Any] | None = None,
    prefix_composition: CompositionV2 | dict[str, Any] | None = None,
    sample_config: MusicTransformerSampleConfigV1 | None = None,
    device: str = "cpu",
    tokenizer_config: TokenizerConfigV1 | None = None,
    tokenizer_version: str,
    vocab_hash_prefix: str,
) -> tuple[CompositionV2, MusicTransformerGenerateReportV1]:
    """Sample token ids from an already loaded model and decode Composition V2."""
    tok_cfg = tokenizer_config or default_tokenizer_config()
    vocab = build_vocab(tok_cfg)
    sample_cfg = sample_config or MusicTransformerSampleConfigV1(greedy=True, max_new_tokens=64)
    prompt = _build_prompt(
        vocab=vocab,
        tok_cfg=tok_cfg,
        conditioning=conditioning,
        prefix_composition=prefix_composition,
    )
    constraints = default_constraints(
        ban_pad=sample_cfg.ban_pad,
        family_transition_hook=sample_cfg.family_transition_hook,
    )
    full_ids, stop = generate_ids(
        model,
        prompt,
        vocab,
        sample_cfg,
        device=device,
        constraints=constraints,
    )

    composition, decode_report = decode_tokens(
        full_ids,
        tok_cfg,
        vocab=vocab,
        on_invalid=sample_cfg.on_invalid,
    )
    issue_codes: list[str] = []
    status = "ok"
    if decode_report.result == "rejected":
        status = "rejected"
        issue_codes.append("decode_rejected")
        logger.error(
            "Generate decode rejected",
            extra={"issue_codes": list(decode_report.issue_codes)[:16]},
        )
        raise MusicTransformerGenerateError(
            "decode_rejected",
            "Tokenizer decode rejected generated sequence",
            details={"issue_codes": list(decode_report.issue_codes)[:32]},
        )
    if decode_report.result == "repaired":
        status = "repaired"

    integrity = validate_composition_integrity(composition, profile="canonical")
    if not integrity.ok:
        logger.error(
            "Generate integrity failed",
            extra={"error_codes": integrity.error_codes()[:16]},
        )
        raise MusicTransformerGenerateError(
            "integrity_failed",
            "Decoded composition failed integrity validation",
            details={"error_codes": integrity.error_codes()[:32]},
        )

    notes_out = sum(len(t.events) for t in composition.tracks)
    cond_map: dict[str, Any] = {}
    if isinstance(conditioning, TokenizerConditioningV1):
        cond_map = conditioning.model_dump(exclude_none=True)
    elif isinstance(conditioning, dict):
        cond_map = {k: v for k, v in conditioning.items() if v is not None}

    report = MusicTransformerGenerateReportV1(
        status=status,  # type: ignore[arg-type]
        stop_reason="eos" if stop == "eos" else "max_new_tokens",
        issue_codes=issue_codes,  # type: ignore[arg-type]
        prompt_tokens=len(prompt),
        generated_tokens=max(0, len(full_ids) - len(prompt)),
        notes_out=notes_out,
        bar_count=composition.bar_count,
        tokenizer_version=tokenizer_version,
        vocab_hash_prefix=vocab_hash_prefix,
        repair_result=decode_report.result,
        conditioning=cond_map,
    )
    logger.info(
        "generate_composition finished",
        extra={
            "status": report.status,
            "notes_out": notes_out,
            "bar_count": composition.bar_count,
            "tokenizer_version": report.tokenizer_version,
            "vocab_hash_prefix": report.vocab_hash_prefix,
            "repair_result": report.repair_result,
            "stop_reason": report.stop_reason,
        },
    )
    logger.debug(
        "generate_composition token prefix",
        extra={"token_id_prefix": full_ids[:64]},
    )
    return composition, report


def _build_prompt(
    *,
    vocab,
    tok_cfg: TokenizerConfigV1,
    conditioning: TokenizerConditioningV1 | dict[str, Any] | None,
    prefix_composition: CompositionV2 | dict[str, Any] | None,
) -> list[int]:
    if prefix_composition is not None:
        return prompt_ids_from_composition(
            prefix_composition,
            config=tok_cfg,
            conditioning=conditioning,
            vocab=vocab,
            drop_eos=True,
        )
    # Minimal prompt: BOS + optional conditioning tokens
    bos = vocab.token_to_id[st.BOS]
    ids = [bos]
    if conditioning is not None and tok_cfg.conditioning_enabled:
        from app.tokenizer.conditioning import conditioning_from_mapping

        cond = (
            conditioning
            if isinstance(conditioning, TokenizerConditioningV1)
            else conditioning_from_mapping(conditioning)
        )
        tokens, _unk = emit_conditioning_tokens(cond, tok_cfg, vocab)
        for token in tokens:
            tid = vocab.token_to_id.get(token)
            if tid is not None:
                ids.append(tid)
    return ids
