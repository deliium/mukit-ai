"""DTOs for AI model discovery (`GET /ai/models`)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AiModelCatalogItem(BaseModel):
    id: str = Field(..., max_length=160)
    display_name: str = Field(..., max_length=200)
    provider: str = Field(..., max_length=64)
    runtime: str = Field(..., max_length=64)
    primary_capability: str = Field(..., max_length=64)
    locality: str = Field(..., max_length=16)
    model_version: str | None = Field(default=None, max_length=120)
    supported_operations: list[str] = Field(default_factory=list, max_length=32)
    status: str = Field(..., max_length=32)
    credentials_present: bool = False
    is_default: bool = False
    limits: dict[str, Any] = Field(default_factory=dict)


class AiModelsResponse(BaseModel):
    models: list[AiModelCatalogItem] = Field(default_factory=list)
    default_model_id: str | None = Field(default=None, max_length=160)
    operation_defaults: dict[str, str | None] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list, max_length=16)
