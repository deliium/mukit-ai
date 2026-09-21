"""Tests for GET /ai/models discovery routes."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from app.ai_runtime import registry as registry_mod
from app.routers.ai_models import get_ai_model, list_ai_models


@pytest.fixture(autouse=True)
def _clear_registry():
    registry_mod.clear_registry_for_tests()
    yield
    registry_mod.clear_registry_for_tests()


def test_list_ai_models_includes_stubs_and_no_secrets(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    registry_mod.reload_registry(dict(**{k: v for k, v in __import__("os").environ.items()}))

    response = asyncio.run(list_ai_models())
    payload = response.model_dump()
    text = str(payload).lower()
    assert "api_key" not in text
    assert "sk-" not in text
    assert any(m.id.startswith("fake:") for m in response.models)
    assert any(m.id == "local:embedding-stub" for m in response.models)
    assert response.default_model_id
    assert "generate" in response.operation_defaults


def test_list_ai_models_filter_by_capability(monkeypatch):
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    registry_mod.reload_registry(dict(**{k: v for k, v in __import__("os").environ.items()}))
    response = asyncio.run(list_ai_models(capability="embedding"))
    assert len(response.models) >= 1
    assert all(m.primary_capability == "embedding" for m in response.models)


def test_get_ai_model_detail(monkeypatch):
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    registry_mod.reload_registry(dict(**{k: v for k, v in __import__("os").environ.items()}))
    item = asyncio.run(get_ai_model("fake:fake-deterministic"))
    assert item.id == "fake:fake-deterministic"
    assert item.runtime == "fake"


def test_get_ai_model_missing(monkeypatch):
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    registry_mod.reload_registry(dict(**{k: v for k, v in __import__("os").environ.items()}))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(get_ai_model("missing:model"))
    assert exc.value.status_code == 404
