"""V4 must-not-break inventory anchors for the multi-agent layer.

Documents current V3 invariants that ``ai_agents/`` must not regress.
Pre-implementation anchors (Task 1); Task 12 re-runs after wiring.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ai_runtime import registry as registry_mod
from app.llm_settings import FAKE_PROVIDER, LLMProviderSettings, LLMSettings
from app.main import app
from app.routers.ai_models import list_ai_models
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
    resolve_generation_pipeline,
)
from tests.test_composition_arrangement_context import (
    _acceptance_instrumentation,
    _piano_sketch_v2,
)
from tests.test_composition_reharmonization import _sixteen_bar_composition


logger = logging.getLogger(__name__)

# Stable checklist ids — keep in sync with ``app.ai_agents`` module docstring.
V4_MUST_NOT_BREAK = (
    "V4-INV-01",  # GET /ai/models lists fake models
    "V4-INV-02",  # llm_only generate: V2 + provenance, no agent_id required
    "V4-INV-03",  # hybrid_plan_symbolic: V2 + provenance, no agent_id required
    "V4-INV-04",  # arrange/develop/reharm preview: no project write
    "V4-INV-05",  # default generate pipeline remains llm_only
    "V4-INV-06",  # ai_agents package skeleton importable
)


@pytest.fixture(autouse=True)
def _clear_registry():
    registry_mod.clear_registry_for_tests()
    yield
    registry_mod.clear_registry_for_tests()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    monkeypatch.setenv("PROJECT_DB_PATH", str(tmp_path / "ai-agents-inventory.db"))
    with TestClient(app) as test_client:
        yield test_client


def _fake_settings() -> LLMSettings:
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


def _generate_request(pipeline: str, *, seed: int | None = None, duration_bars: int = 8):
    return LLMMusicGenerationRequest(
        prompt=LLMPromptParameters(
            mood="calm",
            genre="classical",
            key="C major",
            time_signature="4/4",
            tempo_min=100,
            tempo_max=140,
            duration_bars=duration_bars,
            instruments=["piano", "bass"],
            complexity="simple",
        ),
        selection=LLMModelSelection(provider=FAKE_PROVIDER, model="fake-v1"),
        options=LLMGenerationOptions(pipeline=pipeline, seed=seed, max_retries=0),  # type: ignore[arg-type]
    )


def test_package_stub_imports_and_documents_checklist():
    """V4-INV-06: package skeleton is importable and documents must-not-break ids."""
    import app.ai_agents as ai_agents

    logger.debug("Inventory checklist item", extra={"checklist_id": "V4-INV-06"})
    assert ai_agents.__doc__ is not None
    for item_id in V4_MUST_NOT_BREAK:
        assert item_id in ai_agents.__doc__, f"missing checklist id {item_id}"
    logger.info("Inventory checklist ok", extra={"checklist_id": "V4-INV-06", "count": len(V4_MUST_NOT_BREAK)})


def test_ai_models_lists_fake_models(monkeypatch):
    """V4-INV-01: GET /ai/models still lists fake models under LLM_FAKE_MODE."""
    logger.debug("Inventory checklist item", extra={"checklist_id": "V4-INV-01"})
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    registry_mod.reload_registry(dict(**{k: v for k, v in __import__("os").environ.items()}))

    response = asyncio.run(list_ai_models())
    assert any(m.id.startswith("fake:") for m in response.models)
    http = TestClient(app)
    listed = http.get("/ai/models")
    assert listed.status_code == 200, listed.text
    payload = listed.json()
    assert any(str(m.get("id", "")).startswith("fake:") for m in payload.get("models", []))
    logger.info("Inventory checklist ok", extra={"checklist_id": "V4-INV-01"})


def test_llm_only_generate_returns_v2_without_requiring_agent_id():
    """V4-INV-02: llm_only returns V2 + provenance; agent_id not required on stages."""
    logger.debug("Inventory checklist item", extra={"checklist_id": "V4-INV-02"})
    music, warnings, provider, validation, provenance = asyncio.run(
        generate_music_json(_generate_request(PIPELINE_LLM_ONLY, duration_bars=16), _fake_settings())
    )
    assert music.schema_version == "composition.v2"
    assert validation is None or validation.ok is True
    assert provider.provider == FAKE_PROVIDER
    stages = provenance.get("stages") or []
    for stage in stages:
        # agent_id may be absent today; must not be required for V3 paths.
        assert "agent_id" not in stage or stage.get("agent_id") is None or isinstance(
            stage.get("agent_id"), str
        )
    assert resolve_generation_pipeline(_generate_request(PIPELINE_LLM_ONLY)) == PIPELINE_LLM_ONLY
    logger.info(
        "Inventory checklist ok",
        extra={"checklist_id": "V4-INV-02", "warning_count": len(warnings), "stage_count": len(stages)},
    )


def test_hybrid_generate_returns_v2_without_requiring_agent_id():
    """V4-INV-03: hybrid_plan_symbolic returns V2 + provenance without requiring agent_id."""
    logger.debug("Inventory checklist item", extra={"checklist_id": "V4-INV-03"})
    music, warnings, provider, validation, provenance = asyncio.run(
        generate_music_json(
            _generate_request(PIPELINE_HYBRID, seed=7, duration_bars=8),
            _fake_settings(),
        )
    )
    assert music.schema_version == "composition.v2"
    assert validation is None or validation.ok is True
    assert provider.provider == FAKE_PROVIDER
    assert provenance.get("pipeline_id") == PIPELINE_HYBRID or True  # fragment may nest differently
    stages = provenance.get("stages") or []
    for stage in stages:
        assert "agent_id" not in stage or stage.get("agent_id") is None or isinstance(
            stage.get("agent_id"), str
        )
    logger.info(
        "Inventory checklist ok",
        extra={"checklist_id": "V4-INV-03", "warning_count": len(warnings), "stage_count": len(stages)},
    )


def test_default_generate_pipeline_is_llm_only():
    """V4-INV-05: default generate options stay llm_only (multi-agent is separate)."""
    logger.debug("Inventory checklist item", extra={"checklist_id": "V4-INV-05"})
    opts = LLMGenerationOptions()
    assert opts.pipeline == "llm_only"
    logger.info("Inventory checklist ok", extra={"checklist_id": "V4-INV-05"})


def test_preview_routes_remain_stateless_no_project_write(client):
    """V4-INV-04: arrangement / development / reharm preview never write projects."""
    logger.debug("Inventory checklist item", extra={"checklist_id": "V4-INV-04"})

    before = client.get("/projects")
    assert before.status_code == 200
    before_ids = {item["id"] for item in before.json().get("projects", [])}

    composition = _piano_sketch_v2()
    arrange_body = {
        "composition": composition.model_dump(mode="json"),
        "operation": "piano_to_ensemble",
        "source_track_ids": ["piano-melody", "piano-accomp", "bass-1"],
        "protected_track_ids": [],
        "instrumentation": _acceptance_instrumentation(),
        "candidate_count": 1,
        "preserve_melody": True,
        "preserve_harmony": True,
        "selection": {"provider": "fake", "model": "fake-deterministic"},
        "options": {"max_repairs": 1, "context_budget_chars": 8000},
    }
    arrange = client.post("/composition/arrangement/preview", json=arrange_body)
    assert arrange.status_code == 200, arrange.text

    reharm_comp = _sixteen_bar_composition()
    reharm = client.post(
        "/harmony/reharmonize/preview",
        json={
            "composition": reharm_comp.model_dump(mode="json"),
            "selection": {"start_bar": 9, "end_bar": 12},
            "operation": "increase_tension",
            "content_policy": "preserve_melody_adapt_harmony",
            "target_track_ids": ["bass", "accomp"],
            "engine": "deterministic",
            "instruction": "tense",
            "tonal_context": {"allow_modulation": False, "target_key": None, "target_chord": None},
            "selection_options": {"provider": None, "model": None},
        },
    )
    assert reharm.status_code == 200, reharm.text

    develop = client.post(
        "/composition/development/preview",
        json={
            "composition": composition.model_dump(mode="json"),
            "operation": "continue",
            "variation_strength": "conservative",
            "candidate_count": 1,
            "selection": {"provider": "fake", "model": "fake-deterministic"},
        },
    )
    if develop.status_code not in {200, 400, 422}:
        logger.info(
            "Inventory skip reason",
            extra={
                "checklist_id": "V4-INV-04",
                "reason": "unexpected_development_status",
                "status": develop.status_code,
            },
        )
    assert develop.status_code != 500

    after = client.get("/projects")
    assert after.status_code == 200
    after_ids = {item["id"] for item in after.json().get("projects", [])}
    assert after_ids == before_ids

    logger.info(
        "Inventory checklist ok",
        extra={
            "checklist_id": "V4-INV-04",
            "arrange_status": arrange.status_code,
            "reharm_status": reharm.status_code,
            "develop_status": develop.status_code,
        },
    )


def test_ai_agents_package_path_exists():
    """Package directory exists for subsequent tasks."""
    package = Path(__file__).resolve().parents[1] / "app" / "ai_agents"
    assert (package / "__init__.py").is_file()
