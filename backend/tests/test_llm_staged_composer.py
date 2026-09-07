import asyncio
import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.llm_settings import LLMProviderSettings, LLMSettings
from app.main import app
from app.schemas import LLMMusicGenerationRequest
from app.services import llm_music_generator
from app.services.composition_planner import ComposerDraftNote, OversizedLLMGenerationRequestError
from app.services.llm_music_generator import (
    InvalidLLMOutputError,
    UnsupportedLLMProviderError,
    generate_music_json,
)


TICKS = 480
BAR_TICKS = 1920


def _settings(provider: str = "openai", model: str = "test-model") -> LLMSettings:
    return LLMSettings(
        providers=(
            LLMProviderSettings(
                provider=provider,
                model=model,
                api_key="secret",
                base_url="https://example.test/v1" if provider == "deepseek" else None,
                is_default=True,
            ),
        ),
        default_provider=provider,
        request_timeout_seconds=60,
        temperature=0.2,
    )


def _request(**prompt_overrides) -> LLMMusicGenerationRequest:
    prompt = {
        "genre": "neo-classical",
        "mood": "melancholic",
        "tempo_min": 76,
        "tempo_max": 84,
        "key": "A minor",
        "time_signature": "4/4",
        "instruments": ["piano", "bass", "strings"],
        "sections": [
            {"type": "intro", "bars": 4},
            {"type": "verse", "bars": 8},
            {"type": "outro", "bars": 4},
        ],
        "complexity": "moderate",
        "duration_bars": 16,
        "instructions": "quiet opening, stronger middle, resolved ending",
    }
    prompt.update(prompt_overrides)
    return LLMMusicGenerationRequest.model_validate({"prompt": prompt, "options": {"max_retries": 1}})


def _events_every_bar(pitch: str, bars: int = 16, velocity: int = 80, staff: str | None = None) -> list[dict]:
    events = []
    for bar in range(bars):
        event = {
            "pitch": pitch,
            "start_tick": bar * BAR_TICKS,
            "duration_ticks": TICKS,
            "velocity": velocity,
        }
        if staff:
            event["staff"] = staff
        events.append(event)
    return events


def _stage_payloads(
    *,
    omit_melody: bool = False,
    bad_first_melody: bool = False,
    invalid_pitch: str | None = None,
    overflow_melody: bool = False,
    slight_overflow_melody: bool = False,
    duplicate_track_ids: bool = False,
) -> dict[str, str]:
    form = {
        "tempo": 80,
        "key": "A minor",
        "time_signature": "4/4",
        "bar_count": 16,
        "sections": [
            {"type": "intro", "start_bar": 1, "bar_count": 4, "intensity": "quiet"},
            {"type": "verse", "start_bar": 5, "bar_count": 8, "intensity": "stronger"},
            {"type": "outro", "start_bar": 13, "bar_count": 4, "intensity": "resolved"},
        ],
        "instrumentation": ["piano", "bass", "strings"],
    }
    harmony = {
        "events": [
            {"bar": 1, "chord": "Am", "section_type": "intro", "function": "tonic"},
            {"bar": 5, "chord": "Dm", "section_type": "verse", "function": "subdominant"},
            {"bar": 12, "chord": "E7", "section_type": "verse", "cadence": "half"},
            {"bar": 13, "chord": "Am", "section_type": "outro", "cadence": "authentic"},
        ]
    }
    melody_events = [] if omit_melody else _events_every_bar("A4", staff="treble")
    if bad_first_melody:
        melody_events = _events_every_bar("A4", bars=2, staff="treble")
    if invalid_pitch:
        melody_events = [
            {
                "pitch": invalid_pitch,
                "start_tick": 0,
                "duration_ticks": TICKS,
                "velocity": 80,
                "staff": "treble",
            }
        ]
    if overflow_melody:
        melody_events = [
            {
                "pitch": "A4",
                "start_tick": 16 * BAR_TICKS,
                "duration_ticks": TICKS,
                "velocity": 80,
                "staff": "treble",
            }
        ]
    if slight_overflow_melody:
        # Keep in-bounds notes and one note that ends past composition duration.
        melody_events = _events_every_bar("A4", bars=15, staff="treble") + [
            {
                "pitch": "A4",
                "start_tick": 15 * BAR_TICKS,
                "duration_ticks": BAR_TICKS + TICKS,
                "velocity": 80,
                "staff": "treble",
            }
        ]
    melody = {
        "track": {
            "id": "melody-1",
            "name": "Piano Melody",
            "instrument": "piano",
            "role": "melody",
            "staff": "treble",
            "events": melody_events,
        },
        "motif_context": {
            "motif_ids": ["m1"],
            "interval_cells": ["0,+2,-1"],
            "rhythm_cells": ["1,1,2"],
            "section_notes": ["intro states motif"],
            "handoff": "sequence into outro",
        },
    }
    bass = {
        "track": {
            "id": "bass-1",
            "name": "Bass",
            "instrument": "bass",
            "role": "bass",
            "events": _events_every_bar("A2", velocity=84),
        }
    }
    accompaniment_tracks = [
        {
            "id": "harmony-1",
            "name": "Piano Accompaniment",
            "instrument": "piano",
            "role": "harmony",
            "staff": "grand",
            "events": _events_every_bar("A3", velocity=70, staff="bass")
            + _events_every_bar("C5", velocity=68, staff="treble"),
        },
        {
            "id": "strings-1",
            "name": "Strings",
            "instrument": "strings",
            "role": "pad",
            "events": _events_every_bar("E4", velocity=60),
        },
    ]
    if duplicate_track_ids:
        # Collide with melody and within accompaniment list.
        accompaniment_tracks[0]["id"] = "melody-1"
        accompaniment_tracks[1]["id"] = "melody-1"
    accompaniment = {
        "tracks": accompaniment_tracks,
        "skipped": [],
    }
    return {
        "plan_form": json.dumps(form),
        "plan_harmony": json.dumps(harmony),
        "compose_melody": json.dumps(melody),
        "compose_bass": json.dumps(bass),
        "compose_accompaniment": json.dumps(accompaniment),
    }


