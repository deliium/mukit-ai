import asyncio
import struct
import wave
from io import BytesIO

from fastapi.testclient import TestClient
from music21 import converter

from app.main import app, export_midi, export_musicxml, export_musicxml_preview, export_wav
from app.services.composition_projection import empty_projection_report
from app.services.composition_wav import CompositionWavError, WavRenderResult
from tests.fixtures.load_fixture import load_export_fidelity, load_16bar_multitrack, load_v2_expressive
from tests.test_export_fidelity import build_export_fidelity_composition


client = TestClient(app)


def _minimal_wav_bytes(*, duration_seconds: float = 0.25, sample_rate: int = 22050) -> bytes:
    """Build a tiny valid mono PCM WAV for mocked export assertions."""
    frame_count = max(1, int(duration_seconds * sample_rate))
    buffer = BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(b"\x00\x00" * frame_count)
    return buffer.getvalue()


def _mock_wav_result(payload: bytes | None = None) -> WavRenderResult:
    return WavRenderResult(
        wav_bytes=payload if payload is not None else _minimal_wav_bytes(),
        report=empty_projection_report(),
    )


def _assert_wav_header(payload: bytes) -> float:
    assert payload[:4] == b"RIFF"
    assert payload[8:12] == b"WAVE"
    with wave.open(BytesIO(payload), "rb") as handle:
        frames = handle.getnframes()
        rate = handle.getframerate()
        assert frames > 0
        assert rate > 0
        return frames / float(rate)


def test_export_endpoints_nonempty_and_structurally_valid(monkeypatch, caplog):
    composition = load_export_fidelity()
    fake_wav = _minimal_wav_bytes(duration_seconds=0.5)
    monkeypatch.setattr("app.main.render_wav_with_report", lambda _composition: _mock_wav_result(fake_wav))

    with caplog.at_level("INFO"):
        musicxml = client.post("/export/musicxml", json=composition.model_dump(mode="json"))
        midi = client.post("/export/midi", json=composition.model_dump(mode="json"))
        wav = client.post("/export/wav", json=composition.model_dump(mode="json"))

    assert musicxml.status_code == 200
    assert len(musicxml.content) > 100
    parsed = converter.parseData(musicxml.text)
    assert parsed is not None
    assert len(list(parsed.flatten().notes)) > 0

    assert midi.status_code == 200
    assert len(midi.content) > 20
    assert midi.content[:4] == b"MThd"
    format_type, track_count, division = struct.unpack(">HHH", midi.content[8:14])
    assert format_type in {0, 1}
    assert track_count >= 1
    assert division > 0

    assert wav.status_code == 200
    assert len(wav.content) > 44
    duration = _assert_wav_header(wav.content)
    assert duration >= 0.4
    assert "WAV export request completed" in caplog.text


def test_export_16bar_fixture_midi_and_musicxml_nonempty():
    composition = load_16bar_multitrack()
    midi = client.post("/export/midi", json=composition.model_dump(mode="json"))
    musicxml = client.post("/export/musicxml", json=composition.model_dump(mode="json"))
    assert midi.status_code == 200 and midi.content[:4] == b"MThd" and len(midi.content) > 100
    assert musicxml.status_code == 200 and len(musicxml.content) > 500
    assert "score-partwise" in musicxml.text or "score-timewise" in musicxml.text


def test_export_musicxml_endpoint_returns_projection_headers():
    composition = build_export_fidelity_composition()

    response = client.post("/export/musicxml", json=composition.model_dump(mode="json"))

    assert response.status_code == 200
    assert response.headers.get("X-Mukit-Projection-Status") == "exact"
    assert response.headers.get("X-Mukit-Projection-Issues") == ""
    assert response.headers.get("X-Mukit-Projection-Omitted-Count") == "0"


def test_export_musicxml_preview_returns_projection_headers():
    composition = load_v2_expressive()

    response = client.post("/export/musicxml/preview", json=composition.model_dump(mode="json"))

    assert response.status_code == 200
    assert response.headers.get("X-Mukit-Projection-Status") in {"omitted", "approximated", "exact"}
    issues = response.headers.get("X-Mukit-Projection-Issues", "")
    assert "automation_omitted_from_notation" in issues
    assert int(response.headers.get("X-Mukit-Projection-Omitted-Count", "0")) >= 1


