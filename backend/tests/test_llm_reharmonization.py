"""Fake/AI reharmonization orchestration coverage."""

from __future__ import annotations

import asyncio
import logging

import pytest

from app.harmony_schemas import ReharmonizePreviewRequest
from app.llm_settings import LLMProviderSettings, load_llm_settings
from app.services.fake_llm import draft_fake_reharmonization
from app.services.llm_music_generator import InvalidLLMOutputError
from app.services.llm_reharmonizer import run_reharmonize_preview
from tests.test_composition_reharmonization import _sixteen_bar_composition


def test_fake_reharmonization_preserves_melody_bars_9_12(monkeypatch):
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    composition = _sixteen_bar_composition()
    melody_before = [
        event.model_dump(mode="json")
        for event in next(track for track in composition.tracks if track.id == "melody").events
    ]
    request = ReharmonizePreviewRequest(
        composition=composition,
        selection={"start_bar": 9, "end_bar": 12},
        operation="increase_tension",
        content_policy="preserve_melody_adapt_harmony",
        target_track_ids=["bass", "accomp"],
        engine="ai",
        selection_options={"provider": "fake", "model": "fake-deterministic"},
        instruction="make the harmony more tense while keeping the melody",
    )
    settings = load_llm_settings()
    response = asyncio.run(run_reharmonize_preview(request, settings=settings))
    assert response.provider == "fake"
    melody_after = [
        event.model_dump(mode="json")
        for event in next(track for track in response.composition.tracks if track.id == "melody").events
    ]
    assert melody_after == melody_before
    assert response.harmony_changes
    assert any(item.events_changed > 0 for item in response.track_changes)


def test_fake_malformed_reharmonization(monkeypatch):
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    monkeypatch.setenv("LLM_FAKE_INJECT_MALFORMED", "reharmonize")
    composition = _sixteen_bar_composition()
    request = ReharmonizePreviewRequest(
        composition=composition,
        selection={"start_bar": 9, "end_bar": 12},
        operation="increase_tension",
        content_policy="preserve_melody_adapt_harmony",
        target_track_ids=["bass"],
        engine="ai",
        selection_options={"provider": "fake"},
    )
    with pytest.raises(InvalidLLMOutputError):
        asyncio.run(run_reharmonize_preview(request, settings=load_llm_settings()))


def test_fake_draft_log_hygiene(monkeypatch, caplog):
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    monkeypatch.delenv("LLM_FAKE_INJECT_MALFORMED", raising=False)
    composition = _sixteen_bar_composition()
    request = ReharmonizePreviewRequest(
        composition=composition,
        selection={"start_bar": 9, "end_bar": 12},
        operation="increase_tension",
        content_policy="preserve_melody_adapt_harmony",
        target_track_ids=["bass", "accomp"],
        engine="ai",
        instruction="secret chord soup must not appear in logs",
    )
    provider = LLMProviderSettings(provider="fake", model="fake-deterministic", api_key="secret-key")
    with caplog.at_level(logging.DEBUG):
        asyncio.run(draft_fake_reharmonization(request, provider))
    joined = " ".join(record.getMessage() for record in caplog.records)
    assert "secret chord soup" not in joined
    assert "secret-key" not in joined
    assert '"harmony":' not in joined
