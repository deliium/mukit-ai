"""Domain errors for the Music Transformer package."""

from __future__ import annotations

from typing import Any


class MusicTransformerError(Exception):
    """Base error for Music Transformer failures."""

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


class MusicTransformerConfigError(MusicTransformerError):
    """Invalid architecture / train / sample config."""


class MusicTransformerCheckpointError(MusicTransformerError):
    """Checkpoint save/load or card verification failure."""


class MusicTransformerDependencyError(MusicTransformerError):
    """Optional torch (or device) unavailable."""


class MusicTransformerTrainError(MusicTransformerError):
    """Training loop / data collation failure."""


class MusicTransformerGenerateError(MusicTransformerError):
    """Sampling / constrained generate / decode rejection."""


class MusicTransformerIOError(MusicTransformerError):
    """Filesystem read/write failure for configs or checkpoints."""
