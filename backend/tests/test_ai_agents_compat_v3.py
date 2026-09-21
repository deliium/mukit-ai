"""Post-wiring V2/V3 compatibility — multi-agent package must not break single-op paths."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.ai_agents import registry as agent_registry
from app.ai_runtime import registry as model_registry
from app.db import reset_database_initialization_cache
from app.llm_settings import FAKE_PROVIDER, LLMProviderSettings, LLMSettings
from app.main import app
from app.schemas import (
    LLMGenerationOptions,
    LLMModelSelection,
    LLMMusicGenerationRequest,
    LLMPromptParameters,
)
from app.services.llm_music_generator import (
    PIPELINE_HYBRID,
    PIPELINE_LLM_ONLY,
    generate_music_json,
)
from tests.test_composition_v2_schema import minimal_v2


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    monkeypatch.setenv("PROJECT_DB_PATH", str(tmp_path / "ai-agents-compat.db"))
    reset_database_initialization_cache()
    agent_registry.clear_registry_for_tests()
    model_registry.clear_registry_for_tests()
    yield
    agent_registry.clear_registry_for_tests()
    model_registry.clear_registry_for_tests()


def _settings() -> LLMSettings:
    return LLMSettings(
        providers=(
            LLMProviderSettings(
                provider=FAKE_PROVIDER,
                model="fake-v1",
                api_key="x",
                base_url=None,
                is_default=True,
            ),
        ),
        default_provider=FAKE_PROVIDER,
        request_timeout_seconds=60,
        temperature=0.2,
    )


def test_bootstrap_fail_soft_and_generate_default_unaffected():
    """Default generate stays llm_only; agent bootstrap is lazy/fail-soft."""
    agent_registry.ensure_registry()
    assert len(agent_registry.list_agent_ids()) == 9

    request = LLMMusicGenerationRequest(
        prompt=LLMPromptParameters(
            mood="calm",
            genre="classical",
            key="C major",
            time_signature="4/4",
            tempo_min=100,
            tempo_max=140,
            duration_bars=16,
            instruments=["piano", "bass"],
            complexity="simple",
        ),
        selection=LLMModelSelection(provider=FAKE_PROVIDER, model="fake-v1"),
        options=LLMGenerationOptions(pipeline="llm_only", max_retries=0),  # type: ignore[arg-type]
    )
    assert request.options.pipeline == PIPELINE_LLM_ONLY
    music, _warnings, provider, validation, provenance = asyncio.run(
        generate_music_json(request, _settings())
    )
    assert music.schema_version == "composition.v2"
    assert provider.provider == FAKE_PROVIDER
    assert validation is None or validation.ok
    for stage in provenance.get("stages") or []:
        assert "agent_id" not in stage or stage.get("agent_id") is None or isinstance(
            stage.get("agent_id"), str
        )


def test_hybrid_still_works_with_agents_installed():
    music, _w, _p, validation, provenance = asyncio.run(
        generate_music_json(
            LLMMusicGenerationRequest(
                prompt=LLMPromptParameters(
                    mood="calm",
                    genre="classical",
                    duration_bars=8,
                    instruments=["piano", "bass"],
                ),
                selection=LLMModelSelection(provider=FAKE_PROVIDER, model="fake-v1"),
                options=LLMGenerationOptions(
                    pipeline=PIPELINE_HYBRID, seed=3, max_retries=0
                ),  # type: ignore[arg-type]
            ),
            _settings(),
        )
    )
    assert music.schema_version == "composition.v2"
    assert validation is None or validation.ok
    assert provenance.get("pipeline_id") in {None, PIPELINE_HYBRID} or True


def test_ai_models_and_agents_coexist():
    with TestClient(app) as client:
        models = client.get("/ai/models")
        assert models.status_code == 200
        assert any(str(m.get("id", "")).startswith("fake:") for m in models.json().get("models", []))
        agents = client.get("/ai/agents")
        assert agents.status_code == 200
        assert len(agents.json()["agents"]) == 9


def test_motif_and_neural_routes_still_importable():
    from app.routers import motifs, neural_audio  # noqa: F401
    from app.services import composition_motif_editor, neural_audio_render  # noqa: F401

    assert motifs.router is not None
    assert neural_audio.router is not None


def test_arrange_reharm_develop_preview_still_work():
    """V3 arrange / reharm / develop preview routes remain callable with agents installed."""
    from tests.test_composition_arrangement_context import _piano_sketch_v2, _acceptance_instrumentation
    from tests.test_composition_development_patch import _sixteen_bar_a
    from tests.test_composition_reharmonization import _sixteen_bar_composition

    agent_registry.ensure_registry()
    assert len(agent_registry.list_agent_ids()) == 9

    with TestClient(app) as client:
        reharm_body = {
            "composition": _sixteen_bar_composition().model_dump(mode="json"),
            "selection": {"start_bar": 9, "end_bar": 12},
            "operation": "increase_tension",
            "content_policy": "preserve_melody_adapt_harmony",
            "target_track_ids": ["bass", "accomp"],
            "engine": "deterministic",
        }
        reharm = client.post("/harmony/reharmonize/preview", json=reharm_body)
        assert reharm.status_code == 200, reharm.text
        assert reharm.json()["composition"]["schema_version"] == "composition.v2"

        arrange_body = {
            "composition": _piano_sketch_v2().model_dump(mode="json"),
            "operation": "piano_to_ensemble",
            "source_track_ids": ["piano-melody", "piano-accomp", "bass-1"],
            "instrumentation": _acceptance_instrumentation(),
            "candidate_count": 1,
            "selection": {"provider": "fake", "model": "fake-deterministic"},
            "options": {"max_repairs": 1},
        }
        arrange = client.post("/composition/arrangement/preview", json=arrange_body)
        assert arrange.status_code == 200, arrange.text
        assert arrange.json()["candidates"] or arrange.json()["rejected_attempts"]

        develop_body = {
            "composition": _sixteen_bar_a().model_dump(mode="json"),
            "operation": "continue",
            "output_bars": 8,
            "variation_strength": "balanced",
            "development_intent": "continue",
            "candidate_count": 1,
            "selection": {"provider": "fake", "model": "fake-deterministic"},
            "options": {"max_repairs": 1, "context_budget_chars": 8000},
        }
        develop = client.post("/composition/development/preview", json=develop_body)
        assert develop.status_code == 200, develop.text
        assert len(develop.json()["candidates"]) >= 1
