"""Device resolution for MUSIC_TRANSFORMER_DEVICE (cpu / cuda / mps / rocm)."""

from __future__ import annotations

import logging

from app.music_transformer.errors import MusicTransformerDependencyError
from app.music_transformer.settings import MusicTransformerSettings, load_music_transformer_settings


logger = logging.getLogger(__name__)


def _require_torch():
    try:
        import torch
    except ImportError as exc:
        raise MusicTransformerDependencyError(
            "torch_unavailable",
            "PyTorch is required for device resolution",
        ) from exc
    return torch


def resolve_device(
    requested: str | None = None,
    *,
    settings: MusicTransformerSettings | None = None,
) -> str:
    """Return a torch device string. Maps ``rocm``/``hip`` → ``cuda``."""
    torch = _require_torch()
    cfg = settings or load_music_transformer_settings()
    raw = (requested or cfg.device).strip().lower()
    if raw in {"rocm", "hip"}:
        mapped = "cuda"
        logger.info(
            "Mapping ROCm/HIP device request to torch cuda API",
            extra={"requested": raw, "mapped": mapped},
        )
        raw = mapped
    if raw.startswith("cuda"):
        if not torch.cuda.is_available():
            if cfg.device_fallback == "cpu":
                logger.warning(
                    "CUDA/ROCm unavailable; falling back to cpu",
                    extra={"requested": raw},
                )
                return "cpu"
            raise MusicTransformerDependencyError(
                "device_unavailable",
                f"Requested device {raw!r} is unavailable and no fallback configured",
                details={"requested": raw, "hint": "Set MUSIC_TRANSFORMER_DEVICE_FALLBACK=cpu"},
            )
        return raw if raw != "cuda" else "cuda"
    if raw == "mps":
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps"
        if cfg.device_fallback == "cpu":
            logger.warning("MPS unavailable; falling back to cpu")
            return "cpu"
        raise MusicTransformerDependencyError(
            "device_unavailable",
            "MPS device unavailable",
            details={"requested": raw},
        )
    if raw != "cpu":
        logger.warning("Unknown device string; using cpu", extra={"requested": raw})
    return "cpu"
