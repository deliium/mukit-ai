"""Domain errors for the offline dataset pipeline."""

from __future__ import annotations

from typing import Any


class DatasetError(Exception):
    """Base error for dataset pipeline failures."""

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


class DatasetConfigError(DatasetError):
    """Invalid pipeline config or missing required fields."""


class DatasetProvenanceError(DatasetError):
    """Missing or unsafe provenance / eligibility policy violation."""


class DatasetIngestError(DatasetError):
    """Source ingest failure (format, limits, parse)."""


class DatasetStoreError(DatasetError):
    """Filesystem store / atomic write failure."""


class DatasetVerifyError(DatasetError):
    """Manifest / version digest verification failure."""
