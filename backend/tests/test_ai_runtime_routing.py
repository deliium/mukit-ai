"""Tests for AI operation routing and fallback."""

from __future__ import annotations

import pytest

from app.ai_runtime.errors import (
    FallbackNotConfiguredError,
    ModelNotFoundError,
    ModelUnavailableError,
)
from app.ai_runtime.operations import AiOperation
from app.ai_runtime import registry as registry_mod
from app.ai_runtime.routing import (
    ModelSelectionInput,
    is_fake_resolved,
    resolve_model_for_operation,
)
from app.schemas import LLMModelSelection


@pytest.fixture(autouse=True)
def _clear_registry():
    registry_mod.clear_registry_for_tests()
    yield
    registry_mod.clear_registry_for_tests()


def test_resolve_explicit_model_id():
    env = {"OPENAI_API_KEY": "sk", "OPENAI_MODEL": "gpt-4o-mini", "LLM_FAKE_MODE": "1"}
    registry_mod.reload_registry(env)
    resolved = resolve_model_for_operation(
        AiOperation.GENERATE,
        ModelSelectionInput(model_id="openai:gpt-4o-mini"),
        env=env,
    )
    assert resolved.resolution_path == "explicit"
    assert resolved.resolved_model_id == "openai:gpt-4o-mini"
    assert resolved.fallback_applied is False


def test_resolve_legacy_provider_model():
    env = {"DEEPSEEK_API_KEY": "sk", "DEEPSEEK_MODEL": "deepseek-chat"}
    registry_mod.reload_registry(env)
    resolved = resolve_model_for_operation(
        AiOperation.REGION_EDIT,
        LLMModelSelection(provider="deepseek", model="deepseek-chat"),
        env=env,
    )
    assert resolved.resolution_path == "legacy"
    assert resolved.resolved_model_id == "deepseek:deepseek-chat"


def test_resolve_op_default_env():
    env = {
        "OPENAI_API_KEY": "sk",
        "OPENAI_MODEL": "gpt-4o-mini",
        "DEEPSEEK_API_KEY": "sk2",
        "DEEPSEEK_MODEL": "deepseek-chat",
        "DEFAULT_LLM_PROVIDER": "openai",
        "AI_OP_ARRANGE_PREVIEW": "deepseek:deepseek-chat",
    }
    registry_mod.reload_registry(env)
    resolved = resolve_model_for_operation(AiOperation.ARRANGE_PREVIEW, None, env=env)
    assert resolved.resolution_path == "op_default"
    assert resolved.resolved_model_id == "deepseek:deepseek-chat"


def test_resolve_global_default_fake():
    env = {"LLM_FAKE_MODE": "1"}
    registry_mod.reload_registry(env)
    resolved = resolve_model_for_operation(AiOperation.GENERATE, None, env=env)
    assert resolved.resolution_path == "global"
    assert is_fake_resolved(resolved)
    assert resolved.resolved_model_id.startswith("fake:")


def test_fallback_when_primary_unavailable():
    env = {
        "LLM_FAKE_MODE": "1",
        "AI_OP_GENERATE": "local:embedding-stub",
        "AI_FALLBACK_GENERATE": "fake:fake-deterministic",
    }
    registry_mod.reload_registry(env)
    resolved = resolve_model_for_operation(AiOperation.GENERATE, None, env=env)
    assert resolved.fallback_applied is True
    assert resolved.resolution_path == "fallback"
    assert resolved.resolved_model_id == "fake:fake-deterministic"
    assert resolved.requested_model_id == "local:embedding-stub"


def test_unavailable_without_fallback_fails_closed():
    env = {
        "AI_OP_EMBED": "local:embedding-stub",
    }
    registry_mod.reload_registry(env)
    with pytest.raises(FallbackNotConfiguredError) as exc:
        resolve_model_for_operation(AiOperation.EMBED, None, env=env)
    assert exc.value.code == "fallback_not_configured"


def test_unknown_explicit_model_raises():
    env = {"LLM_FAKE_MODE": "1"}
    registry_mod.reload_registry(env)
    with pytest.raises((ModelNotFoundError, FallbackNotConfiguredError, ModelUnavailableError)):
        resolve_model_for_operation(
            AiOperation.GENERATE,
            ModelSelectionInput(model_id="missing:model"),
            env=env,
        )


def test_llm_model_selection_accepts_model_id():
    sel = LLMModelSelection(model_id="openai:gpt-4o-mini")
    assert sel.model_id == "openai:gpt-4o-mini"
    assert sel.provider is None


def test_schedule_resolution_path_when_enabled(monkeypatch):
    from app.ai_runtime.capabilities import ModelCapability
    from app.ai_runtime.types import ModelDescriptor, ModelHealth
    from app.services import scheduling_candidates as candidates_mod

    monkeypatch.setattr(candidates_mod, "_load_projected_nodes", lambda: {})
    descriptor = ModelDescriptor(
        id="local:llama-planner",
        display_name="llama",
        provider="local",
        runtime="local_openai_compatible",
        primary_capability=ModelCapability.LANGUAGE_PLANNER,
        locality="local",
        model_version=None,
        supported_operations=(AiOperation.GENERATE_PLANNER, AiOperation.GENERATE),
        status="ready",
        health=ModelHealth(status="ready", credentials_present=True),
        limits={
            "memory_available_mb": 4096,
            "device_class": "igpu",
            "estimated_latency_ms": 40,
        },
        provider_model="llama",
    )
    registry_mod.register_model(descriptor, overwrite=True)
    env = {
        "AI_SCHEDULING_ENABLED": "1",
        "AI_SCHEDULING_DEFAULT_MODE": "prefer_local",
        "AI_SCHEDULING_LOCAL_MEMORY_AVAILABLE_MB": "4096",
        "AI_SCHEDULING_LOCAL_DEVICE_CLASS": "igpu",
        "AI_SCHEDULING_LOCAL_ESTIMATED_LATENCY_MS": "40",
    }
    resolved = resolve_model_for_operation(
        AiOperation.GENERATE_PLANNER,
        None,
        env=env,
        collapse_reserved_generate=False,
    )
    assert resolved.resolution_path == "schedule"
    assert resolved.resolved_model_id == "local:llama-planner"
