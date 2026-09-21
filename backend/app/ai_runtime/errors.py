"""Domain errors for AI runtime registry and routing."""

from __future__ import annotations


class AiRuntimeError(Exception):
    """Base class for AI runtime domain errors."""

    code: str = "ai_runtime_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class ModelNotFoundError(AiRuntimeError):
    """Requested model id is not registered."""

    code = "model_not_found"


class ModelUnavailableError(AiRuntimeError):
    """Model is registered but not available (unconfigured, unhealthy, stub)."""

    code = "model_unavailable"


class CapabilityMismatchError(AiRuntimeError):
    """Model does not support the requested operation or capability."""

    code = "capability_mismatch"


class FallbackNotConfiguredError(AiRuntimeError):
    """Primary model unavailable and no explicit fallback chain is configured."""

    code = "fallback_not_configured"
