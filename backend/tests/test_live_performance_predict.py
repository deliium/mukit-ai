"""Tests for live accompaniment predict API and domain validation."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.live_performance_schemas import (
    LIVE_HORIZON_INVALID,
    LiveAccompanimentPredictRequestV1,
    LivePerformanceError,
    map_live_performance_error_to_http,
    validate_predict_request_domain,
)
from app.live_performance_settings import load_live_performance_settings
from app.main import app
from app.services.live_accompaniment_predict import predict_live_accompaniment


def _base_request(**overrides):
    body = {
        "schema_version": "live.accompaniment.predict.request.v1",
        "session_id": "sess-test",
        "request_id": "req-1",
        "clock": {"tick": 0, "bar": 1, "beat": 1, "tempo": 120},
        "active_harmony": {"symbol": "Cmaj7", "start_tick": 0, "duration_ticks": 1920},
        "features": {"density": 0.4, "recent_note_count": 2},
        "horizon": {"bars": 1, "ms": 2000},
    }
    body.update(overrides)
    return body


def test_map_live_performance_error_to_http() -> None:
    exc = LivePerformanceError("x", code=LIVE_HORIZON_INVALID)
    status, detail = map_live_performance_error_to_http(exc)
    assert status == 422
    assert detail["code"] == LIVE_HORIZON_INVALID


def test_validate_clean_request_and_forbid_composition_field() -> None:
    settings = load_live_performance_settings({})
    req = LiveAccompanimentPredictRequestV1.model_validate(_base_request())
    validate_predict_request_domain(req, settings)

    with pytest.raises(Exception):
        LiveAccompanimentPredictRequestV1.model_validate(
            _base_request(composition={"tracks": []})
        )


def test_horizon_out_of_bounds() -> None:
    settings = load_live_performance_settings(
        {
            "LIVE_HORIZON_BARS_MIN": "1",
            "LIVE_HORIZON_BARS_MAX": "2",
            "LIVE_HORIZON_MS_MIN": "250",
            "LIVE_HORIZON_MS_MAX": "8000",
        }
    )
    req = LiveAccompanimentPredictRequestV1.model_validate(
        _base_request(horizon={"bars": 1, "ms": 50_000})
    )
    with pytest.raises(LivePerformanceError) as exc:
        validate_predict_request_domain(req, settings)
    assert exc.value.code == LIVE_HORIZON_INVALID


def test_predict_service_fake_chunk() -> None:
    req = LiveAccompanimentPredictRequestV1.model_validate(_base_request())
    chunk = predict_live_accompaniment(req, env={"LLM_FAKE_MODE": "1"})
    assert chunk.source == "fake"
    assert len(chunk.events) >= 1
    assert chunk.latency_ms is not None
    assert chunk.latency_ms.generation is not None


def test_predict_http_route_ok() -> None:
    client = TestClient(app)
    response = client.post("/live/accompaniment/predict", json=_base_request())
    assert response.status_code == 200
    data = response.json()
    assert data["schema_version"] == "live.accompaniment.chunk.v1"
    assert data["source"] == "fake"
    assert isinstance(data["events"], list)
    assert len(data["events"]) >= 1


def test_predict_http_rejects_composition_field() -> None:
    client = TestClient(app)
    response = client.post(
        "/live/accompaniment/predict",
        json=_base_request(composition={"tracks": []}),
    )
    assert response.status_code == 422


def test_predict_fake_jam_multi_role_deterministic() -> None:
    """Jam mode + belief hash yields multi-role events; same seed → same pitches."""
    from app.services.fake_live_accompaniment import fake_live_accompaniment_chunk

    body = _base_request(
        jam_mode="user_melody",
        controls={
            "complexity": "medium",
            "density": "medium",
            "style": "arp",
            "responsiveness": "medium",
        },
        belief={"symbol": "Cmaj7", "confidence": 0.9, "held": False},
        active_harmony={"symbol": "Cmaj7", "start_tick": 0, "duration_ticks": 1920},
        role_mask=["bass", "accompaniment", "texture"],
    )
    req = LiveAccompanimentPredictRequestV1.model_validate(body)
    chunk_a = fake_live_accompaniment_chunk(req)
    chunk_b = fake_live_accompaniment_chunk(req)
    assert len(chunk_a.events) >= 2
    roles = {e.track_role for e in chunk_a.events}
    assert "bass" in roles
    assert "accompaniment" in roles
    assert "texture" in roles
    assert [e.pitch for e in chunk_a.events] == [e.pitch for e in chunk_b.events]
    assert [e.start_tick for e in chunk_a.events] == [e.start_tick for e in chunk_b.events]


def test_predict_fake_jam_mode_changes_hash() -> None:
    from app.services.fake_live_accompaniment import fake_live_accompaniment_chunk

    base = _base_request(
        belief={"symbol": "Am", "confidence": 0.8, "held": False},
        active_harmony={"symbol": "Am"},
        controls={
            "complexity": "high",
            "density": "high",
            "style": "block",
            "responsiveness": "high",
        },
    )
    melody = LiveAccompanimentPredictRequestV1.model_validate(
        {**base, "jam_mode": "user_melody", "role_mask": ["bass", "accompaniment"]}
    )
    chords = LiveAccompanimentPredictRequestV1.model_validate(
        {**base, "jam_mode": "user_chords", "role_mask": ["melody", "bass", "texture"]}
    )
    chunk_m = fake_live_accompaniment_chunk(melody)
    chunk_c = fake_live_accompaniment_chunk(chords)
    roles_m = {e.track_role for e in chunk_m.events}
    roles_c = {e.track_role for e in chunk_c.events}
    assert "bass" in roles_m
    assert "melody" in roles_c
    # Different jam_mode → different seed → different event stream shape/pitches
    assert [e.pitch for e in chunk_m.events] != [e.pitch for e in chunk_c.events] or roles_m != roles_c


def test_predict_fake_without_jam_stays_accompaniment() -> None:
    """Backward compat: no jam_mode → single accompaniment role."""
    from app.services.fake_live_accompaniment import fake_live_accompaniment_chunk

    req = LiveAccompanimentPredictRequestV1.model_validate(_base_request())
    chunk = fake_live_accompaniment_chunk(req)
    assert len(chunk.events) >= 1
    assert all(e.track_role == "accompaniment" for e in chunk.events)


def test_predict_http_jam_mode_ok() -> None:
    client = TestClient(app)
    response = client.post(
        "/live/accompaniment/predict",
        json=_base_request(
            jam_mode="user_chords",
            controls={
                "complexity": "low",
                "density": "low",
                "style": "pad",
                "responsiveness": "low",
            },
            belief={"symbol": "G7", "confidence": 0.7, "held": True},
            role_mask=["melody", "bass", "texture"],
        ),
    )
    assert response.status_code == 200
    data = response.json()
    roles = {e["track_role"] for e in data["events"]}
    assert "melody" in roles or "bass" in roles
