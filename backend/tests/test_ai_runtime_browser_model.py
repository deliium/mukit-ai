"""BrowserModel registry discovery + server exclusion (not server-executable)."""

from __future__ import annotations

import pytest

from app.ai_runtime import registry as registry_mod
from app.ai_runtime.runtimes.browser_model import (
    BROWSER_MODEL_RUNTIME,
    BROWSER_SYMBOLIC_FEATURES_MODEL_ID,
    default_browser_symbolic_features_descriptor,
)
from app.embeddings.errors import EmbeddingModelUnavailableError
from app.services import scheduling_candidates as candidates_mod
from app.services.composition_embedding import _resolve_embedding_model


@pytest.fixture(autouse=True)
def _clear_registry():
    registry_mod.clear_registry_for_tests()
    yield
    registry_mod.clear_registry_for_tests()


def test_bootstrap_lists_browser_symbolic_features() -> None:
    registry_mod.reload_registry(env={"LLM_FAKE_MODE": "1"})
    models = {m.id: m for m in registry_mod.list_models()}
    assert BROWSER_SYMBOLIC_FEATURES_MODEL_ID in models
    descriptor = models[BROWSER_SYMBOLIC_FEATURES_MODEL_ID]
    assert descriptor.runtime == BROWSER_MODEL_RUNTIME
    assert descriptor.primary_capability == "embedding" or str(descriptor.primary_capability) == "embedding"
    assert "path" not in str(descriptor.limits).lower() or "server_executable" in descriptor.limits
    assert descriptor.limits.get("torch_required") is False
    assert descriptor.limits.get("max_note_count") == 20000
    assert descriptor.limits.get("profile_id") == "symbolic.features.v1"
    # Server symbolic features remains registered for HTTP authority.
    assert "local:symbolic-features-v1" in models
    assert models["local:symbolic-features-v1"].runtime == "symbolic_features"


def test_scheduling_candidates_omit_browser_model(monkeypatch) -> None:
    monkeypatch.setattr(candidates_mod, "_load_projected_nodes", lambda: {})
    registry_mod.register_model(default_browser_symbolic_features_descriptor(), overwrite=True)
    from app.ai_runtime.capabilities import ModelCapability
    from app.ai_runtime.operations import AiOperation
    from app.ai_runtime.types import ModelDescriptor, ModelHealth

    registry_mod.register_model(
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
        ),
        overwrite=True,
    )
    built = candidates_mod.build_scheduling_candidates(env={})
    ids = [c.model_id for c in built]
    assert BROWSER_SYMBOLIC_FEATURES_MODEL_ID not in ids
    assert "local:llama" in ids


def test_embed_resolve_refuses_browser_model_id(monkeypatch) -> None:
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    registry_mod.reload_registry(env={"LLM_FAKE_MODE": "1"})
    with pytest.raises(EmbeddingModelUnavailableError) as exc_info:
        _resolve_embedding_model(BROWSER_SYMBOLIC_FEATURES_MODEL_ID)
    details = exc_info.value.details or {}
    assert details.get("runtime") == "browser_model"
    assert details.get("skip_reason") == "browser_model_not_server_executable"


def test_embed_resolve_still_uses_symbolic_features(monkeypatch) -> None:
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    registry_mod.reload_registry(env={"LLM_FAKE_MODE": "1"})
    model = _resolve_embedding_model(None)
    assert model.model_id == "local:symbolic-features-v1"
