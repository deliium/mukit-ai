"""Mixed-precision helpers (AMP only on CUDA/ROCm; never silent AMP on CPU)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.music_transformer.errors import MusicTransformerTrainError
from app.music_transformer.schemas import PrecisionKind


logger = logging.getLogger(__name__)


@dataclass
class PrecisionContext:
    precision: PrecisionKind
    use_amp: bool
    amp_dtype: Any | None
    scaler: Any | None
    device_type: str


def build_precision_context(
    precision: PrecisionKind,
    *,
    device: str,
    precision_fallback: str = "",
    torch_module: Any | None = None,
) -> PrecisionContext:
    torch = torch_module
    if torch is None:
        import torch as torch_mod

        torch = torch_mod

    device_type = "cuda" if str(device).startswith("cuda") else (
        "mps" if str(device).startswith("mps") else "cpu"
    )
    if precision == "fp32":
        return PrecisionContext(
            precision="fp32",
            use_amp=False,
            amp_dtype=None,
            scaler=None,
            device_type=device_type,
        )

    if device_type != "cuda":
        if precision_fallback == "fp32":
            logger.warning(
                "AMP requested on non-CUDA device; falling back to fp32",
                extra={"precision": precision, "device": device},
            )
            return PrecisionContext(
                precision="fp32",
                use_amp=False,
                amp_dtype=None,
                scaler=None,
                device_type=device_type,
            )
        raise MusicTransformerTrainError(
            "precision_unsupported",
            f"precision={precision} requires CUDA/ROCm; got device={device}",
            details={"precision": precision, "device": device},
        )

    if precision == "amp_fp16":
        scaler = torch.amp.GradScaler("cuda")
        logger.info(
            "AMP fp16 enabled",
            extra={"device": device, "precision": precision},
        )
        return PrecisionContext(
            precision=precision,
            use_amp=True,
            amp_dtype=torch.float16,
            scaler=scaler,
            device_type="cuda",
        )

    # amp_bf16
    bf16_ok = False
    try:
        bf16_ok = bool(torch.cuda.is_bf16_supported())
    except Exception:  # noqa: BLE001
        bf16_ok = False
    if not bf16_ok:
        if precision_fallback == "fp32":
            logger.warning(
                "bf16 unsupported; falling back to fp32",
                extra={"precision": precision, "device": device},
            )
            return PrecisionContext(
                precision="fp32",
                use_amp=False,
                amp_dtype=None,
                scaler=None,
                device_type="cuda",
            )
        raise MusicTransformerTrainError(
            "precision_unsupported",
            "amp_bf16 requested but torch.cuda.is_bf16_supported() is false",
            details={"precision": precision, "device": device},
        )
    logger.info(
        "AMP bf16 enabled",
        extra={"device": device, "precision": precision},
    )
    return PrecisionContext(
        precision=precision,
        use_amp=True,
        amp_dtype=torch.bfloat16,
        scaler=None,  # bf16 typically without GradScaler
        device_type="cuda",
    )
