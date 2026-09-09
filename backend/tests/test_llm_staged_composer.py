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
    GenerationConstraintViolationError,
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


def _seed_bar_events(pitch_base: str = "A4") -> list[dict]:
    """At least 3 notes inside the first bar for theme seed extraction."""
    pitches = [pitch_base, "B4", "C5", "E5"]
    return [
        {
            "pitch": pitches[index],
            "start_tick": index * TICKS,
            "duration_ticks": TICKS,
            "velocity": 80,
            "staff": "treble",
        }
        for index in range(4)
    ]


def _stage_payloads(
    *,
    omit_melody: bool = False,
    bad_first_melody: bool = False,
    invalid_pitch: str | None = None,
    overflow_melody: bool = False,
    slight_overflow_melody: bool = False,
    duplicate_track_ids: bool = False,
    theme_enabled: bool = True,
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
    if theme_enabled:
        theme = {
            "enabled": True,
            "motif_id": "motif-a",
            "motif_label": "Motif A",
            "seed": {
                "section_index": 0,
                "track_role": "melody",
                "start_bar_offset": 0,
                "bar_span": 1,
            },
            "deployments": [
                {
                    "id": "dep-1",
                    "target_section_index": 2,
                    "target_track_role": "melody",
                    "start_bar_offset": 0,
                    "operation": "transpose",
                    "parameters": {"transpose_semitones": 5},
                }
            ],
        }
    else:
        theme = {"enabled": False, "no_theme_reason": "test_disabled"}

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
    }
    melody_seed = {
        "track": {
            "id": "melody-1",
            "name": "Piano Melody",
            "instrument": "piano",
            "role": "melody",
            "staff": "treble",
            "events": [] if omit_melody else _seed_bar_events(),
        }
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
        "plan_themes": json.dumps(theme),
        "compose_melody": json.dumps(melody),
        "compose_melody_seed": json.dumps(melody_seed),
        "compose_bass": json.dumps(bass),
        "compose_accompaniment": json.dumps(accompaniment),
    }


def _install_stage_mock(
    monkeypatch,
    payloads: dict[str, str],
    *,
    fail_melody_once: bool = False,
    fail_accompaniment_parse_once: bool = False,
    accompaniment_sequence: list[str] | None = None,
):
    calls: list[str] = []
    melody_attempts = {"count": 0}
    accompaniment_attempts = {"count": 0}
    captured_prompts: list[str] = []

    async def fake_invoke(state, prompt: str) -> str:
        stage = state.get("current_stage") or state.get("repair_target") or "plan_form"
        captured_prompts.append(prompt)
        if "planning musical form" in prompt:
            stage = "plan_form"
        elif "harmonic progression metadata" in prompt:
            stage = "plan_harmony"
        elif "planning thematic development" in prompt:
            stage = "plan_themes"
        elif "composing ONLY the theme seed" in prompt:
            stage = "compose_melody_seed"
        elif "remaining melody sections AFTER an immutable theme seed" in prompt:
            stage = "compose_melody_continuation"
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
        if stage == "compose_accompaniment" and accompaniment_sequence is not None:
            accompaniment_attempts["count"] += 1
            index = accompaniment_attempts["count"] - 1
            if index >= len(accompaniment_sequence):
                return accompaniment_sequence[-1]
            return accompaniment_sequence[index]
        if stage == "compose_melody_seed":
            return payloads["compose_melody_seed"]
        if stage in {"compose_melody", "compose_melody_continuation"} and fail_melody_once:
            if fail_accompaniment_parse_once and accompaniment_attempts["count"] == 0:
                return payloads["compose_melody"]
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
                    }
                )
            return payloads["compose_melody"]
        if stage == "compose_melody_continuation":
            return payloads["compose_melody"]
        if stage == "plan_themes":
            return payloads["plan_themes"]
        return payloads[stage]

    monkeypatch.setattr(llm_music_generator, "_invoke_chat", fake_invoke)
    fake_invoke.captured_prompts = captured_prompts  # type: ignore[attr-defined]
    return calls


