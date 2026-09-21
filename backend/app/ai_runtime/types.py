"""Shared descriptors for the AI model registry and routing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .capabilities import ModelCapability
from .operations import AiOperation

ModelLocality = Literal["local", "remote"]
# Additive local lifecycle statuses appear in discovery; selectable filters use ready.
ModelStatus = Literal[
    "ready",
    "unconfigured",
    "unavailable",
    "degraded",
    "loading",
    "out_of_memory",
    "unsupported_device",
]
ModelRuntimeId = Literal[
    "openai_compatible_chat",
    "fake",
    "stub",
    "local_openai_compatible",
    "symbolic_features",
    "music_transformer",
    "fake_symbolic",
]


@dataclass(frozen=True)
class ModelHealth:
    """Non-secret health snapshot for discovery and /ready."""

    status: ModelStatus
    detail: str | None = None
    credentials_present: bool = False


@dataclass(frozen=True)
class GenerationParameters:
    """Bounded generation params safe for provenance (no prompts/keys)."""

    temperature: float | None = None
    timeout_seconds: int | None = None
    candidate_count: int | None = None

    def to_public_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.temperature is not None:
            out["temperature"] = self.temperature
        if self.timeout_seconds is not None:
            out["timeout_seconds"] = self.timeout_seconds
        if self.candidate_count is not None:
            out["candidate_count"] = self.candidate_count
        return out


@dataclass(frozen=True)
class ModelDescriptor:
    """Registered model metadata (never includes secrets or weight paths)."""

    id: str
    display_name: str
    provider: str
    runtime: ModelRuntimeId
    primary_capability: ModelCapability
    locality: ModelLocality
    model_version: str | None
    supported_operations: tuple[AiOperation, ...]
    status: ModelStatus
    health: ModelHealth
    secondary_capabilities: tuple[ModelCapability, ...] = ()
    limits: dict[str, Any] = field(default_factory=dict)
    # Non-secret runtime hints only (e.g. model name string for chat APIs).
    provider_model: str | None = None


@dataclass(frozen=True)
class ResolvedModel:
    """Result of operation routing for a single request."""

    descriptor: ModelDescriptor
    operation: AiOperation
    resolution_path: Literal["explicit", "legacy", "op_default", "global", "fallback"]
    requested_model_id: str | None
    resolved_model_id: str
    fallback_applied: bool = False
    generation_parameters: GenerationParameters | None = None
