"""Checkpoint save/load with ``music_transformer.checkpoint.v1`` card.

Lazy-imports torch so schemas/settings remain importable without the extra.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.music_transformer.errors import (
    MusicTransformerCheckpointError,
    MusicTransformerDependencyError,
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
        },
    )
    return card


def save_checkpoint(
    path: Path | str,
    model: Any,
    card: MusicTransformerCheckpointCardV1,
    *,
    optimizer: Any | None = None,
    write_sidecar: bool = True,
) -> Path:
    """Write ``.pt`` (state + embedded card) and optional ``.card.json`` sidecar."""
    torch = _require_torch()
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "model_state_dict": model.state_dict(),
        "card": card.model_dump(mode="json"),
        "schema_version": card.schema_version,
    }
    if optimizer is not None:
        payload["optimizer_state_dict"] = optimizer.state_dict()
    torch.save(payload, out)
    if write_sidecar:
        sidecar = _sidecar_path(out)
        sidecar.write_text(
            json.dumps(card.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    logger.info(
        "Checkpoint saved",
        extra={
            "basename": out.name,
            "architecture_digest_prefix": card.architecture_digest[:12],
            "tokenizer_version": card.tokenizer.expected_tokenizer_version,
            "vocab_hash_prefix": card.tokenizer.vocab_hash[:12],
            "dataset_version_id_prefix": (card.dataset_version_id or "")[:12] or None,
            "has_optimizer": optimizer is not None,
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
        },
    )
    return payload, card


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
    )


def _sidecar_path(ckpt: Path) -> Path:
    return ckpt.with_suffix(ckpt.suffix + ".card.json") if ckpt.suffix else Path(f"{ckpt}.card.json")
