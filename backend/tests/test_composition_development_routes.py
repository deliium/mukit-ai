"""HTTP coverage for POST /composition/development/preview."""

from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.test_composition_development_patch import _sixteen_bar_a
from tests.test_composition_v2_schema import minimal_v2


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    with TestClient(app) as test_client:
        yield test_client


def _body(**overrides):
    composition = _sixteen_bar_a()
    payload = {
        "composition": composition.model_dump(mode="json"),
        "operation": "continue",
        "output_bars": 8,
        "variation_strength": "balanced",
        "development_intent": "continue",
        "candidate_count": 1,
        "selection": {"provider": "fake", "model": "fake-deterministic"},
        "options": {"max_repairs": 1, "context_budget_chars": 8000},
    }
    payload.update(overrides)
    return payload


def test_preview_continue_16_plus_8(client):
    body = _body()
    source = copy.deepcopy(body["composition"])
    response = client.post("/composition/development/preview", json=body)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["provider"] == "fake"
    assert len(payload["candidates"]) == 1
    candidate = payload["candidates"][0]
    assert candidate["composition"]["bar_count"] == 24
    assert candidate["edit_source_fingerprint"] == payload["edit_source_fingerprint"]
    # Request body composition not mutated by server processing (client-side copy).
    assert body["composition"] == source


def test_preview_multiple_candidates(client):
    response = client.post("/composition/development/preview", json=_body(candidate_count=3))
    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["candidates"]) == 3
    assert len({item["candidate_id"] for item in payload["candidates"]}) == 3


def test_preview_rejects_vary_without_source(client):
    response = client.post(
        "/composition/development/preview",
        json=_body(operation="vary_section", output_bars=None, source=None),
    )
    assert response.status_code == 422


def test_preview_rejects_v1_composition(client):
    response = client.post(
        "/composition/development/preview",
        json=_body(
            composition={
                "schema_version": "composition.v1",
                "tempo": 100,
                "key": "C major",
                "time_signature": "4/4",
                "ticks_per_quarter": 480,
                "bar_count": 2,
                "duration_ticks": 3840,
                "sections": [{"type": "intro", "start_bar": 1, "bar_count": 2}],
                "tracks": [
                    {
                        "id": "piano-1",
                        "name": "Piano",
                        "instrument": "piano",
                        "role": "harmony",
                        "midi_program": 0,
                        "channel": 1,
                        "events": [],
                    }
                ],
                "harmony": [],
            }
        ),
    )
    assert response.status_code == 422


def test_preview_unsupported_provider_503(client, monkeypatch):
    for key in ["OPENAI_API_KEY", "DEEPSEEK_API_KEY", "DEFAULT_LLM_PROVIDER", "LLM_FAKE_MODE"]:
        monkeypatch.delenv(key, raising=False)
    response = client.post(
        "/composition/development/preview",
        json=_body(selection={"provider": "openai", "model": "gpt-test"}),
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "development_provider_unavailable"


def test_preview_malformed_returns_502(client, monkeypatch):
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    monkeypatch.setenv("LLM_FAKE_INJECT_MALFORMED", "development")
    response = client.post(
        "/composition/development/preview",
        json=_body(options={"max_repairs": 0}),
    )
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "development_candidate_exhausted"


def test_openapi_includes_development_preview(client):
    schema = client.get("/openapi.json").json()
    assert "/composition/development/preview" in schema["paths"]
    assert "post" in schema["paths"]["/composition/development/preview"]


def test_preview_add_and_vary_operations(client):
    add_response = client.post(
        "/composition/development/preview",
        json=_body(
            operation="add_section",
            output_bars=4,
            target_section_type="bridge",
            development_intent="develop",
        ),
    )
    assert add_response.status_code == 200, add_response.text
    assert add_response.json()["candidates"][0]["composition"]["sections"][-1]["type"] == "bridge"

    vary_response = client.post(
        "/composition/development/preview",
        json=_body(
            operation="vary_section",
            output_bars=None,
            source={"start_bar": 1, "end_bar": 4},
            development_intent="contrast",
            variation_strength="experimental",
        ),
    )
    assert vary_response.status_code == 200, vary_response.text
    assert vary_response.json()["candidates"][0]["composition"]["bar_count"] == 16
