"""Pydantic models for local project persistence API."""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from .schemas import CompositionV1, CompositionV2, LLMPromptParameters

logger = logging.getLogger(__name__)

FORBIDDEN_SECRET_FIELD_NAMES = {
    "api_key",
    "apikey",
    "openai_api_key",
    "deepseek_api_key",
    "authorization",
    "access_token",
    "secret",
    "password",
}


def _contains_forbidden_secret_fields(payload: Any, *, path: str = "") -> list[str]:
    """Return dotted paths of API-key-like fields found in a nested payload."""
    found: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_path = f"{path}.{key}" if path else str(key)
            normalized = str(key).strip().lower().replace("-", "_")
            if normalized in FORBIDDEN_SECRET_FIELD_NAMES or normalized.endswith("_api_key"):
                found.append(key_path)
            found.extend(_contains_forbidden_secret_fields(value, path=key_path))
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            found.extend(_contains_forbidden_secret_fields(item, path=f"{path}[{index}]"))
    return found


class ProjectGenerationMeta(BaseModel):
    provider: Literal["openai", "deepseek", "fake"] | None = None
    model: str | None = Field(default=None, max_length=120)
    prompt: LLMPromptParameters | dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_secret_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            secrets = _contains_forbidden_secret_fields(data)
            if secrets:
                logger.warning(
                    "Rejected generation meta containing secret-like fields",
                    extra={"secret_field_count": len(secrets)},
                )
                raise ValueError(
                    "Generation metadata must not include API keys or secret fields "
                    f"(found: {', '.join(secrets[:5])})"
                )
        return data


class ProjectCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    composition: dict[str, Any] | CompositionV1 | CompositionV2 | None = None
    generation: ProjectGenerationMeta | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_secret_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            secrets = _contains_forbidden_secret_fields(data)
            if secrets:
                logger.warning(
                    "Rejected project create payload containing secret-like fields",
                    extra={"secret_field_count": len(secrets)},
                )
                raise ValueError(
                    "Project payloads must not include API keys or secret fields "
                    f"(found: {', '.join(secrets[:5])})"
                )
        return data


class ProjectPatchRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    composition: dict[str, Any] | CompositionV1 | CompositionV2 | None = None
    generation: ProjectGenerationMeta | None = None
    clear_composition: bool = False
    clear_generation: bool = False

    @model_validator(mode="before")
    @classmethod
    def reject_secret_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            secrets = _contains_forbidden_secret_fields(data)
            if secrets:
                logger.warning(
                    "Rejected project patch payload containing secret-like fields",
                    extra={"secret_field_count": len(secrets)},
                )
                raise ValueError(
                    "Project payloads must not include API keys or secret fields "
                    f"(found: {', '.join(secrets[:5])})"
                )
        return data

    @model_validator(mode="after")
    def require_at_least_one_change(self) -> "ProjectPatchRequest":
        if (
            self.name is None
            and self.composition is None
            and self.generation is None
            and not self.clear_composition
            and not self.clear_generation
        ):
            raise ValueError("PATCH requires at least one of name, composition, generation, or clear flags")
        return self


class ProjectListItem(BaseModel):
    id: str
    name: str
    created_at: str
    updated_at: str
    has_composition: bool
    track_count: int = 0
    event_count: int = 0
    bar_count: int = 0


class ProjectListResponse(BaseModel):
    projects: list[ProjectListItem]


class ProjectDetailResponse(BaseModel):
    id: str
    name: str
    created_at: str
    updated_at: str
    composition: CompositionV2 | None = None
    generation_provider: str | None = None
    generation_model: str | None = None
    generation_prompt: dict[str, Any] | None = None
    composition_migrated: bool = False
    migration_path: str | None = None


class ProjectDuplicateResponse(ProjectDetailResponse):
    pass
