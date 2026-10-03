"""Acceptance: mocked Ardour companion (no real Ardour process)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import reset_database_initialization_cache
from app.main import app
from app.services import ardour_companion_service as companion
from app.services.ardour_osc_codec import decode_osc_message
from app.services.ardour_osc_paths import (
    SET_SURFACE_FEEDBACK,
    build_set_surface,
    set_surface_arg_tuple,
)

FIXTURE = (
    Path(__file__).resolve().parents[1] / "app" / "fixtures" / "composition_v2_expressive.json"
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.setenv("ARDOUR_COMPANION_ENABLED", "1")
    monkeypatch.setenv("ARDOUR_COMPANION_FAKE", "1")
    reset_database_initialization_cache()
    companion.shutdown_ardour_companion()
    with TestClient(app) as test_client:
        yield test_client
    companion.shutdown_ardour_companion()


def test_codec_locked_set_surface_and_feedback_8219() -> None:
    assert SET_SURFACE_FEEDBACK == 8219
    assert set_surface_arg_tuple() == (16, 159, 8219, 0)
    path, args = decode_osc_message(build_set_surface(feedback_port=8000))
    assert path == "/set_surface"
    assert args == [16, 159, 8219, 0, 0, 0, 8000]


def test_fake_connect_play_fader_record_replace_disconnect(client: TestClient) -> None:
    connected = client.post(
        "/ardour/companion/connect",
        json={
            "host": "127.0.0.1",
            "osc_port": 3819,
            "feedback_port": 19001,
            "control_permission": True,
        },
    )
    assert connected.status_code == 200
    assert connected.json()["connection_state"] == "connected"
    with companion._LOCK:
        assert companion._SESSION is not None
        peer = companion._SESSION.transport.fake_peer
        assert peer is not None
        assert "/set_surface" in peer.paths_sent()

    play = client.post("/ardour/companion/transport/play")
    assert play.status_code == 200
    assert play.json()["accepted"] is True
    assert play.json()["status"]["transport_playing"] is True

    fader = client.post("/ardour/companion/strips/1/fader", json={"value": 0.55})
    assert fader.status_code == 200
    assert fader.json()["status"]["strips"][0]["fader"] == pytest.approx(0.55)

    # Record arm toggles only when observed differs.
    armed_false = client.post("/ardour/companion/record", json={"desired": False})
    assert armed_false.status_code == 200
    armed_true = client.post("/ardour/companion/record", json={"desired": True})
    assert armed_true.status_code == 200
    assert armed_true.json()["status"]["record_armed"] is True

    # Force unknown observed → refuse.
    with companion._LOCK:
        assert companion._SESSION is not None
        companion._SESSION.transport.state.record_armed = None
    refuse = client.post("/ardour/companion/record", json={"desired": False})
    assert refuse.status_code == 409
    assert refuse.json()["detail"]["code"] == "ardour_feedback_stale"

    replaced = client.post(
        "/ardour/companion/connect",
        json={
            "host": "10.0.0.9",
            "osc_port": 3819,
            "feedback_port": 19002,
            "control_permission": True,
        },
    )
    assert replaced.status_code == 200
    assert replaced.json()["host"] == "10.0.0.9"

    disconnected = client.post("/ardour/companion/disconnect")
    assert disconnected.status_code == 200
    assert disconnected.json()["status"]["connection_state"] == "disconnected"


def test_flag_off_permission_and_public_host(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "off.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.setenv("ARDOUR_COMPANION_ENABLED", "0")
    monkeypatch.setenv("ARDOUR_COMPANION_FAKE", "1")
    reset_database_initialization_cache()
    companion.shutdown_ardour_companion()
    with TestClient(app) as client:
        status = client.get("/ardour/companion/status")
        assert status.status_code == 200
        assert status.json()["enabled"] is False
        mutate = client.post(
            "/ardour/companion/connect",
            json={
                "host": "127.0.0.1",
                "osc_port": 3819,
                "feedback_port": 19003,
                "control_permission": True,
            },
        )
        assert mutate.status_code == 403

    monkeypatch.setenv("ARDOUR_COMPANION_ENABLED", "1")
    with TestClient(app) as client:
        client.post(
            "/ardour/companion/connect",
            json={
                "host": "127.0.0.1",
                "osc_port": 3819,
                "feedback_port": 19004,
                "control_permission": False,
            },
        )
        play = client.post("/ardour/companion/transport/play")
        assert play.status_code == 422
        assert play.json()["detail"]["code"] == "ardour_control_permission_required"

        public = client.post(
            "/ardour/companion/connect",
            json={
                "host": "1.1.1.1",
                "osc_port": 3819,
                "feedback_port": 19005,
                "control_permission": True,
            },
        )
        assert public.status_code == 422
        assert public.json()["detail"]["code"] == "ardour_host_refused"
    companion.shutdown_ardour_companion()


def test_lifespan_teardown_and_no_composition_write(client: TestClient) -> None:
    composition = json.loads(FIXTURE.read_text(encoding="utf-8"))
    created = client.post(
        "/projects",
        json={"name": "ArdourAccept", "composition": composition},
    )
    assert created.status_code == 201
    project_id = created.json()["id"]
    before_events = created.json()["composition"]["tracks"][0]["events"]

    client.post(
        "/ardour/companion/connect",
        json={
            "host": "127.0.0.1",
            "osc_port": 3819,
            "feedback_port": 19006,
            "control_permission": True,
        },
    )
    client.post("/ardour/companion/transport/play")
    companion.shutdown_ardour_companion()
    status = client.get("/ardour/companion/status")
    assert status.json()["connection_state"] == "disconnected"

    opened = client.get(f"/projects/{project_id}")
    assert opened.json()["composition"]["tracks"][0]["events"] == before_events
