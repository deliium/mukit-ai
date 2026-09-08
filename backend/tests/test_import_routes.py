"""HTTP tests for /imports/midi and /imports/musicxml."""

from __future__ import annotations

import io

import mido
import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.test_musicxml_import import MINIMAL_PARTWISE


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def _simple_midi_bytes() -> bytes:
    mid = mido.MidiFile(type=1, ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=500000, time=0))
    track.append(mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0))
    track.append(mido.Message("program_change", program=0, time=0))
    track.append(mido.Message("note_on", note=60, velocity=90, time=0))
    track.append(mido.Message("note_off", note=60, velocity=0, time=480))
    track.append(mido.MetaMessage("end_of_track", time=0))
    buf = io.BytesIO()
    mid.save(file=buf)
    return buf.getvalue()


def test_import_midi_route_returns_v2_and_regenerated_musicxml(client):
    response = client.post(
        "/imports/midi",
        files={"file": ("piece.mid", _simple_midi_bytes(), "audio/midi")},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["composition"]["schema_version"] == "composition.v2"
    assert payload["composition"]["harmony"] == []
    assert "<score-partwise" in payload["musicxml"]
    assert payload["import_report"]["summary"]["detected_format"] == "midi"
    assert "notation_report" in payload
    assert "status" in payload["notation_report"]


def test_import_musicxml_route_returns_v2(client):
    response = client.post(
        "/imports/musicxml",
        files={"file": ("piece.musicxml", MINIMAL_PARTWISE.encode("utf-8"), "application/xml")},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["composition"]["schema_version"] == "composition.v2"
    assert len(payload["composition"]["tracks"][0]["events"]) == 4
    assert payload["import_report"]["summary"]["detected_format"] == "musicxml"


def test_midi_endpoint_rejects_musicxml_bytes(client):
    response = client.post(
        "/imports/midi",
        files={"file": ("x.mid", MINIMAL_PARTWISE.encode("utf-8"), "audio/midi")},
    )
    assert response.status_code == 415
    assert response.json()["detail"]["code"] == "import_unsupported_media_type"


def test_musicxml_endpoint_rejects_midi_bytes(client):
    response = client.post(
        "/imports/musicxml",
        files={"file": ("x.musicxml", _simple_midi_bytes(), "application/xml")},
    )
    assert response.status_code == 415
    assert response.json()["detail"]["code"] == "import_unsupported_media_type"


def test_empty_upload_is_422(client):
    response = client.post(
        "/imports/midi",
        files={"file": ("empty.mid", b"", "audio/midi")},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "import_malformed_source"
