"""Credit-free fake LLM provider and fixture coverage."""

from __future__ import annotations

import asyncio
import logging

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.db import reset_database_initialization_cache
from app.llm_settings import FAKE_PROVIDER, load_llm_settings
from app.main import app, edit_llm_composition_region, generate_llm_music_json, get_llm_models
from app.schemas import LLMCompositionEditRequest, LLMMusicGenerationRequest
from app.services.composition_midi import render_midi
from app.services.composition_region_patch import event_in_region, selection_tick_bounds
from app.services.fake_llm import FAKE_DISPLAY_NAME, FAKE_MALFORMED_ENV
from app.services.fixture_compositions import (
    FIXTURE_16BAR_MULTITRACK,
    FIXTURE_UNSUPPORTED_INSTRUMENT,
    FIXTURE_V2_EXPRESSIVE,
    load_composition_fixture,
)
from app.services.music_json_renderer import render_musicxml
from tests.fixtures.load_fixture import load_16bar_multitrack, load_unsupported_instrument


@pytest.fixture
def fake_env(monkeypatch):
    for key in (
        "OPENAI_API_KEY",
        "DEEPSEEK_API_KEY",
        "DEFAULT_LLM_PROVIDER",
        FAKE_MALFORMED_ENV,
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    return load_llm_settings()


@pytest.fixture
def client(tmp_path, monkeypatch, fake_env):
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    reset_database_initialization_cache()
    with TestClient(app) as test_client:
        yield test_client


def test_fixtures_load_and_validate():
    multi = load_16bar_multitrack()
    assert multi.bar_count >= 16
    assert len(multi.tracks) >= 3
    assert sum(len(track.events) for track in multi.tracks) > 0
    assert all(track.events or track.id for track in multi.tracks)

    unsupported = load_unsupported_instrument()
    assert any("quantum" in track.instrument.lower() for track in unsupported.tracks)
    assert sum(len(track.events) for track in unsupported.tracks) > 0


def test_load_llm_settings_fake_mode(fake_env):
    assert fake_env.default_provider == FAKE_PROVIDER
    assert any(provider.provider == FAKE_PROVIDER for provider in fake_env.providers)
    assert all(provider.api_key != "" for provider in fake_env.providers)


def test_load_llm_settings_fake_wins_default_when_real_keys_present(monkeypatch, caplog):
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
    monkeypatch.delenv("DEFAULT_LLM_PROVIDER", raising=False)
    with caplog.at_level(logging.WARNING):
        settings = load_llm_settings()
    assert settings.default_provider == FAKE_PROVIDER
    assert {provider.provider for provider in settings.providers} >= {FAKE_PROVIDER, "openai"}
    assert any("LLM_FAKE_MODE" in record.message for record in caplog.records)


def test_get_llm_models_lists_fake_without_secrets(fake_env):
    response = asyncio.run(get_llm_models())
    assert response.models
    fake = next(model for model in response.models if model.provider == FAKE_PROVIDER)
    assert fake.display_name == FAKE_DISPLAY_NAME
    assert fake.is_default is True
    payload = response.model_dump(mode="json")
    serialized = str(payload)
    assert "api_key" not in serialized
    assert "sk-" not in serialized
    assert "fake" in serialized


def test_fake_generate_returns_canonical_notes(fake_env):
    request = LLMMusicGenerationRequest.model_validate(
        {
            "prompt": {
                "genre": "pop",
                "mood": "bright",
                "duration_bars": 16,
                "instruments": ["piano", "bass"],
            },
            "selection": {"provider": "fake"},
        }
    )
    response = asyncio.run(generate_llm_music_json(request))
    assert response.provider == FAKE_PROVIDER
    assert response.music.schema_version == "composition.v2"
    assert response.music.bar_count >= 16
    assert len(response.music.tracks) >= 3
    event_count = sum(len(track.events) for track in response.music.tracks)
    assert event_count > 0
    assert response.musicxml
    fixture = load_composition_fixture(FIXTURE_16BAR_MULTITRACK)
    assert response.music.bar_count == fixture.bar_count
    assert response.music.tempo_changes == []
    assert all(event.articulations == [] for track in response.music.tracks for event in track.events)
    assert response.music.motifs
    motif = response.music.motifs[0]
    assert motif.label == "Motif A"
    assert any(occ.relationship == "original" for occ in motif.occurrences)
    transpose = next(occ for occ in motif.occurrences if occ.relationship == "transpose")
    event_ids = {event.id for track in response.music.tracks for event in track.events if event.id}
    assert all(event_id in event_ids for event_id in transpose.event_ids)
    assert response.validation is not None
    assert response.validation.thematic
    assert response.validation.thematic[0].operation == "transpose"
    assert response.validation.thematic[0].identity_score == 1.0


def test_fake_generate_expressive_v2_fixture(fake_env):
    request = LLMMusicGenerationRequest.model_validate(
        {
            "prompt": {
                "genre": "classical",
                "mood": "expressive",
                "duration_bars": 4,
                "tempo_min": 80,
                "tempo_max": 120,
                "instruments": ["piano", "bass"],
            },
            "selection": {"provider": "fake"},
        }
    )
    response = asyncio.run(generate_llm_music_json(request))
    assert response.music.schema_version == "composition.v2"
    assert response.music.bar_count == 4
    assert response.music.tempo_changes
    assert any(
        event.articulations for track in response.music.tracks for event in track.events
    )
    assert any(event.tie is not None for track in response.music.tracks for event in track.events)

def test_fake_generate_rejects_contradictory_duration(fake_env):
    request = LLMMusicGenerationRequest.model_validate(
        {
            "prompt": {
                "genre": "pop",
                "mood": "bright",
                "duration_bars": 12,
                "instruments": ["piano", "bass"],
            },
            "selection": {"provider": "fake"},
        }
    )
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(generate_llm_music_json(request))
    assert exc_info.value.status_code == 502
    assert "duration_bars" in str(exc_info.value.detail).lower() or "fixture" in str(exc_info.value.detail).lower()


def test_fake_region_edit_patches_only_selected_bars(fake_env):
    composition = load_16bar_multitrack()
    request = LLMCompositionEditRequest.model_validate(
        {
            "composition": composition.model_dump(mode="json"),
            "edit": {
                "instruction": "reshape the melody in these bars",
                "selection": {"start_bar": 4, "end_bar": 5, "track_ids": ["melody-1"]},
            },
            "selection": {"provider": "fake"},
        }
    )
    original = composition.model_dump(mode="json")
    response = asyncio.run(edit_llm_composition_region(request))
    assert response.provider == FAKE_PROVIDER
    assert response.composition.schema_version == "composition.v2"
    assert response.patch.schema_version == "composition.v2"
    assert response.patch.start_bar == 4
    assert response.patch.end_bar == 5

    bounds = selection_tick_bounds(composition, request.edit.selection)
    orig_melody = next(track for track in composition.tracks if track.id == "melody-1")
    new_melody = next(track for track in response.composition.tracks if track.id == "melody-1")
    outside_orig = [
        (event.pitch, event.start_tick, event.duration_ticks, event.velocity)
        for event in orig_melody.events
        if not event_in_region(event, bounds)
    ]
    outside_new = [
        (event.pitch, event.start_tick, event.duration_ticks, event.velocity)
        for event in new_melody.events
        if not event_in_region(event, bounds)
    ]
    assert outside_orig == outside_new
    assert any(event_in_region(event, bounds) for event in new_melody.events)
    assert any(
        event.articulations for event in new_melody.events if event_in_region(event, bounds)
    )
    # Stored request composition object identity is irrelevant; ensure caller's dump unchanged.
    assert composition.model_dump(mode="json") == original


def test_fake_edit_imported_midi_preserves_outside_region_and_exports(fake_env):
    """Imported solo/other compositions must be AI-editable under the canonical integrity profile."""
    import io

    import mido

    from app.services.composition_midi_import import import_midi_bytes

    mid = mido.MidiFile(type=1, ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=500000, time=0))
    track.append(mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0))
    for bar in range(4):
        start = bar * 1920
        track.append(mido.Message("note_on", note=60 + bar, velocity=90, time=0 if bar == 0 else 1440))
        track.append(mido.Message("note_off", note=60 + bar, velocity=0, time=480))
    track.append(mido.MetaMessage("end_of_track", time=0))
    buf = io.BytesIO()
    mid.save(file=buf)
    imported = import_midi_bytes(buf.getvalue(), display_filename="solo.mid").composition
    track_id = imported.tracks[0].id

    request = LLMCompositionEditRequest.model_validate(
        {
            "composition": imported.model_dump(mode="json"),
            "edit": {
                "instruction": "reshape notes in these bars",
                "selection": {"start_bar": 1, "end_bar": 2, "track_ids": [track_id]},
            },
            "selection": {"provider": "fake"},
        }
    )
    response = asyncio.run(edit_llm_composition_region(request))
    assert response.composition.schema_version == "composition.v2"
    bounds = selection_tick_bounds(imported, request.edit.selection)
    orig = imported.tracks[0]
    updated = next(track for track in response.composition.tracks if track.id == track_id)
    outside_orig = [
        (event.id, event.pitch, event.start_tick, event.duration_ticks)
        for event in orig.events
        if not event_in_region(event, bounds)
    ]
    outside_new = [
        (event.id, event.pitch, event.start_tick, event.duration_ticks)
        for event in updated.events
        if not event_in_region(event, bounds)
    ]
    assert outside_orig == outside_new

    midi_bytes = render_midi(response.composition)
    assert midi_bytes.startswith(b"MThd")
    musicxml, _report = render_musicxml(response.composition)
    assert "<score-partwise" in musicxml


