"""Tests for AI model registry bootstrap and reload."""

from __future__ import annotations

import json

import pytest

from app.ai_runtime.capabilities import ModelCapability
from app.ai_runtime.errors import ModelNotFoundError
from app.ai_runtime.operations import AiOperation
from app.ai_runtime import registry as registry_mod


@pytest.fixture(autouse=True)
def _clear_registry():
    registry_mod.clear_registry_for_tests()
    yield
    registry_mod.clear_registry_for_tests()


def test_bootstrap_empty_env_registers_stubs_only():
    registry_mod.reload_registry({})
    models = registry_mod.list_models()
    ids = {m.id for m in models}
    assert "local:embedding-stub" in ids
    assert "local:transcription-stub" in ids
    assert "local:audio-generation-stub" in ids
    assert all(m.status == "unconfigured" for m in models if m.runtime == "stub")
    assert registry_mod.get_default_model_id() is None


def test_bootstrap_openai_and_fake_mode(monkeypatch):
    env = {
        "OPENAI_API_KEY": "sk-test",
        "OPENAI_MODEL": "gpt-4o-mini",
        "LLM_FAKE_MODE": "1",
    }
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    registry_mod.reload_registry(env)

    models = registry_mod.get_registry()
    assert "fake:fake-deterministic" in models
    assert "openai:gpt-4o-mini" in models
    fake = models["fake:fake-deterministic"]
    assert fake.runtime == "fake"
    assert fake.primary_capability == ModelCapability.LANGUAGE_PLANNER
    assert AiOperation.GENERATE in fake.supported_operations
    assert AiOperation.ARRANGE_PREVIEW in fake.supported_operations
    assert registry_mod.get_default_model_id() == "fake:fake-deterministic"


def test_reload_registry_picks_up_monkeypatch_env(monkeypatch):
    registry_mod.reload_registry({})
    assert "openai:gpt-4o-mini" not in registry_mod.get_registry()

    monkeypatch.setenv("OPENAI_API_KEY", "sk-new")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")
    registry_mod.reload_registry(dict(**{k: v for k, v in __import__("os").environ.items()}))

    assert "openai:gpt-4o-mini" in registry_mod.get_registry()
    openai = registry_mod.get_model("openai:gpt-4o-mini")
    assert openai.locality == "remote"
    assert openai.health.credentials_present is True


def test_filter_by_capability_and_operation():
    registry_mod.reload_registry(
        {"OPENAI_API_KEY": "sk", "OPENAI_MODEL": "gpt-4o-mini"}
    )
    language = registry_mod.list_models(capability=ModelCapability.LANGUAGE_PLANNER)
    assert any(m.id == "openai:gpt-4o-mini" for m in language)

    arrange = registry_mod.list_models(operation=AiOperation.ARRANGE_PREVIEW)
    assert any(m.id == "openai:gpt-4o-mini" for m in arrange)

    embed = registry_mod.list_models(capability=ModelCapability.EMBEDDING)
    assert len(embed) >= 2
    by_id = {m.id: m for m in embed}
    assert by_id["local:embedding-stub"].status == "unconfigured"
    assert by_id["local:symbolic-features-v1"].status == "ready"
    assert by_id["local:symbolic-features-v1"].runtime == "symbolic_features"


def test_get_model_missing_raises():
    registry_mod.reload_registry({})
    with pytest.raises(ModelNotFoundError) as exc:
        registry_mod.get_model("missing:model")
    assert exc.value.code == "model_not_found"


def test_optional_registry_file(tmp_path):
    path = tmp_path / "extra_models.json"
    path.write_text(
        json.dumps(
            {
                "models": [
                    {
                        "id": "local:custom-embed",
                        "display_name": "Custom Embed",
                        "primary_capability": "embedding",
                        "supported_operations": ["embed"],
                        "runtime": "stub",
                        "locality": "local",
                        "status": "unconfigured",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    registry_mod.reload_registry({"AI_MODEL_REGISTRY_PATH": str(path)})
    assert "local:custom-embed" in registry_mod.get_registry()
