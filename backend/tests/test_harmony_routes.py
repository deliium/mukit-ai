"""HTTP coverage for POST /harmony/reharmonize/preview."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.test_composition_reharmonization import _sixteen_bar_composition


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        yield test_client


def _preview_body(composition, **overrides):
    body = {
        "composition": composition.model_dump(mode="json"),
        "selection": {"start_bar": 9, "end_bar": 12},
        "operation": "increase_tension",
        "content_policy": "preserve_melody_adapt_harmony",
        "target_track_ids": ["bass", "accomp"],
        "engine": "deterministic",
        "instruction": "make the harmony more tense while keeping the melody",
        "tonal_context": {"allow_modulation": False, "target_key": None, "target_chord": None},
        "selection_options": {"provider": None, "model": None},
    }
    body.update(overrides)
    return body


def test_preview_deterministic_bars_9_12(client):
    composition = _sixteen_bar_composition()
    melody_before = [
        event.model_dump(mode="json")
        for event in next(track for track in composition.tracks if track.id == "melody").events
    ]
    response = client.post("/harmony/reharmonize/preview", json=_preview_body(composition))
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["provider"] == "deterministic"
    assert payload["harmony_changes"]
    assert any(item["events_changed"] > 0 for item in payload["track_changes"])
    melody_after = next(track for track in payload["composition"]["tracks"] if track["id"] == "melody")[
        "events"
    ]
    assert melody_after == melody_before
    # Request composition identity: client payload is not persisted; route is stateless.
    assert payload["compatibility"]["status"] in {"compatible", "compatible_with_warnings"}


def test_preview_rejects_missing_targets(client):
    composition = _sixteen_bar_composition()
    response = client.post(
        "/harmony/reharmonize/preview",
        json=_preview_body(composition, target_track_ids=[]),
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "reharmonize_invalid_targets"


def test_preview_ai_fake_mode(client, monkeypatch):
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    composition = _sixteen_bar_composition()
    response = client.post(
        "/harmony/reharmonize/preview",
        json=_preview_body(
            composition,
            engine="ai",
            selection_options={"provider": "fake", "model": "fake-deterministic"},
        ),
    )
    assert response.status_code == 200, response.text
    assert response.json()["provider"] == "fake"


def test_preview_ai_without_provider_returns_503(client, monkeypatch):
    for key in ["OPENAI_API_KEY", "DEEPSEEK_API_KEY", "DEFAULT_LLM_PROVIDER", "LLM_FAKE_MODE"]:
        monkeypatch.delenv(key, raising=False)
    composition = _sixteen_bar_composition()
    response = client.post(
        "/harmony/reharmonize/preview",
        json=_preview_body(
            composition,
            engine="ai",
            selection_options={"provider": "openai", "model": "gpt-test"},
        ),
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] in {
        "llm_provider_unavailable",
        "llm_provider_unsupported",
    }