def _install_stage_mock(
    monkeypatch,
    payloads: dict[str, str],
    *,
    fail_melody_once: bool = False,
    fail_accompaniment_parse_once: bool = False,
):
    calls: list[str] = []
    melody_attempts = {"count": 0}
    accompaniment_attempts = {"count": 0}

    async def fake_invoke(state, prompt: str) -> str:
        stage = state.get("current_stage") or state.get("repair_target") or "plan_form"
        # During stage execution current_stage may still be previous; infer from prompt markers.
        for name in (
            "plan_form",
            "plan_harmony",
            "compose_melody",
            "compose_bass",
            "compose_accompaniment",
        ):
            if f"Composer stage" in prompt:
                break
        # Prefer explicit stage from _run_json_stage which sets logging before invoke with state's current_stage.
        # _run_json_stage does not set current_stage before invoke; detect via prompt content.
        if "planning musical form" in prompt:
            stage = "plan_form"
        elif "harmonic progression metadata" in prompt:
            stage = "plan_harmony"
        elif "primary melody track" in prompt:
            stage = "compose_melody"
        elif "composing the bass track" in prompt:
            stage = "compose_bass"
        elif "accompaniment / harmonic support" in prompt:
            stage = "compose_accompaniment"

        calls.append(stage)
        if stage == "compose_accompaniment" and fail_accompaniment_parse_once:
            accompaniment_attempts["count"] += 1
            if accompaniment_attempts["count"] == 1:
                return "{not-valid-json"
        if stage == "compose_melody" and fail_melody_once:
            # When combined with accompaniment parse failure, inject sparse melody only on the
            # restarted graph so integrity repair still has budget left.
            if fail_accompaniment_parse_once and accompaniment_attempts["count"] == 0:
                return payloads[stage]
            melody_attempts["count"] += 1
            if melody_attempts["count"] == 1:
                return json.dumps(
                    {
                        "track": {
                            "id": "melody-1",
                            "name": "Melody",
                            "instrument": "piano",
                            "role": "melody",
                            "events": _events_every_bar("A4", bars=2),
                        },
                        "motif_context": {},
                    }
                )
            return _stage_payloads()["compose_melody"]
        return payloads[stage]

    monkeypatch.setattr(llm_music_generator, "_invoke_chat", fake_invoke)
    return calls


