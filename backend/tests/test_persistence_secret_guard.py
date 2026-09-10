"""Tests for persistence secret field/value rejection."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.project_history_schemas import AiProvenance, BranchCreateRequest
from app.project_schemas import ProjectCreateRequest, ProjectGenerationMeta
from app.services.persistence_secret_guard import (
    PersistenceSecretError,
    assert_no_secret_values,
    assert_payload_has_no_secret_values,
    contains_forbidden_secret_fields,
    find_secret_value_hits,
)


def test_forbidden_secret_field_paths():
    hits = contains_forbidden_secret_fields(
        {"generation": {"provider": "openai", "api_key": "x", "nested": {"access_token": "y"}}}
    )
    assert "generation.api_key" in hits
    assert "generation.nested.access_token" in hits


def test_ordinary_prose_is_not_a_secret():
    prose = "Write a brighter chorus with more syncopation and leave the bass alone."
    assert find_secret_value_hits(prose) == []
    assert_no_secret_values(prose, field_name="user_instruction")


@pytest.mark.parametrize(
    "text",
    [
        "please use sk-abcdefghijklmnopqrstuvwxyz012345",
        "Authorization: Bearer abcdefghijklmnop0123456789",
        "token ghp_abcdefghijklmnopqrstuv",
        "slack xoxb-1234567890-abcdefghij",
    ],
)
def test_common_token_patterns_rejected(text):
    with pytest.raises(PersistenceSecretError) as exc:
        assert_no_secret_values(text, field_name="user_instruction")
    assert exc.value.code == "forbidden_secret_value"


def test_exact_configured_provider_secret_rejected(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "super-secret-openai-key-value")
    with pytest.raises(PersistenceSecretError):
        assert_no_secret_values(
            "instruction with super-secret-openai-key-value inside",
            field_name="user_instruction",
        )


def test_nested_generation_prompt_secret_rejected():
    with pytest.raises(PersistenceSecretError):
        assert_payload_has_no_secret_values(
            {
                "provider": "openai",
                "prompt": {"instruction": "use sk-abcdefghijklmnopqrstuvwxyz012345"},
            },
            context="generation",
        )


def test_project_create_rejects_secret_field():
    with pytest.raises(ValidationError):
        ProjectCreateRequest.model_validate(
            {"name": "Leak", "generation": {"provider": "openai", "api_key": "sk-test"}}
        )


def test_project_generation_meta_rejects_token_pattern():
    with pytest.raises(ValidationError):
        ProjectGenerationMeta.model_validate(
            {
                "provider": "openai",
                "prompt": {"instruction": "Bearer abcdefghijklmnop0123456789"},
            }
        )


def test_ai_provenance_rejects_instruction_secret():
    with pytest.raises(ValidationError):
        AiProvenance.model_validate(
            {
                "provider": "fake",
                "user_instruction": "sk-abcdefghijklmnopqrstuvwxyz012345",
            }
        )


def test_branch_create_rejects_secret_in_name():
    with pytest.raises(ValidationError):
        BranchCreateRequest.model_validate(
            {
                "name": "sk-abcdefghijklmnopqrstuvwxyz012345",
                "from_revision_id": "rev-1",
            }
        )
