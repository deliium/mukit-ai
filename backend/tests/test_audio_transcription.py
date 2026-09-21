"""Audio transcription route/service tests (fake engine)."""

from __future__ import annotations

import io
import struct
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.audio_transcription_settings import load_audio_transcription_settings, sniff_audio_format
from app.main import app
from app.services.audio_transcription.alignment import seconds_to_ticks
from app.services.audio_transcription.service import transcribe_audio_bytes


FIXTURES = Path(__file__).parent / "fixtures" / "audio"
MELODY_WAV = FIXTURES / "melody_c_e_g.wav"
QUIET_NOISE_WAV = FIXTURES / "quiet_noise.wav"


def _tiny_wav_bytes(*, duration_s: float = 0.2, freq: float = 440.0, sr: int = 22050) -> bytes:
    import math

    n = max(1, int(sr * duration_s))
    samples = [
        int(0.3 * math.sin(2 * math.pi * freq * (i / sr)) * 32767) for i in range(n)
    ]
    buf = io.BytesIO()
    with wave.open(buf, "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sr)
        handle.writeframes(struct.pack("<" + "h" * len(samples), *samples))
    return buf.getvalue()


@pytest.fixture
def fake_audio_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUDIO_FAKE_MODE", "1")
    monkeypatch.setenv("AUDIO_TRANSCRIPTION_ENGINE", "fake:audio-mono")


def test_sniff_wav_signature() -> None:
    data = _tiny_wav_bytes()
    assert sniff_audio_format(data) == "wav"
    assert sniff_audio_format(b"not-audio") is None


def test_settings_clamp_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUDIO_MAX_DURATION_SECONDS", "9999")
    settings = load_audio_transcription_settings()
    assert settings.max_duration_seconds == 300.0


def test_seconds_to_ticks_vector() -> None:
    # 120 bpm, 480 ppq → 1 second = 2 quarters = 960 ticks
    assert seconds_to_ticks(1.0, tempo_bpm=120, ticks_per_quarter=480) == 960
    assert seconds_to_ticks(0.5, tempo_bpm=120, ticks_per_quarter=480, origin_tick=100) == 580


def test_transcribe_fake_melody_fixture(fake_audio_env: None) -> None:
    assert MELODY_WAV.is_file()
    payload = MELODY_WAV.read_bytes()
    response = transcribe_audio_bytes(
        payload,
        display_filename="melody_c_e_g.wav",
        tempo_bpm=120,
        ticks_per_quarter=480,
    )
    assert response.preview.schema_version == "transcription.preview.v1"
    assert response.engine.fake is True
    assert response.retention.deleted is True
    assert response.preview.summary.note_count == 3
    assert response.preview.summary.low_confidence_count == 1
    pitches = [n.pitch for n in response.preview.notes]
    assert pitches == [60, 64, 67]


def test_transcribe_quiet_noise_fixture(fake_audio_env: None) -> None:
    assert QUIET_NOISE_WAV.is_file()
    response = transcribe_audio_bytes(
        QUIET_NOISE_WAV.read_bytes(),
        display_filename="quiet_noise.wav",
        tempo_bpm=120,
        ticks_per_quarter=480,
    )
    assert response.preview.schema_version == "transcription.preview.v1"
    assert response.retention.deleted is True
    issue_codes = {issue.code for issue in response.preview.issues}
    assert "low_energy_segment_omitted" in issue_codes


def test_temp_dir_cleaned_after_success(
    fake_audio_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tempfile

    from app.services.audio_transcription import service as audio_service

    created: list[Path] = []
    real_mkdtemp = tempfile.mkdtemp

    def tracking_mkdtemp(*args, **kwargs):
        path = Path(real_mkdtemp(*args, **kwargs))
        created.append(path)
        return str(path)

    monkeypatch.setattr(audio_service.tempfile, "mkdtemp", tracking_mkdtemp)
    payload = MELODY_WAV.read_bytes() if MELODY_WAV.is_file() else _tiny_wav_bytes()
    response = transcribe_audio_bytes(payload, tempo_bpm=120, ticks_per_quarter=480)
    assert response.retention.deleted is True
    assert created, "expected TemporaryDirectory creation"
    assert all(not path.exists() for path in created), created


def test_temp_dir_cleaned_after_mid_request_failure(
    fake_audio_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tempfile

    from app.audio_transcription_schemas import AudioTranscriptionError
    from app.services.audio_transcription import service as audio_service

    created: list[Path] = []
    real_mkdtemp = tempfile.mkdtemp

    def tracking_mkdtemp(*args, **kwargs):
        path = Path(real_mkdtemp(*args, **kwargs))
        created.append(path)
        return str(path)

    monkeypatch.setattr(audio_service.tempfile, "mkdtemp", tracking_mkdtemp)
    monkeypatch.setenv("AUDIO_MAX_DURATION_SECONDS", "0.5")
    settings = load_audio_transcription_settings()
    # Melody fixture is ~1.5s → duration exceeded after temp write + engine.
    payload = MELODY_WAV.read_bytes() if MELODY_WAV.is_file() else _tiny_wav_bytes(duration_s=2.0)
    with pytest.raises(AudioTranscriptionError) as exc:
        transcribe_audio_bytes(payload, settings=settings)
    assert exc.value.code == "audio_duration_exceeded"
    assert created, "expected TemporaryDirectory creation before failure"
    assert all(not path.exists() for path in created), created


def test_transcribe_rejects_too_large(fake_audio_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUDIO_MAX_UPLOAD_BYTES", "2048")
    settings = load_audio_transcription_settings()
    payload = _tiny_wav_bytes(duration_s=1.0)
    assert len(payload) > 2048
    from app.audio_transcription_schemas import AudioTranscriptionError

    with pytest.raises(AudioTranscriptionError) as exc:
        transcribe_audio_bytes(payload, settings=settings)
    assert exc.value.code == "audio_payload_too_large"


def test_transcribe_rejects_unsupported_format(fake_audio_env: None) -> None:
    from app.audio_transcription_schemas import AudioTranscriptionError

    with pytest.raises(AudioTranscriptionError) as exc:
        transcribe_audio_bytes(b"MThd\x00\x00\x00\x06", display_filename="x.mid")
    assert exc.value.code == "audio_format_unsupported"


def test_http_transcription_upload(fake_audio_env: None) -> None:
    client = TestClient(app)
    data = MELODY_WAV.read_bytes() if MELODY_WAV.is_file() else _tiny_wav_bytes()
    response = client.post(
        "/transcription/audio",
        files={"file": ("melody.wav", data, "audio/wav")},
        data={"tempo_bpm": "120", "ticks_per_quarter": "480"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "composition" not in body
    assert body["preview"]["schema_version"] == "transcription.preview.v1"
    assert body["retention"]["deleted"] is True
    assert body["preview"]["summary"]["note_count"] >= 1


def test_http_engine_unavailable_without_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUDIO_FAKE_MODE", "0")
    monkeypatch.setenv("AUDIO_TRANSCRIPTION_ENGINE", "auto")
    client = TestClient(app)
    data = _tiny_wav_bytes()
    response = client.post(
        "/transcription/audio",
        files={"file": ("tone.wav", data, "audio/wav")},
    )
    # Without optional extras, auto should 503 (unless librosa accidentally installed).
    if response.status_code == 200:
        pytest.skip("optional transcription engine present in environment")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "audio_engine_unavailable"
