"""``tokenizer.manifest.v1`` writer / reader / verify."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from app.tokenizer.errors import TokenizerIOError, TokenizerVerifyError
from app.tokenizer.schemas import TokenizerConfigV1, TokenizerManifestV1
from app.tokenizer.vocab import Vocab, build_vocab


logger = logging.getLogger(__name__)


def build_manifest(
    config: TokenizerConfigV1,
    vocab: Vocab | None = None,
    *,
    created_at: datetime | None = None,
) -> TokenizerManifestV1:
    active = vocab or build_vocab(config)
    manifest = TokenizerManifestV1(
        tokenizer_version=config.tokenizer_version,
        vocab_hash=active.vocab_hash,
        config_digest=config.config_digest(),
        special_token_ids=dict(active.special_token_ids),
        grid_subdivisions_per_quarter=config.grid_subdivisions_per_quarter,
        velocity_bins=config.velocity_bins,
        profile=config.profile,
        vocab_size=active.size,
        created_at=created_at or datetime.now(timezone.utc),
    )
    logger.info(
        "Tokenizer manifest built",
        extra={
            "tokenizer_version": manifest.tokenizer_version,
            "vocab_hash_prefix": manifest.vocab_hash[:12],
            "vocab_size": manifest.vocab_size,
            "profile": manifest.profile,
        },
    )
    return manifest


def write_manifest(path: Path, manifest: TokenizerManifestV1) -> None:
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise TokenizerIOError(
            "manifest_write_failed",
            f"failed to write manifest {path.name}",
            details={"basename": path.name},
        ) from exc
    logger.info(
        "Tokenizer manifest written",
        extra={
            "basename": path.name,
            "tokenizer_version": manifest.tokenizer_version,
            "vocab_hash_prefix": manifest.vocab_hash[:12],
        },
    )


def read_manifest(path: Path) -> TokenizerManifestV1:
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TokenizerIOError(
            "manifest_read_failed",
            f"failed to read manifest {path.name}",
            details={"basename": path.name},
        ) from exc
    return TokenizerManifestV1.model_validate(raw)


def verify_manifest(
    path: Path,
    config: TokenizerConfigV1 | None = None,
    vocab: Vocab | None = None,
    *,
    require_version: bool = False,
) -> TokenizerManifestV1:
    """Verify on-disk manifest against a freshly built vocab/config.

    When ``require_version`` is True, a ``tokenizer_version`` mismatch raises.
    When False, version drift is logged as a warning and hash/digest checks continue.
    """
    from app.tokenizer.schemas import default_tokenizer_config

    cfg = config or default_tokenizer_config()
    active = vocab or build_vocab(cfg)
    manifest = read_manifest(path)
    if manifest.tokenizer_version != cfg.tokenizer_version:
        if require_version:
            logger.error(
                "Manifest version mismatch",
                extra={
                    "manifest_version": manifest.tokenizer_version,
                    "config_version": cfg.tokenizer_version,
                    "require_version": True,
                },
            )
            raise TokenizerVerifyError(
                "version_mismatch",
                "manifest tokenizer_version does not match config",
            )
        logger.warning(
            "Manifest tokenizer_version differs; continuing hash checks",
            extra={
                "manifest_version": manifest.tokenizer_version,
                "config_version": cfg.tokenizer_version,
                "require_version": False,
            },
        )
    if manifest.vocab_hash != active.vocab_hash:
        logger.error(
            "Manifest vocab hash mismatch",
            extra={
                "manifest_prefix": manifest.vocab_hash[:12],
                "actual_prefix": active.vocab_hash[:12],
            },
        )
        raise TokenizerVerifyError(
            "vocab_hash_mismatch",
            "manifest vocab_hash does not match rebuilt vocabulary",
        )
    expected_digest = cfg.config_digest()
    if manifest.config_digest != expected_digest:
        logger.error(
            "Manifest config digest mismatch",
            extra={
                "manifest_prefix": manifest.config_digest[:12],
                "actual_prefix": expected_digest[:12],
            },
        )
        raise TokenizerVerifyError(
            "config_digest_mismatch",
            "manifest config_digest does not match config",
        )
    logger.info(
        "Tokenizer manifest verified",
        extra={
            "basename": Path(path).name,
            "vocab_hash_prefix": manifest.vocab_hash[:12],
        },
    )
    return manifest
