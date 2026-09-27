"""Recovery and transcription stop reading once the upload byte limit is passed."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.audio_recovery_schemas import AudioRecoveryError
from app.audio_upload import UploadTooLargeError, read_upload_bounded
from app.db.connection import reset_database_initialization_cache
from app.main import app
from app.services.audio_recovery.pipeline import enqueue_audio_recovery_job

FIXTURE = Path(__file__).parent / "fixtures" / "audio" / "recovery" / "mixed_melody_bass.wav"
_SENTINEL = b"SENTINEL_PAYLOAD_DO_NOT_LOG"


class _ChunkedUpload:
    def __init__(self, payload: bytes, *, chunk_size: int) -> None:
        self._payload = payload
        self._chunk_size = chunk_size
        self._offset = 0
        self.reads = 0
        self.closed = False

    async def read(self, size: int = -1) -> bytes:
        self.reads += 1
        if self._offset >= len(self._payload):
            return b""
        take = self._chunk_size if size < 0 else min(size, self._chunk_size)
        chunk = self._payload[self._offset : self._offset + take]
        self._offset += len(chunk)
        return chunk

    async def close(self) -> None:
        self.closed = True


def test_read_upload_bounded_stops_over_limit() -> None:
    import asyncio

    payload = _SENTINEL + (b"x" * 200_000)
    upload = _ChunkedUpload(payload, chunk_size=64 * 1024)

    async def _read() -> None:
        with pytest.raises(UploadTooLargeError) as captured:
            await read_upload_bounded(upload, max_bytes=1024)
        assert captured.value.limit_bytes == 1024
        assert captured.value.code == "audio_payload_too_large"

    asyncio.run(_read())
    assert upload.closed is True
    assert upload.reads == 1
    assert upload._offset < len(payload)


def test_enqueue_still_rejects_oversized_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROJECT_DB_PATH", str(tmp_path / "projects.db"))
    monkeypatch.setenv("AUDIO_RECOVERY_ASSET_ROOT", str(tmp_path / "assets"))
    monkeypatch.setenv("AUDIO_RECOVERY_MAX_UPLOAD_BYTES", "1024")
    payload = _SENTINEL + (b"\x00" * (1025 - len(_SENTINEL)))
    with pytest.raises(AudioRecoveryError) as captured:
        enqueue_audio_recovery_job(payload, display_filename="over.wav", db_path=tmp_path / "projects.db")
    assert captured.value.code == "audio_payload_too_large"
    assert captured.value.http_status == 413
    assert not (tmp_path / "assets").exists()


def test_recovery_http_stops_one_byte_over_and_keeps_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    asset_root = tmp_path / "audio_recovery_assets"
    monkeypatch.setenv("PROJECT_DB_PATH", str(tmp_path / "projects.db"))
    monkeypatch.setenv("AUDIO_RECOVERY_ASSET_ROOT", str(asset_root))
    monkeypatch.setenv("AUDIO_RECOVERY_MAX_UPLOAD_BYTES", "1024")
    monkeypatch.setenv("AUDIO_RECOVERY_FAKE_MODE", "1")
    monkeypatch.setenv("AUDIO_RECOVERY_ENGINE", "fake:audio-recovery")
    monkeypatch.setenv("AUDIO_RECOVERY_SEPARATION_ENGINE", "fake:stems")
    monkeypatch.delenv("COLLABORATION_ENABLED", raising=False)
    reset_database_initialization_cache()
    caplog.set_level(logging.INFO)
    body = _SENTINEL + (b"\x00" * (1025 - len(_SENTINEL)))
    assert len(body) == 1025

    with TestClient(app) as client:
        rejected = client.post(
            "/audio-recovery/jobs",
            files={"file": ("too-big.wav", body, "audio/wav")},
        )
    assert rejected.status_code == 413
    assert rejected.json()["detail"]["code"] == "audio_payload_too_large"
    assert not (asset_root / "_jobs").exists()
    assert _SENTINEL.decode() not in caplog.text
    assert any(
        record.message == "audio_payload_too_large" and record.limit_bytes == 1024
        for record in caplog.records
    )
    assert any(getattr(record, "basename", None) == "too-big.wav" for record in caplog.records)

    fixture = FIXTURE.read_bytes()
    monkeypatch.setenv("AUDIO_RECOVERY_MAX_UPLOAD_BYTES", str(max(len(fixture), 1024)))
    reset_database_initialization_cache()
    with TestClient(app) as client:
        accepted = client.post(
            "/audio-recovery/jobs",
            files={"file": ("mixed_melody_bass.wav", fixture, "audio/wav")},
        )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "complete"


def test_transcription_maps_bounded_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROJECT_DB_PATH", str(tmp_path / "projects.db"))
    monkeypatch.setenv("AUDIO_MAX_UPLOAD_BYTES", "1024")
    monkeypatch.setenv("AUDIO_FAKE_MODE", "1")
    reset_database_initialization_cache()
    body = b"x" * 1025
    with TestClient(app) as client:
        response = client.post(
            "/transcription/audio",
            files={"file": ("clip.wav", body, "audio/wav")},
        )
    assert response.status_code == 413
    detail = response.json()["detail"]
    assert detail["code"] == "audio_payload_too_large"
    assert detail["details"]["limit_bytes"] == 1024
