import asyncio

import pytest
from fastapi import HTTPException

from app.main import generate_llm_music_json, get_llm_models
from app.schemas import LLMMusicGenerationRequest


def test_get_llm_models_no_providers(monkeypatch):
    for key in ["OPENAI_API_KEY", "DEEPSEEK_API_KEY", "DEFAULT_LLM_PROVIDER"]:
        monkeypatch.delenv(key, raising=False)

    response = asyncio.run(get_llm_models())

    assert response.models == []
    assert response.default_provider is None
    assert response.warnings


def test_generate_llm_music_json_no_provider(monkeypatch):
    for key in ["OPENAI_API_KEY", "DEEPSEEK_API_KEY", "DEFAULT_LLM_PROVIDER"]:
        monkeypatch.delenv(key, raising=False)
    request = LLMMusicGenerationRequest.model_validate({"prompt": {"genre": "ambient", "mood": "calm"}})

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(generate_llm_music_json(request))

    assert exc_info.value.status_code == 503
