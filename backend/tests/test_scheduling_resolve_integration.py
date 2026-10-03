"""Schedule resolve path, trust clamp, and reschedule without escalation."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.ai_runtime import registry as registry_mod
from app.ai_runtime.capabilities import ModelCapability
from app.ai_runtime.errors import ModelUnavailableError
from app.ai_runtime.invoke_text import ainvoke_text_for_resolved
from app.ai_runtime.operations import AiOperation
from app.ai_runtime.routing import resolve_model_for_operation
from app.ai_runtime.types import ModelDescriptor, ModelHealth, ResolvedModel
from app.services import scheduling_candidates as candidates_mod


@pytest.fixture(autouse=True)
def _clear_registry():
    registry_mod.clear_registry_for_tests()
    yield
    registry_mod.clear_registry_for_tests()


def _local_planner() -> ModelDescriptor:
    return ModelDescriptor(
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
            "memory_total_mb": 8192,
            "device_class": "igpu",
            "estimated_latency_ms": 40,
        },
        provider_model="llama",
    )


def _node_b() -> ModelDescriptor:
    return ModelDescriptor(
        id="node:bbbbbbbbbbbbbbbb:sym",
        display_name="sym",
        provider="execution_node",
        runtime="execution_node",
        primary_capability=ModelCapability.SYMBOLIC_COMPOSER,
        locality="remote",
        model_version=None,
        supported_operations=(
            AiOperation.GENERATE_COMPOSER,
            AiOperation.GENERATE_PLANNER,
            AiOperation.GENERATE,
        ),
        status="ready",
        health=ModelHealth(status="ready", credentials_present=True),
        limits={"execution_node_id": "node_bbbbbbbbbbbbbbbb"},
        provider_model="sym",
        secondary_capabilities=(ModelCapability.LANGUAGE_PLANNER,),
    )


def _public() -> ModelDescriptor:
    return ModelDescriptor(
        id="openai:gpt",
        display_name="gpt",
        provider="openai",
        runtime="openai_compatible_chat",
        primary_capability=ModelCapability.LANGUAGE_PLANNER,
        locality="remote",
        model_version=None,
        supported_operations=(AiOperation.GENERATE_PLANNER, AiOperation.GENERATE),
        status="ready",
        health=ModelHealth(status="ready", credentials_present=True),
        provider_model="gpt",
    )


def _register_abc(monkeypatch) -> None:
    for descriptor in (_local_planner(), _node_b(), _public()):
        registry_mod.register_model(descriptor, overwrite=True)
    node = SimpleNamespace(
        node_id="node_bbbbbbbbbbbbbbbb",
        availability="available",
        capabilities=["symbolic_composer", "language_planner"],
        hardware=SimpleNamespace(device_class="dgpu"),
        resources=SimpleNamespace(memory_available_mb=48000, memory_total_mb=65536),
        health=SimpleNamespace(latency_ms=25),
    )
    monkeypatch.setattr(candidates_mod, "_load_projected_nodes", lambda: {node.node_id: node})


def test_flag_off_keeps_global_path(monkeypatch) -> None:
    monkeypatch.delenv("AI_SCHEDULING_ENABLED", raising=False)
    registry_mod.register_model(_local_planner(), overwrite=True)
    monkeypatch.setattr(candidates_mod, "_load_projected_nodes", lambda: {})
    resolved = resolve_model_for_operation(
        AiOperation.GENERATE_PLANNER,
        None,
        env={"LLM_FAKE_MODE": "1"},
        collapse_reserved_generate=False,
    )
    # With only local planner registered and fake mode, bootstrap may add fake.
    assert resolved.resolution_path != "schedule"


def test_schedule_path_when_enabled(monkeypatch) -> None:
    _register_abc(monkeypatch)
    env = {
        "AI_SCHEDULING_ENABLED": "1",
        "AI_SCHEDULING_DEFAULT_MODE": "prefer_local",
        "AI_SCHEDULING_ALLOW_PUBLIC_CLOUD": "0",
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
    assert resolved.schedule_policy == "prefer_local"
    assert resolved.schedule_attempt == 1


def test_explicit_model_id_bypasses_schedule(monkeypatch) -> None:
    _register_abc(monkeypatch)
    env = {"AI_SCHEDULING_ENABLED": "1"}
    resolved = resolve_model_for_operation(
        AiOperation.GENERATE_PLANNER,
        {"model_id": "openai:gpt"},
        env=env,
        collapse_reserved_generate=False,
    )
    assert resolved.resolution_path == "explicit"
    assert resolved.resolved_model_id == "openai:gpt"


def test_fallback_after_schedule_exhaustion_does_not_escalate_to_public(monkeypatch) -> None:
    registry_mod.register_model(_public(), overwrite=True)
    monkeypatch.setattr(candidates_mod, "_load_projected_nodes", lambda: {})
    env = {
        "AI_SCHEDULING_ENABLED": "1",
        "AI_SCHEDULING_DEFAULT_MODE": "prefer_local",
        "AI_FALLBACK_GENERATE_PLANNER": "openai:gpt",
    }
    with pytest.raises(ModelUnavailableError):
        resolve_model_for_operation(
            AiOperation.GENERATE_PLANNER,
            None,
            env=env,
            collapse_reserved_generate=False,
        )


def test_reschedule_excludes_failed_node_without_public(monkeypatch) -> None:
    _register_abc(monkeypatch)
    env = {
        "AI_SCHEDULING_ENABLED": "1",
        "AI_SCHEDULING_DEFAULT_MODE": "fastest_available",
        "AI_SCHEDULING_MAX_ATTEMPTS": "2",
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
    assert resolved.resolved_model_id.startswith("node:")

    calls: list[str] = []

    async def _fake_once(current, prompt, **kwargs):
        calls.append(current.resolved_model_id)
        if current.resolved_model_id.startswith("node:"):
            raise ModelUnavailableError("down", code="model_unavailable")
        return "ok-local"

    monkeypatch.setattr("app.ai_runtime.invoke_text._ainvoke_once", _fake_once)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    text = asyncio.run(ainvoke_text_for_resolved(resolved, "prompt", purpose="generate"))
    assert text == "ok-local"
    assert len(calls) == 2
    assert calls[0].startswith("node:")
    assert calls[1] == "local:llama-planner"
    assert "openai:gpt" not in calls


def test_cancel_does_not_reschedule(monkeypatch) -> None:
    _register_abc(monkeypatch)
    resolved = ResolvedModel(
        descriptor=_node_b(),
        operation=AiOperation.GENERATE_PLANNER,
        resolution_path="schedule",
        requested_model_id=None,
        resolved_model_id=_node_b().id,
        schedule_policy="prefer_local",
        schedule_attempt=1,
        schedule_trust_boundary="trusted_lan",
        schedule_node_id="node_bbbbbbbbbbbbbbbb",
    )

    async def _fail(*_a, **_k):
        raise ModelUnavailableError("cancelled", code="operation_cancelled")

    monkeypatch.setattr("app.ai_runtime.invoke_text._ainvoke_once", _fail)
    with pytest.raises(ModelUnavailableError) as exc_info:
        asyncio.run(ainvoke_text_for_resolved(resolved, "x", purpose="generate"))
    assert exc_info.value.code == "operation_cancelled"
