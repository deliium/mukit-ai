"""Invoke seam routes execution-node models without ChatOpenAI."""

from __future__ import annotations

import asyncio

import pytest

from app.ai_runtime.capabilities import ModelCapability
from app.ai_runtime.invoke_text import ainvoke_text_for_resolved
from app.ai_runtime.operations import AiOperation
from app.ai_runtime.registry import clear_registry_for_tests, register_model, reload_registry
from app.ai_runtime.routing import resolve_model_for_operation, set_current_resolved_model
from app.ai_runtime.types import ModelDescriptor, ModelHealth, ResolvedModel
from app.db import initialize_database, reset_database_initialization_cache
from app.services import execution_node_runtime as live
from app.services import execution_node_tasks as tasks
from app.services.execution_node_fake import ensure_fake_peer_registered, reset_fake_peer_state


@pytest.fixture
def fake_env(tmp_path, monkeypatch):
    path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(path))
    monkeypatch.setenv("AI_EXECUTION_NODES_ENABLED", "1")
    monkeypatch.setenv("AI_EXECUTION_NODE_TOKEN", "invoke-token-fixture")
    monkeypatch.setenv("AI_EXECUTION_NODE_ROLE", "both")
    monkeypatch.setenv("AI_EXECUTION_NODE_FAKE", "1")
    monkeypatch.delenv("AI_EXECUTION_NODE_FAKE_SLOW", raising=False)
    reset_database_initialization_cache()
    initialize_database()
    live.clear_live_state()
    tasks.clear_tasks()
    reset_fake_peer_state()
    clear_registry_for_tests()
    ensure_fake_peer_registered(db_path=path)
    reload_registry()
    yield path
    clear_registry_for_tests()
    reset_fake_peer_state()


def test_ainvoke_text_for_resolved_execution_node(fake_env) -> None:
    resolved = resolve_model_for_operation(
        AiOperation.GENERATE,
        {"model_id": "node:0123456789abcdef:fake:language"},
    )
    assert resolved.descriptor.runtime == "execution_node"
    set_current_resolved_model(resolved)
    text = asyncio.run(ainvoke_text_for_resolved(resolved, "compose", purpose="generate"))
    assert text == "fake-execution-node:generate"


def test_invoke_seam_without_chatopenai(monkeypatch, fake_env) -> None:
    def _boom(*_args, **_kwargs):
        raise AssertionError("build_chat_openai must not run for execution_node")

    monkeypatch.setattr("app.services.llm_chat_client.build_chat_openai", _boom)
    monkeypatch.setattr("app.ai_runtime.invoke_text.build_chat_openai", _boom)
    descriptor = ModelDescriptor(
        id="node:0123456789abcdef:fake:language",
        display_name="Fake remote",
        provider="execution_node",
        runtime="execution_node",
        primary_capability=ModelCapability.LANGUAGE_PLANNER,
        locality="remote",
        model_version=None,
        supported_operations=(AiOperation.GENERATE,),
        status="ready",
        health=ModelHealth(status="ready", credentials_present=True),
        limits={
            "execution_node_id": "node_0123456789abcdef",
            "execution_node_address": "http://execution-node.fake",
        },
        provider_model="fake:language",
    )
    register_model(descriptor, overwrite=True)
    resolved = ResolvedModel(
        descriptor=descriptor,
        operation=AiOperation.GENERATE,
        resolution_path="explicit",
        requested_model_id=descriptor.id,
        resolved_model_id=descriptor.id,
    )
    text = asyncio.run(ainvoke_text_for_resolved(resolved, "x", purpose="music_generation"))
    assert text.startswith("fake-execution-node:")
