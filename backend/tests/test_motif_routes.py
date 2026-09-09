"""HTTP coverage for POST /motifs/apply."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.composition_schemas import CompositionV2
from app.llm_settings import LLMProviderSettings, LLMSettings
from app.main import app
from tests.test_composition_v2_schema import _motif_definition, _motif_source_events, _motif_track, minimal_v2


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def _destination_events(start: int):
    return [
        {
            "type": "note",
            "pitch": "G4",
            "start_tick": start,
            "duration_ticks": 480,
            "velocity": 80,
            "id": "dest-blocker",
        }
    ]


def _two_section_composition(*, cross_track: bool = False):
    melody_events = _motif_source_events()
    tracks = [
        _motif_track(events=melody_events),
    ]
    if cross_track:
        tracks.append(
            _motif_track(
                id="melody-2",
                name="Melody 2",
                events=[],
            )
        )
    return CompositionV2.model_validate(
        minimal_v2(
            bar_count=4,
            duration_ticks=7680,
            sections=[
                {
                    "id": "verse",
                    "type": "verse",
                    "start_bar": 1,
                    "bar_count": 2,
                    "start_tick": 0,
                    "duration_ticks": 3840,
                },
                {
                    "id": "chorus",
                    "type": "chorus",
                    "start_bar": 3,
                    "bar_count": 2,
                    "start_tick": 3840,
                    "duration_ticks": 3840,
                },
            ],
            tracks=tracks,
            motifs=[_motif_definition()],
        )
    )


def _apply_body(composition: CompositionV2, **overrides):
    body = {
        "composition": composition.model_dump(mode="json"),
        "source": {"motif_id": "motif-a", "occurrence_id": "occ-orig"},
        "destination": {
            "section_id": "chorus",
            "track_id": "melody-1",
            "start_bar": 3,
        },
        "operation": "repeat",
        "parameters": {},
    }
    body.update(overrides)
    return body


def test_motif_apply_mechanical_repeat_success(client, monkeypatch):
    composition = _two_section_composition()
    monkeypatch.setattr(
        "app.routers.motifs.load_llm_settings",
        lambda: LLMSettings(
            providers=(),
            default_provider=None,
            request_timeout_seconds=30,
            temperature=0.2,
        ),
    )
    monkeypatch.setattr(
        "app.routers.motifs.render_musicxml",
        lambda _composition: ("<score/>", __import__("app.services.composition_projection", fromlist=["empty_projection_report"]).empty_projection_report()),
    )

    response = client.post("/motifs/apply", json=_apply_body(composition))
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["composition"]["schema_version"] == "composition.v2"
    assert payload["musicxml"] == "<score/>"
    assert payload["musicxml_filename"]
    assert payload["result"]["relationship"] == "repeat"
    assert payload["result"]["provider"] is None
    assert payload["result"]["created_event_ids"]
    assert payload["result"]["new_occurrence_id"]
    assert payload["result"]["identity_score"] == 1.0

    motif = next(item for item in payload["composition"]["motifs"] if item["id"] == "motif-a")
    assert len(motif["occurrences"]) == 2
    new_occ = next(item for item in motif["occurrences"] if item["id"] == payload["result"]["new_occurrence_id"])
    assert new_occ["relationship"] == "repeat"
    assert new_occ["event_ids"] == payload["result"]["created_event_ids"]

    dest_track = next(item for item in payload["composition"]["tracks"] if item["id"] == "melody-1")
    dest_ids = {event["id"] for event in dest_track["events"]}
    assert set(payload["result"]["created_event_ids"]).issubset(dest_ids)
    assert all(event["id"] in {"n1", "n2", "n3", "n4"} or event["id"].startswith("motif-") for event in dest_track["events"])


def test_motif_apply_cross_section_and_cross_track(client, monkeypatch):
    composition = _two_section_composition(cross_track=True)
    body = _apply_body(
        composition,
        destination={"section_id": "chorus", "track_id": "melody-2", "start_bar": 3},
    )
    monkeypatch.setattr(
        "app.routers.motifs.load_llm_settings",
        lambda: LLMSettings(
            providers=(),
            default_provider=None,
            request_timeout_seconds=30,
            temperature=0.2,
        ),
    )
    monkeypatch.setattr(
        "app.routers.motifs.render_musicxml",
        lambda _composition: ("<score/>", __import__("app.services.composition_projection", fromlist=["empty_projection_report"]).empty_projection_report()),
    )

    response = client.post("/motifs/apply", json=body)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["result"]["destination_track_id"] == "melody-2"
    assert payload["result"]["destination_start_bar"] == 3
    assert payload["result"]["destination_start_tick"] == 3840


def test_motif_apply_malformed_source_reference(client, monkeypatch):
    composition = _two_section_composition()
    body = _apply_body(composition, source={"motif_id": "motif-a", "occurrence_id": "missing"})
    monkeypatch.setattr(
        "app.routers.motifs.load_llm_settings",
        lambda: LLMSettings(providers=(), default_provider=None, request_timeout_seconds=30, temperature=0.2),
    )

    response = client.post("/motifs/apply", json=body)
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "motif_source_unresolved"


def test_motif_apply_invalid_parameters_schema(client):
    composition = _two_section_composition()
    body = _apply_body(composition, operation="transpose", parameters={})
    response = client.post("/motifs/apply", json=body)
    assert response.status_code == 422
    assert "transpose_semitones" in str(response.json()["detail"])


def test_motif_apply_destination_overflow(client, monkeypatch):
    composition = CompositionV2.model_validate(
        minimal_v2(
            bar_count=2,
            duration_ticks=3840,
            sections=[
                {
                    "id": "verse",
                    "type": "verse",
                    "start_bar": 1,
                    "bar_count": 2,
                    "start_tick": 0,
                    "duration_ticks": 3840,
                }
            ],
            tracks=[_motif_track()],
            motifs=[_motif_definition()],
        )
    )
    body = _apply_body(
        composition,
        destination={"section_id": "verse", "track_id": "melody-1", "start_bar": 2, "start_tick": 3360},
    )
    monkeypatch.setattr(
        "app.routers.motifs.load_llm_settings",
        lambda: LLMSettings(providers=(), default_provider=None, request_timeout_seconds=30, temperature=0.2),
    )

    response = client.post("/motifs/apply", json=body)
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "motif_destination_out_of_bounds"


def test_motif_apply_overlap_rejected(client, monkeypatch):
    # Partial overlap: blocker extends past the realized motif span end.
    composition = CompositionV2.model_validate(
        minimal_v2(
            bar_count=4,
            duration_ticks=7680,
            sections=[
                {
                    "id": "verse",
                    "type": "verse",
                    "start_bar": 1,
                    "bar_count": 2,
                    "start_tick": 0,
                    "duration_ticks": 3840,
                },
                {
                    "id": "chorus",
                    "type": "chorus",
                    "start_bar": 3,
                    "bar_count": 2,
                    "start_tick": 3840,
                    "duration_ticks": 3840,
                },
            ],
            tracks=[
                _motif_track(
                    events=_motif_source_events()
                    + [
                        {
                            "type": "note",
                            "pitch": "G4",
                            "start_tick": 4800,
                            "duration_ticks": 960,
                            "velocity": 80,
                            "id": "dest-blocker",
                        }
                    ]
                )
            ],
            motifs=[_motif_definition()],
        )
    )
    body = _apply_body(composition)
    monkeypatch.setattr(
        "app.routers.motifs.load_llm_settings",
        lambda: LLMSettings(providers=(), default_provider=None, request_timeout_seconds=30, temperature=0.2),
    )

    response = client.post("/motifs/apply", json=body)
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "motif_boundary_crossing_note"


def test_motif_apply_creative_unavailable_provider(client, monkeypatch):
    composition = _two_section_composition()
    body = _apply_body(
        composition,
        operation="melodic_variation",
        variation_strength=0.5,
        selection={"provider": "openai", "model": "test-model"},
    )
    for key in ["OPENAI_API_KEY", "DEEPSEEK_API_KEY", "DEFAULT_LLM_PROVIDER", "LLM_FAKE_MODE"]:
        monkeypatch.delenv(key, raising=False)

    response = client.post("/motifs/apply", json=body)
    assert response.status_code == 503, response.text


def test_motif_apply_creative_with_fake_provider_succeeds(client, monkeypatch):
    composition = _two_section_composition()
    body = _apply_body(
        composition,
        operation="rhythmic_variation",
        variation_strength=0.4,
        selection={"provider": "fake", "model": "fake-deterministic"},
    )
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    monkeypatch.setattr(
        "app.routers.motifs.load_llm_settings",
        lambda: LLMSettings(
            providers=(
                LLMProviderSettings(
                    provider="fake",
                    model="fake-deterministic",
                    api_key="unused",
                    is_default=True,
                ),
            ),
            default_provider="fake",
            request_timeout_seconds=30,
            temperature=0.2,
        ),
    )

    response = client.post("/motifs/apply", json=body)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["result"]["relationship"] == "rhythmic_variation"
    assert payload["result"]["provider"] == "fake"
    assert len(payload["result"]["created_event_ids"]) >= 3
    assert "events" not in str(payload["composition"]["motifs"])


def test_motif_apply_creative_with_mocked_provider_succeeds(client, monkeypatch):
    composition = _two_section_composition()
    body = _apply_body(
        composition,
        operation="melodic_variation",
        variation_strength=0.5,
        selection={"provider": "openai", "model": "test-model"},
    )
    monkeypatch.setattr(
        "app.routers.motifs.load_llm_settings",
        lambda: LLMSettings(
            providers=(
                LLMProviderSettings(
                    provider="openai",
                    model="test-model",
                    api_key="secret",
                    is_default=True,
                ),
            ),
            default_provider="openai",
            request_timeout_seconds=30,
            temperature=0.2,
        ),
    )

    async def fake_chat(_state, _prompt):
        return json.dumps({"note_count": 3, "pitch_semitone_offsets": [0, 1, 0]})

    monkeypatch.setattr("app.services.llm_motif_editor._invoke_motif_chat", fake_chat)

    response = client.post("/motifs/apply", json=body)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["result"]["relationship"] == "melodic_variation"
    assert payload["result"]["provider"] == "openai"
    assert payload["result"]["identity_score"] >= 0.0


def test_motif_apply_sanitized_error_has_no_composition_payload(client, monkeypatch):
    composition = _two_section_composition()
    body = _apply_body(composition, source={"motif_id": "motif-a", "occurrence_id": "missing-occ"})
    monkeypatch.setattr(
        "app.routers.motifs.load_llm_settings",
        lambda: LLMSettings(providers=(), default_provider=None, request_timeout_seconds=30, temperature=0.2),
    )

    response = client.post("/motifs/apply", json=body)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail == {
        "code": "motif_source_unresolved",
        "message": "Source occurrence not found in motif definition",
        "details": {"motif_id": "motif-a", "occurrence_id": "missing-occ"},
    }