def test_fake_region_edit_preserves_integrity_on_v2_expressive(fake_env):
    """Short-window edits on the 4-bar expressive fixture must stay density-valid."""
    composition = load_composition_fixture(FIXTURE_V2_EXPRESSIVE)
    request = LLMCompositionEditRequest.model_validate(
        {
            "composition": composition.model_dump(mode="json"),
            "edit": {
                "instruction": "reshape the melody with clearer articulation",
                "selection": {"start_bar": 1, "end_bar": 2, "track_ids": ["melody-1"]},
            },
            "selection": {"provider": "fake"},
        }
    )
    response = asyncio.run(edit_llm_composition_region(request))
    assert response.composition.schema_version == "composition.v2"
    assert response.composition.bar_count == 4
    melody = next(track for track in response.composition.tracks if track.id == "melody-1")
    assert len(melody.events) >= 4
    assert any(event.articulations for event in melody.events)


def test_malformed_fake_generate_returns_502_and_leaves_project_unchanged(client, monkeypatch, caplog):
    created = client.post("/projects", json={"name": "Keep Me"})
    assert created.status_code == 201
    project_id = created.json()["id"]
    composition = load_16bar_multitrack().model_dump(mode="json")
    patched = client.patch(f"/projects/{project_id}", json={"composition": composition})
    assert patched.status_code == 200

    monkeypatch.setenv(FAKE_MALFORMED_ENV, "generate")
    with caplog.at_level(logging.WARNING):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                generate_llm_music_json(
                    LLMMusicGenerationRequest.model_validate(
                        {
                            "prompt": {
                                "genre": "jazz",
                                "mood": "cool",
                                "duration_bars": 16,
                                "instruments": ["piano", "bass"],
                            },
                            "selection": {"provider": "fake"},
                        }
                    )
                )
            )
    assert exc_info.value.status_code == 502

    opened = client.get(f"/projects/{project_id}")
    assert opened.status_code == 200
    assert opened.json()["composition"] == patched.json()["composition"]
    joined = " ".join(record.message for record in caplog.records)
    assert "OPENAI_API_KEY" not in joined
    assert "sk-" not in joined