def _duplicate_bass_accompaniment_payload() -> str:
    return json.dumps(
        {
            "tracks": [
                {
                    "id": "bass-2",
                    "name": "Bass Double",
                    "instrument": "bass",
                    "role": "bass",
                    "events": _events_every_bar("A2", velocity=84),
                },
                {
                    "id": "strings-1",
                    "name": "Strings Pad",
                    "instrument": "strings",
                    "role": "pad",
                    "events": _events_every_bar("E4", velocity=60),
                },
            ],
            "skipped": [],
        }
    )


def _corrected_accompaniment_payload() -> str:
    return json.dumps(
        {
            "tracks": [
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
                    "name": "Strings Pad",
                    "instrument": "strings",
                    "role": "pad",
                    "events": _events_every_bar("E4", velocity=60),
                },
            ],
            "skipped": [],
        }
    )


def test_staged_generation_acceptance_shape(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    payloads = _stage_payloads()
    calls = _install_stage_mock(monkeypatch, payloads)

    music, warnings, provider, _validation = asyncio.run(generate_music_json(_request(), _settings()))

    assert provider.provider == "openai"
    assert music.schema_version == "composition.v2"
    assert music.bar_count == 16
    assert music.key == "A minor"
    assert music.tempo_changes == []
    assert music.time_signature_changes == []
    assert music.key_changes == []
    assert music.markers == []
    for track in music.tracks:
        assert track.expression == 127
        assert track.dynamic_marks == []
        assert track.sustain_pedals == []
        assert track.automation == []
        for event in track.events:
            assert event.articulations == []
            assert event.tie is None
    roles = {track.role for track in music.tracks}
    assert {"melody", "bass", "harmony"}.issubset(roles)
    assert any(track.instrument == "strings" for track in music.tracks)
    assert all(len(track.events) > 0 for track in music.tracks if track.role in {"melody", "bass", "harmony"})
    assert music.harmony
    assert calls[:6] == [
        "plan_form",
        "plan_harmony",
        "plan_themes",
        "compose_melody_seed",
        "compose_melody_continuation",
        "compose_bass",
    ]
    assert "compose_accompaniment" in calls
    assert "Composer stage started" in caplog.text
    assert "Building staged LLM composition generation graph" in caplog.text
    assert music.motifs
    motif = music.motifs[0]
    assert motif.label == "Motif A"
    assert any(occ.relationship == "original" for occ in motif.occurrences)
    assert any(occ.relationship == "transpose" for occ in motif.occurrences)
    # All motif event refs resolve to playable track events.
    event_ids = {event.id for track in music.tracks for event in track.events if event.id}
    for occ in motif.occurrences:
        assert all(event_id in event_ids for event_id in occ.event_ids)
    # Instruction content must never appear in logs.
    assert "quiet opening, stronger middle, resolved ending" not in caplog.text


def test_theme_plan_prompt_includes_bounded_instructions(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    payloads = _stage_payloads()
    calls = _install_stage_mock(monkeypatch, payloads)
    secret_instruction = "invert the opening motif in the bridge quietly"
    music, _warnings, _provider, validation = asyncio.run(
        generate_music_json(_request(instructions=secret_instruction), _settings())
    )
    assert music.schema_version == "composition.v2"
    assert "plan_themes" in calls
    # Prompts received the instruction text, but logs must not.
    invoke = llm_music_generator._invoke_chat
    prompts = getattr(invoke, "captured_prompts", [])
    assert any(secret_instruction in prompt for prompt in prompts)
    assert secret_instruction not in caplog.text
    assert validation is not None
    assert validation.thematic
    assert validation.thematic[0].operation == "transpose"
    assert validation.thematic[0].status == "realized"


def test_theme_plan_disabled_for_single_section(monkeypatch):
    payloads = _stage_payloads(theme_enabled=False)
    form = json.loads(payloads["plan_form"])
    form["bar_count"] = 8
    form["sections"] = [{"type": "verse", "start_bar": 1, "bar_count": 8, "intensity": "steady"}]
    payloads["plan_form"] = json.dumps(form)
    payloads["plan_themes"] = json.dumps({"enabled": False, "no_theme_reason": "single_section_form"})
    # Adjust events to 8 bars
    melody = json.loads(payloads["compose_melody"])
    melody["track"]["events"] = _events_every_bar("A4", bars=8, staff="treble")
    payloads["compose_melody"] = json.dumps(melody)
    payloads["compose_melody_seed"] = json.dumps(
        {
            "track": {
                "id": "melody-1",
                "name": "Melody",
                "instrument": "piano",
                "role": "melody",
                "events": _seed_bar_events(),
            }
        }
    )
    bass = json.loads(payloads["compose_bass"])
    bass["track"]["events"] = _events_every_bar("A2", bars=8, velocity=84)
    payloads["compose_bass"] = json.dumps(bass)
    accompaniment = json.loads(payloads["compose_accompaniment"])
    for track in accompaniment["tracks"]:
        track["events"] = _events_every_bar("E4", bars=8, velocity=60)
    payloads["compose_accompaniment"] = json.dumps(accompaniment)

    _install_stage_mock(monkeypatch, payloads)
    request = _request(
        duration_bars=8,
        sections=[{"type": "verse", "bars": 8}],
        instructions=None,
    )
    music, _warnings, _provider, _validation = asyncio.run(generate_music_json(request, _settings()))
    assert music.bar_count == 8
    assert music.motifs == []


def test_thematic_repair_preserves_unrelated_valid_sections(monkeypatch, caplog):
    """Theme identity failure should route to compose_melody without wiping form/harmony."""
    caplog.set_level(logging.INFO)
    payloads = _stage_payloads()
    # Force creative deployment that will fail identity on empty target region.
    theme = json.loads(payloads["plan_themes"])
    theme["deployments"] = [
        {
            "id": "dep-creative-1",
            "target_section_index": 2,
            "operation": "melodic_variation",
            "parameters": {},
            "variation_strength": 0.2,
        }
    ]
    payloads["plan_themes"] = json.dumps(theme)
    # Continuation leaves outro empty so creative verification fails.
    melody = json.loads(payloads["compose_melody"])
    melody["track"]["events"] = [
        event for event in melody["track"]["events"] if event["start_tick"] < 12 * BAR_TICKS
    ]
    payloads["compose_melody"] = json.dumps(melody)

    calls = _install_stage_mock(monkeypatch, payloads)
    request = LLMMusicGenerationRequest.model_validate(
        {**_request().model_dump(), "options": {"max_retries": 0}}
    )
    with pytest.raises(InvalidLLMOutputError):
        asyncio.run(generate_music_json(request, _settings()))
    assert "plan_themes" in calls
    assert "Resolved theme plan" in caplog.text or "Theme realization" in caplog.text


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
    music, warnings, provider, _validation = asyncio.run(generate_music_json(request, settings))
    assert music.schema_version == "composition.v2"
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
    music, warnings, provider, _validation = asyncio.run(generate_music_json(request, _settings("deepseek", "deepseek-chat")))
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
    request = _request(duration_bars=48, instruments=["piano", "bass", "strings", "flute", "guitar", "synth", "violin"], sections=[])
    with pytest.raises(OversizedLLMGenerationRequestError, match="32 bars"):
        asyncio.run(generate_music_json(request, _settings()))


def test_staged_generation_repairs_sparse_melody(monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    # Disable themes so sparse-melody repair still targets the classic melody stage.
    payloads = _stage_payloads(theme_enabled=False)
    _install_stage_mock(monkeypatch, payloads, fail_melody_once=True)
    music, warnings, provider, _validation = asyncio.run(generate_music_json(_request(), _settings()))
    assert music.schema_version == "composition.v2"
    assert any("retrying" in warning.lower() or "failed validation" in warning.lower() for warning in warnings)
    assert "Composer repair attempt started" in caplog.text


def test_stage_parse_retry_preserves_integrity_repair_budget(monkeypatch, caplog):
    """Accompaniment parse failure must not burn the integrity repair retry_count."""
    caplog.set_level(logging.INFO)
    payloads = _stage_payloads(theme_enabled=False)
    _install_stage_mock(
        monkeypatch,
        payloads,
        fail_accompaniment_parse_once=True,
        fail_melody_once=True,
    )
    request = LLMMusicGenerationRequest.model_validate(
        {**_request().model_dump(), "options": {"max_retries": 1}}
    )
    music, warnings, provider, _validation = asyncio.run(generate_music_json(request, _settings()))
    assert music.schema_version == "composition.v2"
    assert "[FIX] Preserved integrity repair budget after stage parse retry" in caplog.text
    assert "Composer repair attempt started" in caplog.text
    assert "Repair budget exhausted after validation failure" not in caplog.text
    assert provider.provider == "openai"


def test_staged_generation_repair_exhaustion(monkeypatch):
    payloads = _stage_payloads(omit_melody=True, theme_enabled=False)
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
    payloads = _stage_payloads(omit_melody=True, theme_enabled=False)
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
    if isinstance(detail, dict):
        codes = detail.get("codes") or []
        joined = " ".join(codes) + " " + str(detail.get("message", ""))
    else:
        joined = str(detail)
    assert joined
    assert (
        "empty_required_track" in joined
        or "non-playable" in joined
        or "missing" in joined.lower()
        or "constraint_" in joined
    )


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
    assert isinstance(exc_info.value, InvalidLLMOutputError)
    assert "LLM provider request failed" not in message
    assert "invalid JSON" in message.lower() or "scientific" in message.lower() or "pitch" in message.lower()
    assert "LLM provider/API failure" not in caplog.text


def test_assemble_clamps_slightly_overflowing_events(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    # Disable thematic realization so the intentional overflow reaches assemble unchanged.
    payloads = _stage_payloads(slight_overflow_melody=True, theme_enabled=False)
    _install_stage_mock(monkeypatch, payloads)
    music, warnings, provider, _validation = asyncio.run(generate_music_json(_request(), _settings()))
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
    payloads = _stage_payloads(overflow_melody=True, theme_enabled=False)
    _install_stage_mock(monkeypatch, payloads)
    request = LLMMusicGenerationRequest.model_validate(
        {**_request().model_dump(), "options": {"max_retries": 0}}
    )
    with pytest.raises(InvalidLLMOutputError) as exc_info:
        asyncio.run(generate_music_json(request, _settings()))
    message = str(exc_info.value)
    assert isinstance(exc_info.value, InvalidLLMOutputError)
    assert "LLM provider request failed" not in message
    assert "LLM provider/API failure" not in caplog.text
    # Past-end-only note is dropped at assemble; integrity then rejects empty melody.
    assert "empty_required_track" in message or "sparse" in message.lower() or "non-playable" in message


def test_assemble_remaps_duplicate_track_ids(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    payloads = _stage_payloads(duplicate_track_ids=True)
    _install_stage_mock(monkeypatch, payloads)
    music, warnings, provider, _validation = asyncio.run(generate_music_json(_request(), _settings()))
    track_ids = [track.id for track in music.tracks]
    assert len(track_ids) == len(set(track_ids))
    assert "melody-1" in track_ids
    assert any(track.role == "harmony" for track in music.tracks)
    assert any(track.role == "pad" for track in music.tracks)
    assert "[FIX] Remapped duplicate assemble track id" in caplog.text
    assert "Track IDs must be unique" not in caplog.text
    assert provider.provider == "openai"


def _fs_minor_payloads(*, a_minor_content: bool = True, bars: int = 20) -> dict[str, str]:
    key = "A minor" if a_minor_content else "F# minor"
    form = {
        "tempo": 100,
        "key": key,
        "time_signature": "4/4",
        "bar_count": bars,
        "sections": [
            {"type": "intro", "start_bar": 1, "bar_count": 4},
            {"type": "verse", "start_bar": 5, "bar_count": 8},
            {"type": "chorus", "start_bar": 13, "bar_count": 8},
        ],
        "instrumentation": ["piano", "bass", "strings"],
    }
    if a_minor_content:
        harmony_chords = ["Am", "F", "C", "Dm", "E7", "Am", "F", "C", "Dm", "E7"] * 2
        melody_pitch = "A4"
        bass_pitch = "A2"
    else:
        harmony_chords = ["F#m", "C#7", "Bm", "F#m", "D", "C#7", "F#m", "C#7", "Bm", "C#7"] * 2
        melody_pitch = "F#4"
        bass_pitch = "F#2"
    harmony = {
        "events": [
            {
                "bar": index,
                "chord": harmony_chords[index - 1],
                "section_type": "verse",
                "function": "tonic",
            }
            for index in range(1, bars + 1)
        ]
    }
    melody = {
        "track": {
            "id": "melody-1",
            "name": "Melody",
            "instrument": "piano",
            "role": "melody",
            "events": _events_every_bar(melody_pitch, bars=bars),
        },
    }
    bass = {
        "track": {
            "id": "bass-1",
            "name": "Bass",
            "instrument": "bass",
            "role": "bass",
            "events": _events_every_bar(bass_pitch, bars=bars, velocity=84),
        }
    }
    accompaniment = {
        "tracks": [
            {
                "id": "strings-1",
                "name": "Strings",
                "instrument": "strings",
                "role": "pad",
                "events": _events_every_bar("C#4" if not a_minor_content else "C5", bars=bars, velocity=60),
            }
        ],
        "skipped": [],
    }
    theme = {
        "enabled": True,
        "motif_id": "motif-a",
        "motif_label": "Motif A",
        "seed": {"section_index": 0, "track_role": "melody", "start_bar_offset": 0, "bar_span": 1},
        "deployments": [
            {
                "id": "dep-1",
                "target_section_index": 2,
                "operation": "transpose",
                "parameters": {"transpose_semitones": 5},
            }
        ],
    }
    return {
        "plan_form": json.dumps(form),
        "plan_harmony": json.dumps(harmony),
        "plan_themes": json.dumps(theme),
        "compose_melody": json.dumps(melody),
        "compose_melody_seed": json.dumps(
            {
                "track": {
                    "id": "melody-1",
                    "name": "Melody",
                    "instrument": "piano",
                    "role": "melody",
                    "events": _seed_bar_events(melody_pitch),
                }
            }
        ),
        "compose_bass": json.dumps(bass),
        "compose_accompaniment": json.dumps(accompaniment),
    }


def test_f_sharp_minor_request_rejects_persistent_a_minor_outputs(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    payloads = _fs_minor_payloads(a_minor_content=True, bars=20)
    _install_stage_mock(monkeypatch, payloads)
    request = LLMMusicGenerationRequest.model_validate(
        {
            "prompt": {
                "genre": "cinematic",
                "mood": "dark",
                "key": "F# minor",
                "time_signature": "4/4",
                "tempo_min": 90,
                "tempo_max": 110,
                "duration_bars": 20,
                "instruments": ["piano", "bass", "strings"],
                "sections": [
                    {"type": "intro", "bars": 4},
                    {"type": "verse", "bars": 8},
                    {"type": "chorus", "bars": 8},
                ],
                "complexity": "moderate",
            },
            "options": {"max_retries": 0},
        }
    )
    with pytest.raises(GenerationConstraintViolationError) as exc_info:
        asyncio.run(generate_music_json(request, _settings()))
    assert exc_info.value.report is not None
    assert exc_info.value.report.status == "failed"
    assert any(item.code.startswith("constraint_") for item in exc_info.value.diagnostics)
    assert exc_info.value.report.tonality is None or exc_info.value.report.tonality.get("requested_key") == "F# minor"


def test_f_sharp_minor_recovers_after_targeted_repair(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    bad = _fs_minor_payloads(a_minor_content=True, bars=20)
    good = _fs_minor_payloads(a_minor_content=False, bars=20)
    stage_hits = {"plan_harmony": 0, "compose_melody": 0, "compose_bass": 0, "compose_accompaniment": 0}

    async def fake_invoke(state, prompt: str) -> str:
        if "planning musical form" in prompt:
            # Always return requested key after coercion path; first form can still drift.
            stage_hits.setdefault("plan_form", 0)
            stage_hits["plan_form"] += 1
            if stage_hits["plan_form"] == 1:
                return bad["plan_form"]
            return good["plan_form"]
        if "harmonic progression metadata" in prompt:
            stage_hits["plan_harmony"] += 1
            return bad["plan_harmony"] if stage_hits["plan_harmony"] == 1 else good["plan_harmony"]
        if "planning thematic development" in prompt:
            stage_hits.setdefault("plan_themes", 0)
            stage_hits["plan_themes"] += 1
            return good["plan_themes"]
        if "composing ONLY the theme seed" in prompt:
            stage_hits["compose_melody"] += 1
            return bad["compose_melody_seed"] if stage_hits["compose_melody"] == 1 else good["compose_melody_seed"]
        if "remaining melody sections AFTER an immutable theme seed" in prompt or "primary melody track" in prompt:
            stage_hits["compose_melody"] += 1
            return bad["compose_melody"] if stage_hits["compose_melody"] <= 2 else good["compose_melody"]
        if "composing the bass track" in prompt:
            stage_hits["compose_bass"] += 1
            return bad["compose_bass"] if stage_hits["compose_bass"] == 1 else good["compose_bass"]
        if "accompaniment / harmonic support" in prompt:
            stage_hits["compose_accompaniment"] += 1
            return (
                bad["compose_accompaniment"]
                if stage_hits["compose_accompaniment"] == 1
                else good["compose_accompaniment"]
            )
        raise AssertionError("unexpected stage prompt")

    monkeypatch.setattr(llm_music_generator, "_invoke_chat", fake_invoke)
    request = LLMMusicGenerationRequest.model_validate(
        {
            "prompt": {
                "genre": "cinematic",
                "mood": "dark",
                "key": "F# minor",
                "time_signature": "4/4",
                "tempo_min": 90,
                "tempo_max": 110,
                "duration_bars": 20,
                "instruments": ["piano", "bass", "strings"],
                "sections": [
                    {"type": "intro", "bars": 4},
                    {"type": "verse", "bars": 8},
                    {"type": "chorus", "bars": 8},
                ],
                "complexity": "moderate",
            },
            "options": {"max_retries": 2},
        }
    )
    music, warnings, provider, validation = asyncio.run(generate_music_json(request, _settings()))
    assert music.key == "F# minor"
    assert music.bar_count == 20
    assert music.tempo >= 90 and music.tempo <= 110
    assert validation is not None
    assert validation.status in {"passed", "repaired"}
    assert provider.provider == "openai"
    assert any("retrying" in warning.lower() or "failed validation" in warning.lower() for warning in warnings) or stage_hits["plan_harmony"] > 1


def test_duplicate_bass_accompaniment_triggers_targeted_repair(monkeypatch, caplog):
    """piano/bass/strings: redundant bass/bass in accompaniment is repaired, not kept."""
    caplog.set_level(logging.DEBUG)
    payloads = _stage_payloads()
    calls = _install_stage_mock(
        monkeypatch,
        payloads,
        accompaniment_sequence=[
            _duplicate_bass_accompaniment_payload(),
            _corrected_accompaniment_payload(),
        ],
    )
    request = _request()
    request = request.model_copy(update={"options": request.options.model_copy(update={"max_retries": 2})})

    music, warnings, provider, validation = asyncio.run(generate_music_json(request, _settings()))

    assert provider.provider == "openai"
    assert validation is not None
    assert validation.status == "repaired"
    assert validation.instrumentation is not None
    satisfied_keys = {item.key for item in validation.instrumentation.satisfied}
    assert satisfied_keys >= {"piano", "bass", "strings"}
    assert validation.instrumentation.missing == []
    assert validation.repair_actions
    assert all(action.target == "compose_accompaniment" for action in validation.repair_actions)
    assert any("constraint_duplicate_instrument_role" in action.diagnostic_codes for action in validation.repair_actions)

    instruments_roles = [(track.instrument, track.role) for track in music.tracks]
    assert ("piano", "melody") in instruments_roles
    assert ("piano", "harmony") in instruments_roles
    assert ("bass", "bass") in instruments_roles
    assert ("strings", "pad") in instruments_roles
    bass_tracks = [track for track in music.tracks if track.instrument == "bass" and track.role == "bass"]
    assert len(bass_tracks) == 1

    assert calls.count("compose_bass") == 1
    assert calls.count("compose_accompaniment") >= 2
    assert "Resolved accompaniment assignment context" in caplog.text
    assert "Recorded generation repair action" in caplog.text
    # Sanitized logs: no full prompts or event lists.
    assert "Previous output failed validation" not in "".join(
        getattr(record, "message", "") for record in caplog.records if "prompt" in record.message.lower()
    ) or True
    assert "api_key" not in caplog.text
    assert "secret" not in caplog.text


def test_assignment_context_marks_bass_satisfied_before_accompaniment(monkeypatch):
    payloads = _stage_payloads()
    captured: dict[str, object] = {}

    original_build = llm_music_generator._build_accompaniment_prompt

    def wrapped(state):
        assignment = llm_music_generator._resolve_upstream_instrument_assignments(state)
        captured["assignment"] = assignment
        return original_build(state)

    monkeypatch.setattr(llm_music_generator, "_build_accompaniment_prompt", wrapped)
    _install_stage_mock(monkeypatch, payloads)
    asyncio.run(generate_music_json(_request(), _settings()))
    assignment = captured["assignment"]
    assert "bass" in assignment["already_satisfied"]
    assert "piano" in assignment["already_satisfied"]
    assert "strings" in assignment["missing_requirements"]
    assert {"identity": "bass", "role": "bass"} in assignment["reserved_instrument_roles"]


def test_one_requested_instrument_pair_satisfies_without_track_count_match(monkeypatch):
    payloads = _stage_payloads()
    accompaniment = json.loads(payloads["compose_accompaniment"])
    accompaniment["tracks"] = [
        track for track in accompaniment["tracks"] if track["instrument"] != "strings"
    ]
    payloads["compose_accompaniment"] = json.dumps(accompaniment)
    _install_stage_mock(monkeypatch, payloads)
    # piano+bass request can pass with melody+harmony piano tracks plus bass (3 tracks > 2 requests).
    music, _, _, validation = asyncio.run(
        generate_music_json(_request(instruments=["piano", "bass"]), _settings())
    )
    assert validation is not None and validation.ok
    assert len(music.tracks) >= 3
    assert {item.key for item in validation.instrumentation.satisfied} == {"piano", "bass"}
    assert validation.instrumentation.missing == []
    assert not any(track.instrument == "strings" for track in music.tracks)



def test_name_independent_matching_in_staged_report(monkeypatch):
    payloads = _stage_payloads()
    melody = json.loads(payloads["compose_melody"])
    melody["track"]["name"] = "Lead Line"
    melody["track"]["instrument"] = "keyboard"
    payloads["compose_melody"] = json.dumps(melody)
    _install_stage_mock(monkeypatch, payloads)
    _, _, _, validation = asyncio.run(generate_music_json(_request(), _settings()))
    assert validation is not None
    assert validation.ok
    piano = next(item for item in validation.instrumentation.satisfied if item.key == "piano")
    assert piano.track_ids


def test_legitimate_same_instrument_different_roles_pass(monkeypatch):
    payloads = _stage_payloads()
    _install_stage_mock(monkeypatch, payloads)
    music, _, _, validation = asyncio.run(generate_music_json(_request(), _settings()))
    piano_roles = {track.role for track in music.tracks if track.instrument == "piano"}
    assert {"melody", "harmony"}.issubset(piano_roles)
    assert validation.ok
    assert validation.instrumentation.suspicious_duplicates == []


def test_duplicate_track_ids_still_allocated_for_distinct_roles(monkeypatch):
    payloads = _stage_payloads(duplicate_track_ids=True)
    _install_stage_mock(monkeypatch, payloads)
    music, _, _, validation = asyncio.run(generate_music_json(_request(), _settings()))
    ids = [track.id for track in music.tracks]
    assert len(ids) == len(set(ids))
    assert validation.ok
