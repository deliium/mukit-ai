"""Pydantic models for local project persistence API."""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from .schemas import CompositionV1, CompositionV2, LLMPromptParameters
from .services.persistence_secret_guard import (
    assert_no_secret_fields,
    assert_payload_has_no_secret_values,
    contains_forbidden_secret_fields,
)

logger = logging.getLogger(__name__)

# Back-compat re-export for existing imports/tests.
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
    return contains_forbidden_secret_fields(payload, path=path)


class ProjectGenerationMeta(BaseModel):
    provider: Literal["openai", "deepseek", "fake"] | None = None
    model: str | None = Field(default=None, max_length=120)
    prompt: LLMPromptParameters | dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_secret_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            assert_no_secret_fields(data, context="generation_meta")
            assert_payload_has_no_secret_values(data, context="generation_meta")
        return data


class ProjectCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    composition: dict[str, Any] | CompositionV1 | CompositionV2 | None = None
    generation: ProjectGenerationMeta | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_secret_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            assert_no_secret_fields(data, context="project_create")
            # Scan generation/prompt text only; composition note payloads are musical.
            generation = data.get("generation")
            if generation is not None:
                assert_payload_has_no_secret_values(generation, context="project_create.generation")
        return data


class ProjectPatchRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    composition: dict[str, Any] | CompositionV1 | CompositionV2 | None = None
    generation: ProjectGenerationMeta | None = None
    clear_composition: bool = False
    clear_generation: bool = False
    branch_id: str | None = Field(default=None, min_length=1, max_length=80)
    expected_active_branch_id: str | None = Field(default=None, min_length=1, max_length=80)
    expected_working_version: int | None = Field(default=None, ge=0)
    expected_source_fingerprint: str | None = Field(default=None, min_length=16, max_length=128)

    @model_validator(mode="before")
    @classmethod
    def reject_secret_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            assert_no_secret_fields(data, context="project_patch")
            generation = data.get("generation")
            if generation is not None:
                assert_payload_has_no_secret_values(generation, context="project_patch.generation")
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
    active_branch_id: str | None = None
    active_branch_name: str | None = None
    current_revision_id: str | None = None
    current_revision_sequence: int | None = None
    working_version: int | None = None
    working_fingerprint: str | None = None


class ProjectDuplicateResponse(ProjectDetailResponse):
    pass
