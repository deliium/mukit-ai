import asyncio
import json

import pytest
from fastapi import HTTPException

from app.llm_settings import LLMProviderSettings, LLMSettings
from app.main import edit_llm_composition_region, generate_llm_music_json, get_llm_models
from app.schemas import CompositionRegionReplacementPatch, LLMCompositionEditRequest, LLMMusicGenerationRequest
from app.services.llm_music_generator import InvalidLLMOutputError
from tests.test_composition_region_patch import _sixteen_bar_composition
from tests.test_llm_composition_editing import _melody_patch_payload


def test_get_llm_models_no_providers(monkeypatch):
    for key in ["OPENAI_API_KEY", "DEEPSEEK_API_KEY", "DEFAULT_LLM_PROVIDER", "LLM_FAKE_MODE"]:
        monkeypatch.delenv(key, raising=False)

    response = asyncio.run(get_llm_models())

    assert response.models == []
    assert response.default_provider is None
    assert response.warnings


def test_generate_llm_music_json_no_provider(monkeypatch):
    for key in ["OPENAI_API_KEY", "DEEPSEEK_API_KEY", "DEFAULT_LLM_PROVIDER", "LLM_FAKE_MODE"]:
        monkeypatch.delenv(key, raising=False)
    request = LLMMusicGenerationRequest.model_validate({"prompt": {"genre": "ambient", "mood": "calm"}})

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(generate_llm_music_json(request))

    assert exc_info.value.status_code == 503


def test_edit_llm_composition_region_no_provider(monkeypatch):
    for key in ["OPENAI_API_KEY", "DEEPSEEK_API_KEY", "DEFAULT_LLM_PROVIDER", "LLM_FAKE_MODE"]:
        monkeypatch.delenv(key, raising=False)
    request = LLMCompositionEditRequest.model_validate(
        {
            "composition": _sixteen_bar_composition().model_dump(mode="json"),
            "edit": {
                "instruction": "make this phrase more dramatic but keep the harmony",
                "selection": {"start_bar": 9, "end_bar": 12, "track_ids": ["melody-1"]},
            },
        }
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(edit_llm_composition_region(request))

    assert exc_info.value.status_code == 503


def test_edit_llm_composition_region_success(monkeypatch):
    request = LLMCompositionEditRequest.model_validate(
        {
            "composition": _sixteen_bar_composition().model_dump(mode="json"),
            "edit": {
                "instruction": "make this phrase more dramatic but keep the harmony",
                "selection": {"start_bar": 9, "end_bar": 12, "track_ids": ["melody-1"]},
            },
            "selection": {"provider": "openai", "model": "test-model"},
        }
    )

    async def fake_edit(_request, _settings):
        from app.services.composition_normalizer import normalize_composition_json

        composition = normalize_composition_json(_sixteen_bar_composition())
        region_patch = CompositionRegionReplacementPatch.model_validate(_melody_patch_payload())
        provider = LLMProviderSettings(provider="openai", model="test-model", api_key="secret", is_default=True)
        return composition, region_patch, [], provider

    monkeypatch.setattr("app.main.edit_composition_region", fake_edit)
    monkeypatch.setattr("app.main.render_musicxml", lambda _composition: ("<score/>", __import__("app.services.composition_projection", fromlist=["empty_projection_report"]).empty_projection_report()))
    monkeypatch.setattr(
        "app.main.load_llm_settings",
        lambda: LLMSettings(
            providers=(LLMProviderSettings(provider="openai", model="test-model", api_key="secret", is_default=True),),
            default_provider="openai",
            request_timeout_seconds=30,
            temperature=0.2,
        ),
    )

    response = asyncio.run(edit_llm_composition_region(request))
    assert response.provider == "openai"
    assert response.model == "test-model"
    assert response.patch.operation == "replace_region"
    assert response.musicxml == "<score/>"
    assert response.composition.schema_version == "composition.v2"


def test_edit_llm_composition_region_invalid_patch_maps_to_502(monkeypatch):
    request = LLMCompositionEditRequest.model_validate(
        {
            "composition": _sixteen_bar_composition().model_dump(mode="json"),
            "edit": {
                "instruction": "make this phrase more dramatic but keep the harmony",
                "selection": {"start_bar": 9, "end_bar": 12, "track_ids": ["melody-1"]},
            },
            "selection": {"provider": "openai", "model": "test-model"},
        }
    )

    async def fake_edit(_request, _settings):
        raise InvalidLLMOutputError("bad patch")

    monkeypatch.setattr("app.main.edit_composition_region", fake_edit)
    monkeypatch.setattr(
        "app.main.load_llm_settings",
        lambda: LLMSettings(
            providers=(LLMProviderSettings(provider="openai", model="test-model", api_key="secret", is_default=True),),
            default_provider="openai",
            request_timeout_seconds=30,
            temperature=0.2,
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(edit_llm_composition_region(request))

    assert exc_info.value.status_code == 502