def test_unsupported_instrument_fixture_exports_and_keeps_notes():
    composition = load_composition_fixture(FIXTURE_UNSUPPORTED_INSTRUMENT)
    event_count = sum(len(track.events) for track in composition.tracks)
    assert event_count > 0

    midi_bytes = render_midi(composition)
    assert midi_bytes.startswith(b"MThd")
    assert len(midi_bytes) > 20

    musicxml, report = render_musicxml(composition)
    assert "<score-partwise" in musicxml or "score-partwise" in musicxml
    assert len(musicxml) > 100
    assert report.status == "exact"
    # Unknown instrument should not wipe notes; program 0 / piano fallback is acceptable.
    lead = next(track for track in composition.tracks if "quantum" in track.instrument.lower())
    assert lead.midi_program == 0
    assert lead.events
    assert isinstance(report.issues, list)


def test_fake_generate_honors_aliases_and_reports_instrumentation(fake_env, caplog):
    request = LLMMusicGenerationRequest.model_validate(
        {
            "prompt": {
                "genre": "pop",
                "mood": "bright",
                "duration_bars": 16,
                "instruments": ["Acoustic Piano", "bass guitar"],
            },
            "selection": {"provider": "fake"},
        }
    )
    with caplog.at_level(logging.INFO):
        response = asyncio.run(generate_llm_music_json(request))
    assert response.validation is not None
    assert response.validation.ok
    assert response.validation.instrumentation is not None
    keys = {item.key for item in response.validation.instrumentation.satisfied}
    assert keys == {"piano", "bass"}
    assert response.validation.instrumentation.missing == []
    assert "Fake LLM instrumentation gate" in caplog.text


