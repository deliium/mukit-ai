"""Service wrapper for Music Transformer generate (may import adapter; not torch in router)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.music_transformer.errors import (
    MusicTransformerCheckpointError,
    MusicTransformerDependencyError,
    MusicTransformerError,
    MusicTransformerGenerateError,
)
from app.music_transformer.schemas import MusicTransformerSampleConfigV1
from app.music_transformer.settings import load_music_transformer_settings
from app.music_transformer_schemas import (
    MusicTransformerGenerateRequest,
    MusicTransformerGenerateResponse,
)


logger = logging.getLogger(__name__)


def generate_via_music_transformer(
    request: MusicTransformerGenerateRequest,
) -> MusicTransformerGenerateResponse:
    """Call inference adapter; raises MusicTransformerError subclasses."""
    settings = load_music_transformer_settings()
    ckpt = request.checkpoint or settings.default_checkpoint
    if not ckpt:
        raise MusicTransformerCheckpointError(
            "checkpoint_missing",
            "No checkpoint configured (set MUSIC_TRANSFORMER_CHECKPOINT or request.checkpoint)",
        )
    from app.music_transformer.inference import generate_composition

    sample = MusicTransformerSampleConfigV1(
        temperature=request.temperature,
        top_k=request.top_k,
        top_p=request.top_p,
        greedy=request.greedy,
        max_new_tokens=request.max_new_tokens,
    )
    composition, report = generate_composition(
        Path(ckpt),
        conditioning=request.conditioning,
        prefix_composition=request.prefix_composition,
        sample_config=sample,
        seed=request.seed,
    )
    logger.info(
        "Music Transformer API generate ok",
        extra={
            "status": report.status,
            "notes_out": report.notes_out,
            "checkpoint_basename": Path(ckpt).name,
        },
    )
    return MusicTransformerGenerateResponse(
        composition=composition.model_dump(mode="json"),
        status=report.status,
        stop_reason=report.stop_reason,
        notes_out=report.notes_out,
        bar_count=report.bar_count,
        tokenizer_version=report.tokenizer_version,
        vocab_hash_prefix=report.vocab_hash_prefix,
        repair_result=report.repair_result,
    )


def music_transformer_readiness_block() -> dict[str, Any]:
    """Soft readiness block (non-blocking for /ready)."""
    settings = load_music_transformer_settings()
    torch_ok = False
    try:
        import torch  # noqa: F401

        torch_ok = True
    except ImportError:
        torch_ok = False
    ckpt = settings.default_checkpoint
    ckpt_ok = bool(ckpt and Path(ckpt).is_file())
    block = {
        "enabled": settings.api_enabled,
        "torch_available": torch_ok,
        "checkpoint_configured": ckpt is not None,
        "checkpoint_present": ckpt_ok,
        "device": settings.device,
        "ready": bool(settings.api_enabled and torch_ok and ckpt_ok),
    }
    logger.debug("Music Transformer readiness block", extra=block)
    return block
