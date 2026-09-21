"""Service wrapper for Music Transformer generate (may import adapter; not torch in router)."""

from __future__ import annotations

import logging
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
    from app.services.model_path_resolve import (
        MODEL_PATH_REJECTED,
        ModelPathRejectedError,
        resolve_music_transformer_checkpoint,
    )

    try:
        checkpoint_path = resolve_music_transformer_checkpoint(ckpt)
    except ModelPathRejectedError as exc:
        raise MusicTransformerCheckpointError(
            MODEL_PATH_REJECTED,
            str(exc),
        ) from exc
    from app.music_transformer.inference import generate_composition

    sample = MusicTransformerSampleConfigV1(
        temperature=request.temperature,
        top_k=request.top_k,
        top_p=request.top_p,
        greedy=request.greedy,
        max_new_tokens=request.max_new_tokens,
    )
    composition, report = generate_composition(
        checkpoint_path,
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
            "checkpoint_basename": checkpoint_path.name,
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
    ckpt_ok = False
    ckpt_rejected = False
    checkpoint_status = "not_configured"
    if ckpt:
        from app.services.model_path_resolve import (
            ModelPathRejectedError,
            resolve_music_transformer_checkpoint,
        )

        try:
            resolved = resolve_music_transformer_checkpoint(ckpt)
            ckpt_ok = resolved.is_file()
            checkpoint_status = "present" if ckpt_ok else "not_installed"
        except ModelPathRejectedError:
            ckpt_rejected = True
            ckpt_ok = False
            checkpoint_status = "path_rejected"
    block = {
        "enabled": settings.api_enabled,
        "torch_available": torch_ok,
        "checkpoint_configured": ckpt is not None,
        "checkpoint_present": ckpt_ok,
        "checkpoint_path_rejected": ckpt_rejected,
        # Soft operator hint — never auto-download weights from /ready or lifespan.
        "checkpoint_status": checkpoint_status,
        "install_hint": (
            "checkpoint not installed — mount weights under MUSIC_TRANSFORMER_CHECKPOINT_DIR "
            "or models/; no auto-download on compose up"
            if checkpoint_status in {"not_installed", "not_configured", "path_rejected"}
            else None
        ),
        "device": settings.device,
        "ready": bool(settings.api_enabled and torch_ok and ckpt_ok and not ckpt_rejected),
    }
    logger.debug("Music Transformer readiness block", extra=block)
    return block
