"""Checkpoint save/load with ``music_transformer.checkpoint.v1`` card.

Lazy-imports torch so schemas/settings remain importable without the extra.
Supports resume: optimizer / scheduler / scaler / step / RNG state.
"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.music_transformer.errors import (
    MusicTransformerCheckpointError,
    MusicTransformerDependencyError,
    MusicTransformerResumeError,
)
from app.music_transformer.schemas import (
    MusicTransformerCheckpointCardV1,
    MusicTransformerConfigV1,
    MusicTransformerTrainingCardV1,
)
from app.music_transformer.settings import MUSIC_TRANSFORMER_VERSION
from app.music_transformer.version_info import capture_git_commit, capture_project_version
from app.tokenizer.schemas import TokenizerConfigV1, TokenizerModelExpectationV1, default_tokenizer_config
from app.tokenizer.versioning import expected_tokenizer_record, verify_expectation
from app.tokenizer.vocab import build_vocab


logger = logging.getLogger(__name__)


def _require_torch():
    try:
        import torch
    except ImportError as exc:
        raise MusicTransformerDependencyError(
            "torch_unavailable",
            "PyTorch is required for checkpoint I/O; install "
            "backend/requirements-music-transformer.txt",
            details={"hint": "pip install -r backend/requirements-music-transformer.txt"},
        ) from exc
    return torch


@dataclass
class ResumeState:
    """Restored training state from a checkpoint."""

    payload: dict[str, Any]
    card: MusicTransformerCheckpointCardV1
    global_step: int
    epoch: int
    experiment_id: str | None
    has_optimizer: bool
    has_scheduler: bool
    has_scaler: bool


def build_checkpoint_card(
    architecture: MusicTransformerConfigV1,
    *,
    training: MusicTransformerTrainingCardV1,
    tokenizer_config: TokenizerConfigV1 | None = None,
    dataset_version_id: str | None = None,
    dataset_name: str | None = None,
    created_at: datetime | None = None,
) -> MusicTransformerCheckpointCardV1:
    tok_cfg = tokenizer_config or default_tokenizer_config()
    vocab = build_vocab(tok_cfg)
    expectation = expected_tokenizer_record(tok_cfg, vocab)
    if architecture.vocab_size != vocab.size:
        logger.warning(
            "Architecture vocab_size differs from tokenizer vocab; card uses architecture value",
            extra={
                "architecture_vocab_size": architecture.vocab_size,
                "tokenizer_vocab_size": vocab.size,
            },
        )
    card = MusicTransformerCheckpointCardV1(
        package_version=MUSIC_TRANSFORMER_VERSION,
        architecture=architecture,
        architecture_digest=architecture.config_digest(),
        tokenizer=expectation,
        dataset_version_id=dataset_version_id,
        dataset_name=dataset_name,
        training=training,
        created_at=created_at or datetime.now(timezone.utc),
        tokenizer_profile=tok_cfg.profile,
    )
    logger.info(
        "Checkpoint card built",
        extra={
            "architecture_digest_prefix": card.architecture_digest[:12],
            "tokenizer_version": card.tokenizer.expected_tokenizer_version,
            "vocab_hash_prefix": card.tokenizer.vocab_hash[:12],
            "dataset_version_id_prefix": (dataset_version_id or "")[:12] or None,
            "experiment_id": training.experiment_id,
            "global_step": training.global_step,
        },
    )
    return card


def save_checkpoint(
    path: Path | str,
    model: Any,
    card: MusicTransformerCheckpointCardV1,
    *,
    optimizer: Any | None = None,
    scheduler: Any | None = None,
    scaler: Any | None = None,
    rng_state: dict[str, Any] | None = None,
    write_sidecar: bool = True,
    latest_link: Path | str | None = None,
) -> Path:
    """Write ``.pt`` (state + embedded card) and optional ``.card.json`` sidecar."""
    torch = _require_torch()
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "model_state_dict": model.state_dict(),
        "card": card.model_dump(mode="json"),
        "schema_version": card.schema_version,
        "global_step": card.training.global_step or card.training.steps,
        "epoch": card.training.epoch,
        "experiment_id": card.training.experiment_id,
    }
    if optimizer is not None:
        payload["optimizer_state_dict"] = optimizer.state_dict()
    if scheduler is not None:
        payload["scheduler_state_dict"] = scheduler.state_dict()
    if scaler is not None:
        payload["scaler_state_dict"] = scaler.state_dict()
    if rng_state is None:
        rng_state = capture_rng_state()
    payload["rng_state"] = rng_state
    torch.save(payload, out)
    if write_sidecar:
        sidecar = _sidecar_path(out)
        sidecar.write_text(
            json.dumps(card.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    if latest_link is not None:
        _update_latest(Path(latest_link), out)
    logger.info(
        "Checkpoint saved",
        extra={
            "basename": out.name,
            "architecture_digest_prefix": card.architecture_digest[:12],
            "tokenizer_version": card.tokenizer.expected_tokenizer_version,
            "vocab_hash_prefix": card.tokenizer.vocab_hash[:12],
            "dataset_version_id_prefix": (card.dataset_version_id or "")[:12] or None,
            "experiment_id": card.training.experiment_id,
            "global_step": card.training.global_step,
            "has_optimizer": optimizer is not None,
            "has_scheduler": scheduler is not None,
            "has_scaler": scaler is not None,
        },
    )
    return out


def load_checkpoint(
    path: Path | str,
    *,
    map_location: str | None = "cpu",
    require_tokenizer_version: bool = True,
    tokenizer_config: TokenizerConfigV1 | None = None,
) -> tuple[dict[str, Any], MusicTransformerCheckpointCardV1]:
    """Load ``.pt`` payload and validated card; optionally verify tokenizer expectation."""
    torch = _require_torch()
    ckpt_path = Path(path)
    if not ckpt_path.is_file():
        logger.error("Checkpoint missing", extra={"basename": ckpt_path.name})
        raise MusicTransformerCheckpointError(
            "checkpoint_missing",
            f"Checkpoint not found: {ckpt_path.name}",
            details={"basename": ckpt_path.name},
        )
    try:
        payload = torch.load(ckpt_path, map_location=map_location, weights_only=False)
    except Exception as exc:  # noqa: BLE001 — surface as domain error
        logger.error(
            "Checkpoint corrupt",
            extra={"basename": ckpt_path.name, "error_type": type(exc).__name__},
        )
        raise MusicTransformerCheckpointError(
            "checkpoint_corrupt",
            f"Failed to load checkpoint: {ckpt_path.name}",
            details={"error_type": type(exc).__name__},
        ) from exc

    if not isinstance(payload, dict) or "card" not in payload:
        raise MusicTransformerCheckpointError(
            "checkpoint_corrupt",
            "Checkpoint missing embedded card",
            details={"basename": ckpt_path.name},
        )
    try:
        card = MusicTransformerCheckpointCardV1.model_validate(payload["card"])
    except Exception as exc:  # noqa: BLE001
        raise MusicTransformerCheckpointError(
            "checkpoint_corrupt",
            "Checkpoint card failed validation",
            details={"error_type": type(exc).__name__},
        ) from exc

    if require_tokenizer_version:
        tok_cfg = tokenizer_config or default_tokenizer_config()
        try:
            verify_expectation(card.tokenizer, tok_cfg, require_version=True)
        except Exception as exc:
            logger.error(
                "Checkpoint tokenizer expectation mismatch",
                extra={
                    "basename": ckpt_path.name,
                    "tokenizer_version": card.tokenizer.expected_tokenizer_version,
                    "vocab_hash_prefix": card.tokenizer.vocab_hash[:12],
                },
            )
            raise MusicTransformerCheckpointError(
                "tokenizer_version_mismatch",
                "Checkpoint tokenizer expectation failed verification",
                details={"cause": getattr(exc, "code", type(exc).__name__)},
            ) from exc

    logger.info(
        "Checkpoint loaded",
        extra={
            "basename": ckpt_path.name,
            "architecture_digest_prefix": card.architecture_digest[:12],
            "tokenizer_version": card.tokenizer.expected_tokenizer_version,
            "vocab_hash_prefix": card.tokenizer.vocab_hash[:12],
            "dataset_version_id_prefix": (card.dataset_version_id or "")[:12] or None,
            "experiment_id": card.training.experiment_id,
            "global_step": card.training.global_step,
        },
    )
    return payload, card


def resume_checkpoint(
    path: Path | str,
    *,
    expected_architecture: MusicTransformerConfigV1 | None = None,
    expected_tokenizer: TokenizerModelExpectationV1 | None = None,
    expected_experiment_id: str | None = None,
    tokenizer_config: TokenizerConfigV1 | None = None,
    map_location: str | None = "cpu",
    require_optimizer: bool = False,
) -> ResumeState:
    """Load checkpoint for training resume; fail closed on digest / expectation mismatch."""
    payload, card = load_checkpoint(
        path,
        map_location=map_location,
        require_tokenizer_version=True,
        tokenizer_config=tokenizer_config,
    )
    if expected_architecture is not None:
        expected_digest = expected_architecture.config_digest()
        if card.architecture_digest != expected_digest:
            logger.error(
                "Resume architecture mismatch",
                extra={
                    "basename": Path(path).name,
                    "card_digest_prefix": card.architecture_digest[:12],
                    "expected_digest_prefix": expected_digest[:12],
                },
            )
            raise MusicTransformerResumeError(
                "resume_mismatch",
                "Checkpoint architecture digest does not match experiment",
                details={
                    "card_digest_prefix": card.architecture_digest[:12],
                    "expected_digest_prefix": expected_digest[:12],
                },
            )
    if expected_tokenizer is not None:
        if (
            card.tokenizer.expected_tokenizer_version != expected_tokenizer.expected_tokenizer_version
            or card.tokenizer.vocab_hash != expected_tokenizer.vocab_hash
        ):
            logger.error(
                "Resume tokenizer mismatch",
                extra={
                    "basename": Path(path).name,
                    "card_tokenizer_version": card.tokenizer.expected_tokenizer_version,
                    "card_vocab_hash_prefix": card.tokenizer.vocab_hash[:12],
                },
            )
            raise MusicTransformerResumeError(
                "resume_mismatch",
                "Checkpoint tokenizer expectation does not match experiment",
            )
    if expected_experiment_id is not None and card.training.experiment_id:
        if card.training.experiment_id != expected_experiment_id:
            raise MusicTransformerResumeError(
                "resume_mismatch",
                "Checkpoint experiment_id does not match",
                details={
                    "card_experiment_id": card.training.experiment_id,
                    "expected_experiment_id": expected_experiment_id,
                },
            )

    global_step = int(
        payload.get("global_step")
        or card.training.global_step
        or card.training.steps
        or 0
    )
    epoch = int(payload.get("epoch") or card.training.epoch or 0)
    has_optimizer = "optimizer_state_dict" in payload
    has_scheduler = "scheduler_state_dict" in payload
    has_scaler = "scaler_state_dict" in payload
    if require_optimizer and not has_optimizer:
        raise MusicTransformerResumeError(
            "optimizer_missing",
            "Checkpoint has no optimizer state for resume",
            details={"basename": Path(path).name},
        )
    if not has_optimizer:
        logger.warning(
            "Resume weights-only (optimizer state missing)",
            extra={"basename": Path(path).name, "global_step": global_step},
        )
    if "rng_state" in payload:
        try:
            restore_rng_state(payload["rng_state"])
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to restore RNG state on resume",
                extra={"error_type": type(exc).__name__},
            )
    logger.info(
        "Checkpoint resume ready",
        extra={
            "basename": Path(path).name,
            "global_step": global_step,
            "epoch": epoch,
            "experiment_id": card.training.experiment_id,
            "architecture_digest_prefix": card.architecture_digest[:12],
            "has_optimizer": has_optimizer,
            "has_scheduler": has_scheduler,
            "has_scaler": has_scaler,
        },
    )
    return ResumeState(
        payload=payload,
        card=card,
        global_step=global_step,
        epoch=epoch,
        experiment_id=card.training.experiment_id or payload.get("experiment_id"),
        has_optimizer=has_optimizer,
        has_scheduler=has_scheduler,
        has_scaler=has_scaler,
    )


def load_card_only(path: Path | str) -> MusicTransformerCheckpointCardV1:
    """Prefer sidecar JSON; fall back to embedded card in ``.pt``."""
    ckpt_path = Path(path)
    sidecar = _sidecar_path(ckpt_path)
    if sidecar.is_file():
        raw = json.loads(sidecar.read_text(encoding="utf-8"))
        return MusicTransformerCheckpointCardV1.model_validate(raw)
    _payload, card = load_checkpoint(
        ckpt_path,
        require_tokenizer_version=False,
    )
    return card


def default_training_card(
    *,
    seed: int,
    steps: int,
    batch_size: int,
    lr: float,
    max_seq_len: int,
    device: str,
    epochs: int | None = None,
    final_loss: float | None = None,
    experiment_id: str | None = None,
    global_step: int | None = None,
    epoch: int = 0,
    optimizer: str = "adamw",
    scheduler: str = "none",
    precision: str = "fp32",
    grad_accum_steps: int = 1,
    weight_decay: float | None = None,
    warmup_steps: int | None = None,
) -> MusicTransformerTrainingCardV1:
    return MusicTransformerTrainingCardV1(
        seed=seed,
        steps=steps,
        epochs=epochs,
        batch_size=batch_size,
        lr=lr,
        max_seq_len=max_seq_len,
        device=device,
        git_commit=capture_git_commit(),
        project_version=capture_project_version(),
        final_loss=final_loss,
        experiment_id=experiment_id,
        global_step=global_step if global_step is not None else steps,
        epoch=epoch,
        optimizer=optimizer,  # type: ignore[arg-type]
        scheduler=scheduler,  # type: ignore[arg-type]
        precision=precision,  # type: ignore[arg-type]
        grad_accum_steps=grad_accum_steps,
        weight_decay=weight_decay,
        warmup_steps=warmup_steps,
    )


def capture_rng_state() -> dict[str, Any]:
    torch = _require_torch()
    state: dict[str, Any] = {
        "python": random.getstate(),
        "torch": torch.get_rng_state(),
    }
    try:
        import numpy as np

        state["numpy"] = np.random.get_state()
    except ImportError:
        pass
    if torch.cuda.is_available():
        try:
            state["cuda"] = torch.cuda.get_rng_state_all()
        except Exception:  # noqa: BLE001
            pass
    return state


def restore_rng_state(state: dict[str, Any]) -> None:
    torch = _require_torch()
    if "python" in state:
        random.setstate(state["python"])
    if "torch" in state:
        torch.set_rng_state(state["torch"])
    if "numpy" in state:
        try:
            import numpy as np

            np.random.set_state(state["numpy"])
        except ImportError:
            pass
    if "cuda" in state and torch.cuda.is_available():
        try:
            torch.cuda.set_rng_state_all(state["cuda"])
        except Exception:  # noqa: BLE001
            pass


def _sidecar_path(ckpt: Path) -> Path:
    return ckpt.with_suffix(ckpt.suffix + ".card.json") if ckpt.suffix else Path(f"{ckpt}.card.json")


def _update_latest(latest: Path, source: Path) -> None:
    latest.parent.mkdir(parents=True, exist_ok=True)
    try:
        if latest.is_symlink() or latest.exists():
            latest.unlink()
        latest.symlink_to(source.name)
    except OSError:
        # Fall back to copy when symlinks are unavailable
        import shutil

        shutil.copy2(source, latest)
        sidecar_src = _sidecar_path(source)
        if sidecar_src.is_file():
            shutil.copy2(sidecar_src, _sidecar_path(latest))