def test_export_midi_endpoint_returns_projection_headers():
    composition = load_v2_expressive()

    response = client.post("/export/midi", json=composition.model_dump(mode="json"))

    assert response.status_code == 200
    assert response.content[:4] == b"MThd"
    status = response.headers.get("X-Mukit-Projection-Status")
    assert status in {"approximated", "omitted", "exact"}
    issues = response.headers.get("X-Mukit-Projection-Issues", "")
    assert issues, "expressive MIDI should report at least one projection issue code"
    assert int(response.headers.get("X-Mukit-Projection-Approximated-Count", "0")) + int(
        response.headers.get("X-Mukit-Projection-Omitted-Count", "0")
    ) >= 1


def test_export_wav_endpoint_returns_projection_headers(monkeypatch):
    composition = load_v2_expressive()
    report = empty_projection_report()
    report.add_issue(
        code="tempo_quantized",
        severity="warning",
        status="approximated",
        path="tempo",
        details={},
    )
    monkeypatch.setattr(
        "app.main.render_wav_with_report",
        lambda _composition: WavRenderResult(wav_bytes=_minimal_wav_bytes(), report=report),
    )

    response = client.post("/export/wav", json=composition.model_dump(mode="json"))

    assert response.status_code == 200
    assert response.headers.get("X-Mukit-Projection-Status") == "approximated"
    assert "tempo_quantized" in response.headers.get("X-Mukit-Projection-Issues", "")
    assert int(response.headers.get("X-Mukit-Projection-Approximated-Count", "0")) >= 1


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


def test_export_wav_endpoint_returns_attachment(monkeypatch, caplog):
    composition = build_export_fidelity_composition()
    fake_wav = _minimal_wav_bytes()

    monkeypatch.setattr("app.main.render_wav_with_report", lambda _composition: _mock_wav_result(fake_wav))

    with caplog.at_level("INFO"):
        response = client.post("/export/wav", json=composition.model_dump())

    assert response.status_code == 200, "WAV export should succeed when render_wav_with_report is mocked"
    assert "audio/wav" in response.headers["content-type"]
    assert "attachment" in response.headers["content-disposition"]
    assert ".wav" in response.headers["content-disposition"]
    assert response.content == fake_wav
    assert response.headers.get("X-Mukit-Projection-Status") == "exact"
    assert "WAV export request completed" in caplog.text
    _assert_wav_header(response.content)


def test_export_wav_returns_503_when_renderer_unavailable(monkeypatch, caplog):
    composition = build_export_fidelity_composition()

    def boom(_composition):
        raise CompositionWavError("WAV renderer unavailable: SoundFont not found", unavailable=True)

    monkeypatch.setattr("app.main.render_wav_with_report", boom)

    with caplog.at_level("WARNING"):
        response = client.post("/export/wav", json=composition.model_dump())

    assert response.status_code == 503
    assert "SoundFont" in response.json()["detail"]
    assert "WAV export unavailable" in caplog.text


def test_export_wav_returns_500_on_render_failure(monkeypatch, caplog):
    composition = build_export_fidelity_composition()

    def boom(_composition):
        raise CompositionWavError("FluidSynth failed with exit code 1")

    monkeypatch.setattr("app.main.render_wav_with_report", boom)

    with caplog.at_level("ERROR"):
        response = client.post("/export/wav", json=composition.model_dump())

    assert response.status_code == 500
    assert "FluidSynth" in response.json()["detail"]
    assert "WAV export failed" in caplog.text


def test_export_endpoints_reject_invalid_composition():
    response = client.post("/export/midi", json={"schema_version": "composition.v1", "tempo": 120})

    assert response.status_code == 422
    preview = client.post("/export/musicxml/preview", json={"schema_version": "composition.v1", "tempo": 120})
    assert preview.status_code == 422
    wav = client.post("/export/wav", json={"schema_version": "composition.v1", "tempo": 120})
    assert wav.status_code == 422


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
    assert response.headers.get("X-Mukit-Projection-Status") in {"exact", "approximated", "omitted"}


def test_export_wav_direct_handler_logs_completion(monkeypatch, caplog):
    composition = build_export_fidelity_composition()
    monkeypatch.setattr("app.main.render_wav_with_report", lambda _c: _mock_wav_result())

    with caplog.at_level("INFO"):
        response = asyncio.run(export_wav(composition))

    assert response.media_type == "audio/wav"
    assert "WAV export request completed" in caplog.text
    assert response.headers.get("X-Mukit-Projection-Status") == "exact"
