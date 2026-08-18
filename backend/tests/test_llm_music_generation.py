import asyncio

import pytest

from app.llm_settings import LLMProviderSettings, LLMSettings, load_llm_settings
from app.schemas import LLMMusicGenerationRequest, LLMMusicJson
from app.services import llm_music_generator
from app.services.llm_music_generator import (
    InvalidLLMOutputError,
    NoLLMProviderConfiguredError,
    UnsupportedLLMProviderError,
    generate_music_json,
)


def test_music_json_schema_validates_example_shape():
    music = LLMMusicJson.model_validate(
        {
            "tempo": 92,
            "key": "C minor",
            "time_signature": "4/4",
            "sections": [{"type": "intro", "bars": 4}],
            "tracks": [{"instrument": "piano", "role": "harmony"}],
            "harmony": [{"bar": 1, "chord": "Cm"}],
            "notes": [
                {"track": 1, "staff": "treble", "bar": 1, "beat": 1, "pitch": "C4", "duration": 1},
                {"track": 1, "staff": "treble", "bar": 1, "beat": 1, "pitch": "Eb4", "duration": 1},
                {"track": 1, "staff": "treble", "bar": 1, "beat": 1, "pitch": "G4", "duration": 1},
                {"track": 1, "staff": "bass", "bar": 1, "beat": 1, "pitch": "C3", "duration": 2},
            ],
        }
    )

    assert music.tempo == 92
    assert music.sections[0].type == "intro"
    assert music.notes[0].pitch == "C4"
    assert music.notes[-1].staff == "bass"


def test_music_json_schema_defaults_notes_to_treble_staff():
    music = LLMMusicJson.model_validate(
        {
            "tempo": 92,
            "key": "C minor",
            "time_signature": "4/4",
            "sections": [{"type": "intro", "bars": 1}],
            "tracks": [{"instrument": "piano", "role": "harmony"}],
            "harmony": [{"bar": 1, "chord": "Cm"}],
            "notes": [{"track": 1, "bar": 1, "beat": 1, "pitch": "C4", "duration": 1}],
        }
    )

    assert music.notes[0].staff == "treble"


def test_music_json_schema_rejects_harmony_outside_sections():
    with pytest.raises(ValueError):
        LLMMusicJson.model_validate(
            {
                "tempo": 92,
                "key": "C minor",
                "time_signature": "4/4",
                "sections": [{"type": "intro", "bars": 1}],
                "tracks": [{"instrument": "piano", "role": "harmony"}],
                "harmony": [{"bar": 2, "chord": "Cm"}],
            }
        )


def test_music_json_schema_rejects_invalid_note_pitch():
    with pytest.raises(ValueError):
        LLMMusicJson.model_validate(
            {
                "tempo": 92,
                "key": "C minor",
                "time_signature": "4/4",
                "sections": [{"type": "intro", "bars": 1}],
                "tracks": [{"instrument": "piano", "role": "harmony"}],
                "harmony": [{"bar": 1, "chord": "Cm"}],
                "notes": [{"track": 1, "bar": 1, "beat": 1, "pitch": "C", "duration": 1}],
            }
        )


def test_music_json_schema_rejects_note_outside_measure():
    with pytest.raises(ValueError):
        LLMMusicJson.model_validate(
            {
                "tempo": 92,
                "key": "C minor",
                "time_signature": "3/4",
                "sections": [{"type": "intro", "bars": 1}],
                "tracks": [{"instrument": "piano", "role": "harmony"}],
                "harmony": [{"bar": 1, "chord": "Cm"}],
                "notes": [{"track": 1, "bar": 1, "beat": 3, "pitch": "C4", "duration": 2}],
            }
        )


def test_generation_no_provider_error():
    request = LLMMusicGenerationRequest.model_validate({"prompt": {"genre": "ambient", "mood": "calm"}})

    with pytest.raises(NoLLMProviderConfiguredError):
        asyncio.run(generate_music_json(request, load_llm_settings({})))


def test_generation_unsupported_provider_error():
    request = LLMMusicGenerationRequest.model_validate(
        {"selection": {"provider": "deepseek"}, "prompt": {"genre": "ambient", "mood": "calm"}}
    )
    settings = load_llm_settings({"OPENAI_API_KEY": "secret"})

    with pytest.raises(UnsupportedLLMProviderError):
        asyncio.run(generate_music_json(request, settings))


def test_generation_success_with_mocked_graph(monkeypatch):
    request = LLMMusicGenerationRequest.model_validate({"prompt": {"genre": "ambient", "mood": "calm"}})
    settings = LLMSettings(
        providers=(LLMProviderSettings(provider="openai", model="test-model", api_key="secret", is_default=True),),
        default_provider="openai",
        request_timeout_seconds=60,
        temperature=0.7,
    )

    class FakeGraph:
        async def ainvoke(self, state):
            return {
                **state,
                "music": LLMMusicJson.model_validate(
                    {
                        "tempo": 100,
                        "key": "C major",
                        "time_signature": "4/4",
                        "sections": [{"type": "intro", "bars": 1}],
                        "tracks": [{"instrument": "piano", "role": "harmony"}],
                        "harmony": [{"bar": 1, "chord": "C"}],
                    }
                ),
            }

    monkeypatch.setattr(llm_music_generator, "_build_generation_graph", lambda: FakeGraph())

    music, warnings, provider = asyncio.run(generate_music_json(request, settings))

    assert music.tempo == 100
    assert warnings == []
    assert provider.provider == "openai"


def test_invalid_llm_output_parser():
    with pytest.raises(InvalidLLMOutputError):
        llm_music_generator._parse_and_validate({"raw_output": "not json"})
