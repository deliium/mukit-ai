"""HTTP API tests for Ardour companion (fake OSC; no real Ardour)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import reset_database_initialization_cache
from app.main import app
from app.services import ardour_companion_service as companion

FIXTURE = (
    Path(__file__).resolve().parents[1] / "app" / "fixtures" / "composition_v2_expressive.json"
)


@pytest.fixture
def client_enabled(tmp_path, monkeypatch):
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.setenv("ARDOUR_COMPANION_ENABLED", "1")
    monkeypatch.setenv("ARDOUR_COMPANION_FAKE", "1")
    reset_database_initialization_cache()
    companion.shutdown_ardour_companion()
    with TestClient(app) as test_client:
        yield test_client
    companion.shutdown_ardour_companion()


@pytest.fixture
def client_disabled(tmp_path, monkeypatch):
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.setenv("ARDOUR_COMPANION_ENABLED", "0")
    monkeypatch.setenv("ARDOUR_COMPANION_FAKE", "1")
    reset_database_initialization_cache()
    companion.shutdown_ardour_companion()
    with TestClient(app) as test_client:
        yield test_client
    companion.shutdown_ardour_companion()


def test_status_when_disabled_is_200(client_disabled: TestClient) -> None:
    response = client_disabled.get("/ardour/companion/status")
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False
    assert body["connection_state"] == "disabled"


def test_mutating_when_disabled_is_403(client_disabled: TestClient) -> None:
    response = client_disabled.post(
        "/ardour/companion/connect",
        json={
            "host": "127.0.0.1",
            "osc_port": 3819,
            "feedback_port": 18010,
            "control_permission": True,
        },
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "ardour_companion_disabled"


def test_connect_play_fader_disconnect(client_enabled: TestClient) -> None:
    connected = client_enabled.post(
        "/ardour/companion/connect",
        json={
            "host": "127.0.0.1",
            "osc_port": 3819,
            "feedback_port": 18011,
            "control_permission": True,
        },
    )
    assert connected.status_code == 200, connected.text
    assert connected.json()["connection_state"] == "connected"

    play = client_enabled.post("/ardour/companion/transport/play")
    assert play.status_code == 200
    assert play.json()["accepted"] is True
    assert play.json()["status"]["transport_playing"] is True

    fader = client_enabled.post("/ardour/companion/strips/1/fader", json={"value": 0.4})
    assert fader.status_code == 200
    strips = client_enabled.get("/ardour/companion/strips")
    assert strips.status_code == 200
    assert strips.json()["strips"][0]["fader"] == pytest.approx(0.4)

    disconnected = client_enabled.post("/ardour/companion/disconnect")
    assert disconnected.status_code == 200
    assert disconnected.json()["status"]["connection_state"] == "disconnected"


def test_permission_required(client_enabled: TestClient) -> None:
    client_enabled.post(
        "/ardour/companion/connect",
        json={
            "host": "127.0.0.1",
            "osc_port": 3819,
            "feedback_port": 18012,
            "control_permission": False,
        },
    )
    play = client_enabled.post("/ardour/companion/transport/play")
    assert play.status_code == 422
    assert play.json()["detail"]["code"] == "ardour_control_permission_required"


def test_public_host_refused(client_enabled: TestClient) -> None:
    response = client_enabled.post(
        "/ardour/companion/connect",
        json={
            "host": "8.8.8.8",
            "osc_port": 3819,
            "feedback_port": 18013,
            "control_permission": True,
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "ardour_host_refused"


def test_teardown_closes_listener(client_enabled: TestClient) -> None:
    client_enabled.post(
        "/ardour/companion/connect",
        json={
            "host": "127.0.0.1",
            "osc_port": 3819,
            "feedback_port": 18014,
            "control_permission": True,
        },
    )
    companion.shutdown_ardour_companion()
    status = client_enabled.get("/ardour/companion/status")
    assert status.json()["connection_state"] == "disconnected"


def test_companion_does_not_write_composition(client_enabled: TestClient) -> None:
    composition = json.loads(FIXTURE.read_text(encoding="utf-8"))
    created = client_enabled.post(
        "/projects",
        json={"name": "ArdourNoWrite", "composition": composition},
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    before = created.json()["composition"]

    client_enabled.post(
        "/ardour/companion/connect",
        json={
            "host": "127.0.0.1",
            "osc_port": 3819,
            "feedback_port": 18015,
            "control_permission": True,
        },
    )
    client_enabled.post("/ardour/companion/transport/play")
    client_enabled.post("/ardour/companion/strips/1/fader", json={"value": 0.1})

    opened = client_enabled.get(f"/projects/{project_id}")
    assert opened.status_code == 200
    assert opened.json()["composition"] == before
