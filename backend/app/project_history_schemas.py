"""Strict DTOs for project revision / branch history APIs."""

from __future__ import annotations

import logging
import unicodedata
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.composition_schemas import CompositionV2
from app.services.persistence_secret_guard import (
    assert_no_secret_fields,
    assert_no_secret_values,
)

logger = logging.getLogger(__name__)

USER_INSTRUCTION_MAX_LENGTH = 2000
REVISION_NAME_MAX_LENGTH = 120
BRANCH_NAME_MAX_LENGTH = 80
AFFECTED_RANGE_MAX_COUNT = 64
AFFECTED_TRACK_MAX_COUNT = 64
WARNING_CODE_MAX_COUNT = 32
WARNING_CODE_MAX_LENGTH = 80
DEFAULT_REVISION_PAGE_LIMIT = 50
MAX_REVISION_PAGE_LIMIT = 100


class RevisionOperationType(StrEnum):
    PROJECT_CREATE = "project-create"
    MANUAL_CHECKPOINT = "manual-checkpoint"
    PRE_AI_CHECKPOINT = "pre-ai-checkpoint"
    GENERATE_APPLY = "generate-apply"
    AI_REGION_EDIT_APPLY = "ai-region-edit-apply"
    CREATIVE_MOTIF_APPLY = "creative-motif-apply"
    REHARMONIZE_APPLY = "reharmonize-apply"
    DEVELOPMENT_APPLY = "development-apply"
    ARRANGEMENT_APPLY = "arrangement-apply"
    REVISION_RESTORE = "revision-restore"
    IMPORT = "import"
    MIGRATION = "migration"


class AffectedBarRange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_bar: int = Field(..., ge=1)
    end_bar: int = Field(..., ge=1)

    @model_validator(mode="after")
    def validate_order(self) -> AffectedBarRange:
        if self.end_bar < self.start_bar:
            raise ValueError("end_bar must be >= start_bar")
        return self


class DeclaredScope(BaseModel):
    """Client-declared authorization scope; actual scope is derived server-side."""

    model_config = ConfigDict(extra="forbid")

    ranges: list[AffectedBarRange] = Field(default_factory=list, max_length=AFFECTED_RANGE_MAX_COUNT)
    track_ids: list[str] = Field(default_factory=list, max_length=AFFECTED_TRACK_MAX_COUNT)

    @field_validator("track_ids")
    @classmethod
    def validate_track_ids(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for track_id in value:
            normalized = track_id.strip()
            if not normalized:
                raise ValueError("track_ids must be non-empty strings")
            if normalized in seen:
                continue
            seen.add(normalized)
            cleaned.append(normalized)
        return cleaned


class AiProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["openai", "deepseek", "fake"] | None = None
    model: str | None = Field(default=None, max_length=120)
    user_instruction: str | None = Field(default=None, max_length=USER_INSTRUCTION_MAX_LENGTH)
    candidate_id: str | None = Field(default=None, max_length=120)
    candidate_fingerprint: str | None = Field(default=None, min_length=16, max_length=128)
    warning_codes: list[str] = Field(default_factory=list, max_length=WARNING_CODE_MAX_COUNT)

    @field_validator("user_instruction")
    @classmethod
    def validate_instruction(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        assert_no_secret_values(cleaned, field_name="user_instruction")
        return cleaned

    @field_validator("warning_codes")
    @classmethod
    def validate_warning_codes(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for code in value:
            normalized = code.strip()
            if not normalized or len(normalized) > WARNING_CODE_MAX_LENGTH:
                raise ValueError("warning_codes entries must be 1..80 characters")
            cleaned.append(normalized)
        return cleaned

    @model_validator(mode="before")
    @classmethod
    def reject_secret_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            assert_no_secret_fields(data, context="ai_provenance")
        return data


class RevisionListItem(BaseModel):
    """Metadata-only revision row — never includes composition JSON."""

    model_config = ConfigDict(extra="forbid")

    id: str
    project_id: str
    parent_revision_id: str | None = None
    sequence: int = Field(..., ge=1)
    name: str | None = None
    operation_type: RevisionOperationType
    ai_provider: str | None = None
    ai_model: str | None = None
    has_user_instruction: bool = False
    affected_ranges: list[AffectedBarRange] = Field(default_factory=list)
    affected_track_ids: list[str] = Field(default_factory=list)
    snapshot_fingerprint: str
    created_at: str
    summary: dict[str, Any] = Field(default_factory=dict)


class RevisionListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revisions: list[RevisionListItem]
    next_before_sequence: int | None = None


class RevisionDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: RevisionListItem
    composition: CompositionV2 | None = None


class RevisionNameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=REVISION_NAME_MAX_LENGTH)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @model_validator(mode="before")
    @classmethod
    def reject_secret_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            assert_no_secret_fields(data, context="revision_name")
            if "name" in data and data["name"] is not None:
                assert_no_secret_values(str(data["name"]), field_name="name")
        return data


class DurableCommitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    branch_id: str = Field(..., min_length=1, max_length=80)
    expected_active_branch_id: str = Field(..., min_length=1, max_length=80)
    expected_working_version: int = Field(..., ge=0)
    expected_head_revision_id: str = Field(..., min_length=1, max_length=80)
    expected_source_fingerprint: str = Field(..., min_length=16, max_length=128)
    composition: CompositionV2 | None = None
    clear_composition: bool = False
    operation_type: RevisionOperationType
    checkpoint_dirty_draft: bool = False
    name: str | None = Field(default=None, max_length=REVISION_NAME_MAX_LENGTH)
    declared_scope: DeclaredScope | None = None
    ai: AiProvenance | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_secret_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            assert_no_secret_fields(data, context="durable_commit")
        return data

    @model_validator(mode="after")
    def validate_operation(self) -> DurableCommitRequest:
        if self.operation_type == RevisionOperationType.REVISION_RESTORE:
            raise ValueError("Use the restore endpoint for revision-restore operations")
        if self.clear_composition and self.composition is not None:
            raise ValueError("clear_composition cannot be combined with composition")
        return self


class RestoreRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    branch_id: str = Field(..., min_length=1, max_length=80)
    expected_active_branch_id: str = Field(..., min_length=1, max_length=80)
    expected_working_version: int = Field(..., ge=0)
    expected_head_revision_id: str = Field(..., min_length=1, max_length=80)


class BranchListItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    project_id: str
    name: str
    head_revision_id: str
    created_from_revision_id: str | None = None
    working_version: int = Field(..., ge=0)
    working_fingerprint: str
    is_active: bool
    created_at: str
    updated_at: str


class BranchListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    branches: list[BranchListItem]


class BranchCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=BRANCH_NAME_MAX_LENGTH)
    from_revision_id: str = Field(..., min_length=1, max_length=80)
    checkout: bool = False

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Branch name must not be empty")
        assert_no_secret_values(cleaned, field_name="name")
        return cleaned

    @model_validator(mode="before")
    @classmethod
    def reject_secret_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            assert_no_secret_fields(data, context="branch_create")
        return data


class BranchRenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=BRANCH_NAME_MAX_LENGTH)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Branch name must not be empty")
        assert_no_secret_values(cleaned, field_name="name")
        return cleaned


class BranchCheckoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_active_branch_id: str = Field(..., min_length=1, max_length=80)
    expected_working_version: int = Field(..., ge=0)
    expected_head_revision_id: str = Field(..., min_length=1, max_length=80)


class ApplyAsBranchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=BRANCH_NAME_MAX_LENGTH)
    source_branch_id: str = Field(..., min_length=1, max_length=80)
    expected_active_branch_id: str = Field(..., min_length=1, max_length=80)
    expected_working_version: int = Field(..., ge=0)
    expected_head_revision_id: str = Field(..., min_length=1, max_length=80)
    expected_source_fingerprint: str = Field(..., min_length=16, max_length=128)
    composition: CompositionV2 | None = None
    clear_composition: bool = False
    operation_type: RevisionOperationType
    declared_scope: DeclaredScope | None = None
    ai: AiProvenance | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Branch name must not be empty")
        assert_no_secret_values(cleaned, field_name="name")
        if not unicodedata.normalize("NFKC", cleaned).casefold():
            raise ValueError("Branch name normalizes to empty")
        return cleaned

    @model_validator(mode="before")
    @classmethod
    def reject_secret_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            assert_no_secret_fields(data, context="apply_as_branch")
        return data


    @model_validator(mode="after")
    def validate_operation(self) -> ApplyAsBranchRequest:
        if self.operation_type == RevisionOperationType.REVISION_RESTORE:
            raise ValueError("Use the restore endpoint for revision-restore operations")
        if self.clear_composition and self.composition is not None:
            raise ValueError("clear_composition cannot be combined with composition")
        return self


class ProjectRevisionConflictBody(BaseModel):
    """Bounded 409 payload — IDs and sequences only."""

    model_config = ConfigDict(extra="forbid")

    code: Literal["project_revision_conflict"] = "project_revision_conflict"
    project_id: str
    expected_active_branch_id: str | None = None
    current_active_branch_id: str | None = None
    expected_working_version: int | None = None
    current_working_version: int | None = None
    expected_head_revision_id: str | None = None
    current_head_revision_id: str | None = None
    expected_source_fingerprint_prefix: str | None = None
    current_working_fingerprint_prefix: str | None = None
    current_sequence: int | None = None


class DurableCommandResponse(BaseModel):
    """Shared response for durable commit / restore / apply-as-branch."""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    active_branch_id: str
    active_branch_name: str
    current_revision_id: str
    current_revision_sequence: int
    working_version: int
    working_fingerprint: str
    composition: CompositionV2 | None = None
    revision_created: bool
    created_revision_ids: list[str] = Field(default_factory=list)
    operation_type: RevisionOperationType | None = None