def test_fake_generate_fails_genuinely_missing_requirement(fake_env, caplog):
    request = LLMMusicGenerationRequest.model_validate(
        {
            "prompt": {
                "genre": "pop",
                "mood": "bright",
                "duration_bars": 16,
                "instruments": ["piano", "bass", "strings"],
            },
            "selection": {"provider": "fake"},
        }
    )
    with caplog.at_level(logging.WARNING):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(generate_llm_music_json(request))
    assert exc_info.value.status_code == 502
    detail = str(exc_info.value.detail).lower()
    assert "constraint_missing_instrument_family" in detail or "fixture" in detail
    assert "Fake LLM fixture conformance failure" in caplog.text


def test_fake_composition_development_draft_is_relative(fake_env):
    from app.composition_development_schemas import CompositionDevelopmentPreviewRequest
    from app.llm_settings import LLMProviderSettings
    from app.services.fake_llm import draft_fake_composition_development
    from tests.test_composition_development_patch import _sixteen_bar_a

    request = CompositionDevelopmentPreviewRequest.model_validate(
        {
            "composition": _sixteen_bar_a(),
            "operation": "continue",
            "output_bars": 8,
            "variation_strength": "balanced",
            "candidate_count": 2,
            "selection": {"provider": "fake", "model": "fake-deterministic"},
        }
    )
    provider = LLMProviderSettings(provider="fake", model="fake-deterministic", api_key="unused")
    draft = asyncio.run(
        draft_fake_composition_development(
            request,
            provider,
            candidate_ordinal=2,
            creative_direction="develop motivic cells with moderate rhythmic variation",
        )
    )
    assert {track.track_id for track in draft.tracks} == {t.id for t in request.composition.tracks}
    # Relative draft: no absolute start_tick / complete composition fields.
    dumped = draft.model_dump(mode="json")
    assert "bar_count" not in dumped
    assert "duration_ticks" not in dumped
    for track in draft.tracks:
        for event in track.events:
            assert event.relative_start_tick >= 0
            assert "start_tick" not in event.model_dump(mode="json")
