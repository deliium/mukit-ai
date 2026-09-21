"""Tokenizer versioning helpers (wall-clock excluded from digests)."""

from __future__ import annotations

import logging

from app.tokenizer.errors import TokenizerVerifyError
from app.tokenizer.schemas import (
    TOKENIZER_VERSION,
    TokenizerConfigV1,
    TokenizerManifestV1,
    TokenizerModelExpectationV1,
)
from app.tokenizer.vocab import Vocab, build_vocab


logger = logging.getLogger(__name__)


def expected_tokenizer_record(
    config: TokenizerConfigV1,
    vocab: Vocab | None = None,
) -> TokenizerModelExpectationV1:
    """Build the sidecar trainers must embed for train/inference binding."""
    active = vocab or build_vocab(config)
    record = TokenizerModelExpectationV1(
        expected_tokenizer_version=TOKENIZER_VERSION,
        vocab_hash=active.vocab_hash,
        config_digest=config.config_digest(),
        profile=config.profile,
    )
    logger.info(
        "Model expectation record built",
        extra={
            "expected_tokenizer_version": record.expected_tokenizer_version,
            "vocab_hash_prefix": record.vocab_hash[:12],
            "profile": record.profile,
        },
    )
    return record


def verify_expectation(
    expectation: TokenizerModelExpectationV1,
    config: TokenizerConfigV1,
    vocab: Vocab | None = None,
    *,
    require_version: bool = True,
) -> None:
    active = vocab or build_vocab(config)
    if require_version and expectation.expected_tokenizer_version != TOKENIZER_VERSION:
        logger.error(
            "Tokenizer version mismatch",
            extra={
                "expected": expectation.expected_tokenizer_version,
                "actual": TOKENIZER_VERSION,
            },
        )
        raise TokenizerVerifyError(
            "version_mismatch",
            "expected_tokenizer_version does not match TOKENIZER_VERSION",
            details={
                "expected": expectation.expected_tokenizer_version,
                "actual": TOKENIZER_VERSION,
            },
        )
    if expectation.vocab_hash != active.vocab_hash:
        logger.error(
            "Vocab hash mismatch",
            extra={
                "expected_prefix": expectation.vocab_hash[:12],
                "actual_prefix": active.vocab_hash[:12],
            },
        )
        raise TokenizerVerifyError(
            "vocab_hash_mismatch",
            "vocab_hash does not match built vocabulary",
            details={
                "expected_prefix": expectation.vocab_hash[:12],
                "actual_prefix": active.vocab_hash[:12],
            },
        )
    logger.info(
        "Tokenizer expectation verified",
        extra={
            "tokenizer_version": TOKENIZER_VERSION,
            "vocab_hash_prefix": active.vocab_hash[:12],
        },
    )


def bump_policy() -> str:
    """Documented bump policy for TOKENIZER_VERSION."""
    return (
        "Bump TOKENIZER_VERSION (tokenizer.vN) whenever vocabulary families, "
        "special-token ids, or quantization rules change identity. Rebuild "
        "tokenizer.manifest.v1 and require trainers to update "
        "expected_tokenizer_version + vocab_hash."
    )
