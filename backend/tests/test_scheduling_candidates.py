"""Candidate projection trust mapping and local resource hints."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.ai_runtime import registry as registry_mod
from app.ai_runtime.capabilities import ModelCapability
from app.ai_runtime.operations import AiOperation
from app.ai_runtime.types import ModelDescriptor, ModelHealth
from app.scheduling_settings import load_scheduling_settings
from app.services import scheduling_candidates as candidates_mod


@pytest.fixture(autouse=True)
def _clear_registry():
    registry_mod.clear_registry_for_tests()
    yield
    registry_mod.clear_registry_for_tests()


def _register(descriptor: ModelDescriptor) -> None:
    registry_mod.register_model(descriptor, overwrite=True)


def test_execution_node_with_remote_locality_is_trusted_lan(monkeypatch) -> None:
    node = SimpleNamespace(
        node_id="node_aaaaaaaaaaaaaaaa",
        availability="available",
        capabilities=["symbolic_composer", "language_planner"],
        hardware=SimpleNamespace(device_class="dgpu"),
        resources=SimpleNamespace(memory_available_mb=48000, memory_total_mb=65536),
        health=SimpleNamespace(latency_ms=25),
    )
    monkeypatch.setattr(candidates_mod, "_load_projected_nodes", lambda: {node.node_id: node})
    _register(
        ModelDescriptor(
            id="node:aaaaaaaaaaaaaaaa:sym",
            display_name="sym",
            provider="execution_node",
            runtime="execution_node",
            primary_capability=ModelCapability.SYMBOLIC_COMPOSER,
            locality="remote",
            model_version=None,
            supported_operations=(AiOperation.GENERATE_COMPOSER,),
            status="ready",
            health=ModelHealth(status="ready", credentials_present=True),
            limits={"execution_node_id": node.node_id},
        )
    )
    built = candidates_mod.build_scheduling_candidates(env={})
    assert len(built) == 1
    assert built[0].trust_boundary == "trusted_lan"
    assert built[0].secondary_capabilities == ["language_planner"]
    assert built[0].device_class == "dgpu"
    assert built[0].memory_available_mb == 48000
    assert built[0].estimated_latency_ms == 25


def test_local_env_hints_fill_controller_local(monkeypatch) -> None:
    monkeypatch.setattr(candidates_mod, "_load_projected_nodes", lambda: {})
    _register(
        ModelDescriptor(
            id="local:llama",
            display_name="llama",
            provider="local",
            runtime="local_openai_compatible",
            primary_capability=ModelCapability.LANGUAGE_PLANNER,
            locality="local",
            model_version=None,
            supported_operations=(AiOperation.GENERATE_PLANNER,),
            status="ready",
            health=ModelHealth(status="ready", credentials_present=True),
            limits={},
        )
    )
    settings = load_scheduling_settings(
        {
            "AI_SCHEDULING_LOCAL_MEMORY_AVAILABLE_MB": "4096",
            "AI_SCHEDULING_LOCAL_MEMORY_TOTAL_MB": "8192",
            "AI_SCHEDULING_LOCAL_DEVICE_CLASS": "igpu",
            "AI_SCHEDULING_LOCAL_ESTIMATED_LATENCY_MS": "40",
        }
    )
    built = candidates_mod.build_scheduling_candidates(env={}, settings=settings)
    assert len(built) == 1
    assert built[0].trust_boundary == "controller_local"
    assert built[0].memory_available_mb == 4096
    assert built[0].device_class == "igpu"
    assert built[0].estimated_latency_ms == 40


def test_local_hints_do_not_override_descriptor_limits(monkeypatch) -> None:
    monkeypatch.setattr(candidates_mod, "_load_projected_nodes", lambda: {})
    _register(
        ModelDescriptor(
            id="local:llama",
            display_name="llama",
            provider="local",
            runtime="local_openai_compatible",
            primary_capability=ModelCapability.LANGUAGE_PLANNER,
            locality="local",
            model_version=None,
            supported_operations=(AiOperation.GENERATE_PLANNER,),
            status="ready",
            health=ModelHealth(status="ready", credentials_present=True),
            limits={
                "memory_available_mb": 2048,
                "device_class": "cpu",
                "estimated_latency_ms": 10,
            },
        )
    )
    settings = load_scheduling_settings(
        {
            "AI_SCHEDULING_LOCAL_MEMORY_AVAILABLE_MB": "4096",
            "AI_SCHEDULING_LOCAL_DEVICE_CLASS": "igpu",
            "AI_SCHEDULING_LOCAL_ESTIMATED_LATENCY_MS": "40",
        }
    )
    built = candidates_mod.build_scheduling_candidates(env={}, settings=settings)
    assert built[0].memory_available_mb == 2048
    assert built[0].device_class == "cpu"
    assert built[0].estimated_latency_ms == 10


def test_local_hints_never_apply_to_execution_node(monkeypatch) -> None:
    node = SimpleNamespace(
        node_id="node_bbbbbbbbbbbbbbbb",
        availability="available",
        capabilities=["language_planner"],
        hardware=SimpleNamespace(device_class="dgpu"),
        resources=SimpleNamespace(memory_available_mb=1000, memory_total_mb=2000),
        health=SimpleNamespace(latency_ms=12),
    )
    monkeypatch.setattr(candidates_mod, "_load_projected_nodes", lambda: {node.node_id: node})
    _register(
        ModelDescriptor(
            id="node:bbbbbbbbbbbbbbbb:lp",
            display_name="lp",
            provider="execution_node",
            runtime="execution_node",
            primary_capability=ModelCapability.LANGUAGE_PLANNER,
            locality="remote",
            model_version=None,
            supported_operations=(AiOperation.GENERATE_PLANNER,),
            status="ready",
            health=ModelHealth(status="ready", credentials_present=True),
            limits={"execution_node_id": node.node_id},
        )
    )
    settings = load_scheduling_settings(
        {
            "AI_SCHEDULING_LOCAL_MEMORY_AVAILABLE_MB": "99999",
            "AI_SCHEDULING_LOCAL_DEVICE_CLASS": "igpu",
            "AI_SCHEDULING_LOCAL_ESTIMATED_LATENCY_MS": "1",
        }
    )
    built = candidates_mod.build_scheduling_candidates(env={}, settings=settings)
    assert built[0].memory_available_mb == 1000
    assert built[0].device_class == "dgpu"
    assert built[0].estimated_latency_ms == 12