def test_staged_generation_acceptance_shape(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    payloads = _stage_payloads()
    calls = _install_stage_mock(monkeypatch, payloads)

    music, warnings, provider = asyncio.run(generate_music_json(_request(), _settings()))

    assert provider.provider == "openai"
    assert music.schema_version == "composition.v1"
    assert music.bar_count == 16
    assert music.key == "A minor"
    roles = {track.role for track in music.tracks}
    assert {"melody", "bass", "harmony"}.issubset(roles)
    assert any(track.instrument == "strings" for track in music.tracks)
    assert all(len(track.events) > 0 for track in music.tracks if track.role in {"melody", "bass", "harmony"})
    assert music.harmony
    assert calls[:5] == [
        "plan_form",
        "plan_harmony",
        "compose_melody",
        "compose_bass",
        "compose_accompaniment",
    ]
    assert "Composer stage started" in caplog.text
    assert "Building staged LLM composition generation graph" in caplog.text


def test_staged_generation_preserves_model_override(monkeypatch):
    payloads = _stage_payloads()
    _install_stage_mock(monkeypatch, payloads)
    request = _request()
    request = LLMMusicGenerationRequest.model_validate(
        {
            **request.model_dump(),
            "selection": {"provider": "openai", "model": "gpt-override"},
        }
    )
    settings = _settings(model="base-model")
    music, warnings, provider = asyncio.run(generate_music_json(request, settings))
    assert music.schema_version == "composition.v1"
    assert provider.model == "gpt-override"


def test_staged_generation_deepseek_provider(monkeypatch):
    payloads = _stage_payloads()
    _install_stage_mock(monkeypatch, payloads)
    request = LLMMusicGenerationRequest.model_validate(
        {
            **_request().model_dump(),
            "selection": {"provider": "deepseek", "model": "deepseek-chat"},
        }
    )
    music, warnings, provider = asyncio.run(generate_music_json(request, _settings("deepseek", "deepseek-chat")))
    assert provider.provider == "deepseek"
    assert music.bar_count == 16


def test_staged_generation_unsupported_provider():
    request = LLMMusicGenerationRequest.model_validate(
        {
            **_request().model_dump(),
            "selection": {"provider": "deepseek"},
        }
    )
    with pytest.raises(UnsupportedLLMProviderError):
        asyncio.run(generate_music_json(request, _settings("openai")))


def test_staged_generation_oversized_request_rejected():
    request = _request(duration_bars=48, instruments=["piano", "bass", "strings", "flute", "guitar", "synth", "violin"])
    with pytest.raises(OversizedLLMGenerationRequestError, match="32 bars"):
        asyncio.run(generate_music_json(request, _settings()))


def test_staged_generation_repairs_sparse_melody(monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    payloads = _stage_payloads()
    _install_stage_mock(monkeypatch, payloads, fail_melody_once=True)
    music, warnings, provider = asyncio.run(generate_music_json(_request(), _settings()))
    assert music.schema_version == "composition.v1"
    assert any("retrying" in warning.lower() or "failed validation" in warning.lower() for warning in warnings)
    assert "Composer repair attempt started" in caplog.text


def test_stage_parse_retry_preserves_integrity_repair_budget(monkeypatch, caplog):
    """Accompaniment parse failure must not burn the integrity repair retry_count."""
    caplog.set_level(logging.INFO)
    payloads = _stage_payloads()
    _install_stage_mock(
        monkeypatch,
        payloads,
        fail_accompaniment_parse_once=True,
        fail_melody_once=True,
    )
    request = LLMMusicGenerationRequest.model_validate(
        {**_request().model_dump(), "options": {"max_retries": 1}}
    )
    music, warnings, provider = asyncio.run(generate_music_json(request, _settings()))
    assert music.schema_version == "composition.v1"
    assert "[FIX] Preserved integrity repair budget after stage parse retry" in caplog.text
    assert "Composer repair attempt started" in caplog.text
    assert "Repair budget exhausted after validation failure" not in caplog.text
    assert provider.provider == "openai"


def test_staged_generation_repair_exhaustion(monkeypatch):
    payloads = _stage_payloads(omit_melody=True)
    _install_stage_mock(monkeypatch, payloads)
    request = LLMMusicGenerationRequest.model_validate(
        {**_request().model_dump(), "options": {"max_retries": 0}}
    )
    with pytest.raises(InvalidLLMOutputError, match="non-playable|empty_required_track|missing"):
        asyncio.run(generate_music_json(request, _settings()))


def test_api_oversized_request_returns_422():
    client = TestClient(app)
    response = client.post(
        "/llm/generate-music-json",
        json={
            "prompt": {
                "genre": "ambient",
                "mood": "calm",
                "duration_bars": 64,
                "instruments": ["piano"],
            }
        },
    )
    assert response.status_code == 422
    assert "32 bars" in response.json()["detail"]


def test_api_invalid_output_returns_actionable_502(monkeypatch):
    client = TestClient(app)
    payloads = _stage_payloads(omit_melody=True)
    _install_stage_mock(monkeypatch, payloads)

    async def fake_generate_async(request, settings=None):
        return await generate_music_json(
            LLMMusicGenerationRequest.model_validate(
                {**request.model_dump(), "options": {"max_retries": 0}}
            ),
            _settings(),
        )

    monkeypatch.setattr("app.main.generate_music_json", fake_generate_async)
    response = client.post(
        "/llm/generate-music-json",
        json={
            "prompt": {
                "genre": "ambient",
                "mood": "calm",
                "duration_bars": 16,
                "instruments": ["piano", "bass", "strings"],
                "complexity": "moderate",
            },
            "options": {"max_retries": 0},
        },
    )
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert isinstance(detail, str) and detail
    assert "empty_required_track" in detail or "non-playable" in detail or "missing" in detail.lower()


@pytest.mark.parametrize("pitch", ["C4", "F#3", "Bb2"])
def test_composer_draft_note_accepts_scientific_pitch(pitch):
    note = ComposerDraftNote.model_validate(
        {"pitch": pitch, "start_tick": 0, "duration_ticks": TICKS, "velocity": 80}
    )
    assert note.pitch == pitch


@pytest.mark.parametrize("pitch", ["H4", "C", "X9"])
def test_composer_draft_note_rejects_invalid_scientific_pitch(pitch):
    with pytest.raises(Exception):
        ComposerDraftNote.model_validate(
            {"pitch": pitch, "start_tick": 0, "duration_ticks": TICKS, "velocity": 80}
        )


def test_invalid_draft_pitch_is_not_provider_failure(monkeypatch, caplog):
    caplog.set_level(logging.ERROR)
    payloads = _stage_payloads(invalid_pitch="H4")
    _install_stage_mock(monkeypatch, payloads)
    request = LLMMusicGenerationRequest.model_validate(
        {**_request().model_dump(), "options": {"max_retries": 0}}
    )
    with pytest.raises(InvalidLLMOutputError) as exc_info:
        asyncio.run(generate_music_json(request, _settings()))
    message = str(exc_info.value)
    assert type(exc_info.value) is InvalidLLMOutputError
    assert "LLM provider request failed" not in message
    assert "invalid JSON" in message.lower() or "scientific" in message.lower() or "pitch" in message.lower()
    assert "LLM provider/API failure" not in caplog.text


def test_assemble_clamps_slightly_overflowing_events(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    payloads = _stage_payloads(slight_overflow_melody=True)
    _install_stage_mock(monkeypatch, payloads)
    music, warnings, provider = asyncio.run(generate_music_json(_request(), _settings()))
    melody = next(track for track in music.tracks if track.role == "melody")
    assert melody.events
    assert all(event.start_tick + event.duration_ticks <= music.duration_ticks for event in melody.events)
    last = max(melody.events, key=lambda event: event.start_tick + event.duration_ticks)
    assert last.start_tick + last.duration_ticks == music.duration_ticks
    assert "[FIX] Clamped assemble events to composition duration" in caplog.text
    assert "Track events must fit within the composition duration" not in caplog.text
    assert provider.provider == "openai"


def test_past_end_only_melody_is_not_provider_failure(monkeypatch, caplog):
    caplog.set_level(logging.ERROR)
    payloads = _stage_payloads(overflow_melody=True)
    _install_stage_mock(monkeypatch, payloads)
    request = LLMMusicGenerationRequest.model_validate(
        {**_request().model_dump(), "options": {"max_retries": 0}}
    )
    with pytest.raises(InvalidLLMOutputError) as exc_info:
        asyncio.run(generate_music_json(request, _settings()))
    message = str(exc_info.value)
    assert type(exc_info.value) is InvalidLLMOutputError
    assert "LLM provider request failed" not in message
    assert "LLM provider/API failure" not in caplog.text
    # Past-end-only note is dropped at assemble; integrity then rejects empty melody.
    assert "empty_required_track" in message or "sparse" in message.lower() or "non-playable" in message


def test_assemble_remaps_duplicate_track_ids(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    payloads = _stage_payloads(duplicate_track_ids=True)
    _install_stage_mock(monkeypatch, payloads)
    music, warnings, provider = asyncio.run(generate_music_json(_request(), _settings()))
    track_ids = [track.id for track in music.tracks]
    assert len(track_ids) == len(set(track_ids))
    assert "melody-1" in track_ids
    assert any(track.role == "harmony" for track in music.tracks)
    assert any(track.role == "pad" for track in music.tracks)
    assert "[FIX] Remapped duplicate assemble track id" in caplog.text
    assert "Track IDs must be unique" not in caplog.text
    assert provider.provider == "openai"
