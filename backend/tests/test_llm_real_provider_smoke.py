"""Opt-in real-provider smoke test for staged Composition V1 generation.

Skipped unless explicitly enabled:

    RUN_LLM_SMOKE=1 LLM_SMOKE_PROVIDER=openai ../.venv/bin/python -m pytest tests/test_llm_real_provider_smoke.py

Requires the matching provider API key in the environment. Normal pytest runs never spend credits.
"""

from __future__ import annotations

import logging
import os

import pytest

from app.llm_settings import load_llm_settings
from app.schemas import LLMMusicGenerationRequest
from app.services.composition_validator import validate_composition_integrity
from app.services.llm_music_generator import generate_music_json


logger = logging.getLogger(__name__)


def _smoke_enabled() -> bool:
    return os.getenv("RUN_LLM_SMOKE", "").strip() == "1"


pytestmark = pytest.mark.skipif(
    not _smoke_enabled(),
    reason="Opt-in only: set RUN_LLM_SMOKE=1 (and provider API key) to spend real LLM credits",
)


def test_real_provider_staged_smoke():
    import asyncio

    settings = load_llm_settings()
    if not settings.providers:
        pytest.skip("No LLM providers configured; set OPENAI_API_KEY or DEEPSEEK_API_KEY")

    provider_name = os.getenv("LLM_SMOKE_PROVIDER", settings.default_provider or settings.providers[0].provider)
    provider = next((item for item in settings.providers if item.provider == provider_name), None)
    if provider is None:
        pytest.skip(f"Requested smoke provider '{provider_name}' is not configured")

    request = LLMMusicGenerationRequest.model_validate(
        {
            "selection": {"provider": provider.provider, "model": provider.model},
            "options": {"max_retries": 1, "temperature": 0.2, "timeout_seconds": 120},
            "prompt": {
                "genre": "neo-classical",
                "mood": "melancholic",
                "tempo_min": 76,
                "tempo_max": 84,
                "key": "A minor",
                "time_signature": "4/4",
                "instruments": ["piano", "bass", "strings"],
                "complexity": "simple",
                "duration_bars": 8,
                "sections": [
                    {"type": "intro", "bars": 2},
                    {"type": "verse", "bars": 4},
                    {"type": "outro", "bars": 2},
                ],
                "instructions": "quiet opening, stronger middle, resolved ending",
            },
        }
    )

    logger.info(
        "Starting opt-in LLM smoke test",
        extra={
            "provider": provider.provider,
            "model": provider.model,
            "bars": request.prompt.duration_bars,
        },
    )
    music, warnings, selected = asyncio.run(generate_music_json(request, settings))
    result = validate_composition_integrity(
        music,
        requested_instruments=request.prompt.instruments,
        complexity=request.prompt.complexity,
    )
    logger.info(
        "LLM smoke test completed",
        extra={
            "provider": selected.provider,
            "model": selected.model,
            "bars": music.bar_count,
            "tracks": len(music.tracks),
            "events": sum(len(track.events) for track in music.tracks),
            "validation_ok": result.ok,
            "warning_count": len(warnings),
        },
    )
    assert music.schema_version == "composition.v1"
    assert music.bar_count <= 32
    assert sum(len(track.events) for track in music.tracks) > 0
    assert result.ok
