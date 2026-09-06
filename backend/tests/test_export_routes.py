import asyncio

from fastapi.testclient import TestClient

from app.main import app, export_midi, export_musicxml, export_musicxml_preview
from app.schemas import Composition
from tests.test_export_fidelity import build_export_fidelity_composition


client = TestClient(app)


def test_export_musicxml_endpoint_returns_attachment():
    composition = build_export_fidelity_composition()

    response = client.post("/export/musicxml", json=composition.model_dump())

    assert response.status_code == 200
    assert "application/vnd.recordare.musicxml+xml" in response.headers["content-type"]
    assert "attachment" in response.headers["content-disposition"]
    assert ".musicxml" in response.headers["content-disposition"]
    assert "score-partwise" in response.text or "score-timewise" in response.text
    assert "<pitch>" in response.text


def test_export_musicxml_preview_returns_text_without_attachment():
    composition = build_export_fidelity_composition()

    response = client.post("/export/musicxml/preview", json=composition.model_dump())

    assert response.status_code == 200
    assert "application/vnd.recordare.musicxml+xml" in response.headers["content-type"]
    assert "content-disposition" not in {key.lower() for key in response.headers.keys()}
    assert "score-partwise" in response.text or "score-timewise" in response.text
    assert "<pitch>" in response.text


def test_export_midi_endpoint_returns_attachment():
    composition = build_export_fidelity_composition()

    response = client.post("/export/midi", json=composition.model_dump())

    assert response.status_code == 200
    assert "audio/midi" in response.headers["content-type"]
    assert "attachment" in response.headers["content-disposition"]
    assert ".mid" in response.headers["content-disposition"]
    assert response.content[:4] == b"MThd"


def test_export_endpoints_reject_invalid_composition():
    response = client.post("/export/midi", json={"schema_version": "composition.v1", "tempo": 120})

    assert response.status_code == 422
    preview = client.post("/export/musicxml/preview", json={"schema_version": "composition.v1", "tempo": 120})
    assert preview.status_code == 422


def test_export_musicxml_direct_handler_logs_completion(caplog):
    composition = build_export_fidelity_composition()
    with caplog.at_level("INFO"):
        response = asyncio.run(export_musicxml(composition))

    assert response.media_type == "application/vnd.recordare.musicxml+xml"
    assert "MusicXML export request completed" in caplog.text


def test_export_musicxml_preview_direct_handler_logs_completion(caplog):
    composition = build_export_fidelity_composition()
    with caplog.at_level("INFO"):
        response = asyncio.run(export_musicxml_preview(composition))

    assert response.media_type == "application/vnd.recordare.musicxml+xml"
    assert response.headers.get("content-disposition") is None
    assert "MusicXML preview render completed" in caplog.text


def test_export_midi_direct_handler_logs_completion(caplog):
    composition = build_export_fidelity_composition()
    with caplog.at_level("INFO"):
        response = asyncio.run(export_midi(composition))

    assert response.media_type == "audio/midi"
    assert "MIDI export request completed" in caplog.text
