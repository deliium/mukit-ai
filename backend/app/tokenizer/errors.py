"""Domain errors for the Composition V2 tokenizer package."""

from __future__ import annotations

from typing import Any


class TokenizerError(Exception):
    """Base error for tokenizer failures."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class TokenizerConfigError(TokenizerError):
    """Invalid tokenizer config or unsupported profile/settings."""


class TokenizerVocabError(TokenizerError):
    """Vocabulary build / size / hash failures."""


class TokenizerEncodeError(TokenizerError):
    """Composition → tokens encode failure."""


class TokenizerDecodeError(TokenizerError):
    """Tokens → Composition V2 decode / reject failure."""


class TokenizerVerifyError(TokenizerError):
    """Manifest / version / vocab_hash verification failure."""


class TokenizerIOError(TokenizerError):
    """Filesystem read/write failure for tokenizer artifacts."""
