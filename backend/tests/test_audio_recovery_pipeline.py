"""Audio recovery separation + pipeline smoke tests (fake mode)."""

from __future__ import annotations

import io
import json
import math
import struct
import wave
from pathlib import Path

import pytest

from app.audio_recovery_schemas import AudioRecoveryBindRequestV1, AudioRecoveryError
from app.audio_recovery_settings import load_audio_recovery_settings
from app.db.connection import reset_database_initialization_cache, run_alembic_upgrade
from app.services import project_store as project_store_mod
from app.services.audio_recovery.pipeline import (
    bind_audio_recovery_job,
    enqueue_audio_recovery_job,
)
from app.services.audio_recovery.separation import (
    map_demucs_stems_to_product,
    run_separation,
)


FIXTURE = Path(__file__).parent / "fixtures" / "audio" / "recovery" / "mixed_melody_bass.wav"


def _tiny_wav_bytes(*, duration_s: float = 0.5, freq: float = 440.0) -> bytes:
    sr = 22050
    n = max(1, int(sr * duration_s))
    samples = [int(12000 * math.sin(2 * math.pi * freq * i / sr)) for i in range(n)]
    buf = io.BytesIO()
    with wave.open(buf, "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sr)
        handle.writeframes(struct.pack("<" + "h" * n, *samples))
    return buf.getvalue()


@pytest.fixture()
def recovery_env(tmp_path, monkeypatch):
    db_path = tmp_path / "projects.db"
    asset_root = tmp_path / "audio_recovery_assets"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.setenv("AUDIO_RECOVERY_ASSET_ROOT", str(asset_root))
    monkeypatch.setenv("AUDIO_RECOVERY_FAKE_MODE", "1")
    monkeypatch.setenv("AUDIO_RECOVERY_SEPARATION_ENGINE", "fake:stems")
    monkeypatch.setenv("AUDIO_RECOVERY_ENGINE", "fake:audio-recovery")
    reset_database_initialization_cache()
    run_alembic_upgrade(db_path)
    return {"db_path": db_path, "asset_root": asset_root}


def test_demucs_to_product_map_issues():
    roles, issues = map_demucs_stems_to_product(["vocals", "bass", "drums", "other"])
    assert "melody" in roles
    assert "harmonic" in roles
    codes = {i.code for i in issues}
    assert "melody_derived_from_vocals" in codes
    assert "separation_partial" in codes


def test_fake_separation_complete(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIO_RECOVERY_FAKE_MODE", "1")
    monkeypatch.setenv("AUDIO_RECOVERY_SEPARATION_ENGINE", "fake:stems")
    settings = load_audio_recovery_settings()
    source = tmp_path / "src.wav"
    source.write_bytes(_tiny_wav_bytes())
    result = run_separation(source, work_dir=tmp_path / "stems", settings=settings)
    assert result.status == "complete"
    assert result.engine_id == "fake:stems"
    assert result.stems
    assert all(s.path and s.path.is_file() for s in result.stems)


def test_enqueue_run_inline_fake_complete(recovery_env):
    payload = FIXTURE.read_bytes() if FIXTURE.is_file() else _tiny_wav_bytes(duration_s=1.0)
    job = enqueue_audio_recovery_job(
        payload,
        display_filename="mixed.wav",
        run_inline=True,
        db_path=recovery_env["db_path"],
    )
    assert job.status == "complete"
    assert job.preview is not None
    assert job.preview.schema_version == "audio.recovery.preview.v1"
    assert job.preview.preview_fingerprint
    assert job.preview.summary.note_count >= 0
    for note in job.preview.notes:
        assert 0.0 <= note.confidence <= 1.0
        assert note.provisional_id


def test_bind_requires_project_id(recovery_env):
    payload = _tiny_wav_bytes()
    job = enqueue_audio_recovery_job(
        payload,
        display_filename="a.wav",
        run_inline=True,
        db_path=recovery_env["db_path"],
    )
    assert job.preview is not None
    # Bypass pydantic min_length so the service-layer guard is exercised.
    body = AudioRecoveryBindRequestV1.model_construct(
        schema_version="audio.recovery.bind.v1",
        project_id="",
        preview_fingerprint=job.preview.preview_fingerprint,
        event_map=[],
    )
    with pytest.raises(AudioRecoveryError) as exc:
        bind_audio_recovery_job(
            job.id,
            body,
            db_path=recovery_env["db_path"],
        )
    assert exc.value.code == "audio_recovery_project_required"


def test_bind_persists_assets(recovery_env):
    project = project_store_mod.create_project(
        "Recovery Bind Test",
        db_path=recovery_env["db_path"],
    )
    project_id = project.id

    payload = _tiny_wav_bytes(duration_s=0.8)
    job = enqueue_audio_recovery_job(
        payload,
        display_filename="b.wav",
        project_id=project_id,
        run_inline=True,
        db_path=recovery_env["db_path"],
    )
    assert job.status == "complete"
    assert job.preview is not None
    notes = job.preview.notes[:3]
    event_map = [
        {
            "provisional_id": n.provisional_id,
            "event_id": f"ev-{i}",
            "track_id": "track-1",
        }
        for i, n in enumerate(notes)
    ]
    result = bind_audio_recovery_job(
        job.id,
        AudioRecoveryBindRequestV1(
            project_id=project_id,
            preview_fingerprint=job.preview.preview_fingerprint,
            event_map=event_map,
        ),
        db_path=recovery_env["db_path"],
    )
    assert result.source_audio_asset_id
    assert result.result_asset_id
    assert result.alignment_asset_id
    assert result.roundtrip_provenance is not None
    assert result.roundtrip_provenance.alignment_asset_id == result.alignment_asset_id
    asset_root = Path(recovery_env["asset_root"])
    assert any(asset_root.rglob("*.wav"))
    json_files = list(asset_root.rglob("*.json"))
    assert len(json_files) >= 2  # result_json + alignment.json
    schemas = {json.loads(p.read_text())["schema_version"] for p in json_files}
    assert "audio.recovery.result.v1" in schemas
    assert "audio.alignment.v1" in schemas

    # Idempotent re-bind returns same ids; source WAV bytes unchanged.
    source_path = next(asset_root.rglob("*.wav"))
    source_sha = source_path.read_bytes()
    again = bind_audio_recovery_job(
        job.id,
        AudioRecoveryBindRequestV1(
            project_id=project_id,
            preview_fingerprint=job.preview.preview_fingerprint,
            event_map=event_map,
        ),
        db_path=recovery_env["db_path"],
    )
    assert again.source_audio_asset_id == result.source_audio_asset_id
    assert again.alignment_asset_id == result.alignment_asset_id
    assert source_path.read_bytes() == source_sha


def test_bound_discovery_after_bind(recovery_env):
    from app.services.audio_recovery.pipeline import discover_bound_recovery_for_project

    project = project_store_mod.create_project(
        "Discovery Test",
        db_path=recovery_env["db_path"],
    )
    idle = discover_bound_recovery_for_project(
        project.id, db_path=recovery_env["db_path"]
    )
    assert idle.bound is False
    assert idle.latest is None

    payload = _tiny_wav_bytes(duration_s=0.6)
    job = enqueue_audio_recovery_job(
        payload,
        display_filename="c.wav",
        project_id=project.id,
        run_inline=True,
        db_path=recovery_env["db_path"],
    )
    bind_audio_recovery_job(
        job.id,
        AudioRecoveryBindRequestV1(
            project_id=project.id,
            preview_fingerprint=job.preview.preview_fingerprint,
            event_map=[],
        ),
        db_path=recovery_env["db_path"],
    )
    discovery = discover_bound_recovery_for_project(
        project.id, db_path=recovery_env["db_path"]
    )
    assert discovery.bound is True
    assert discovery.latest is not None
    assert discovery.latest.source_audio_asset_id
    assert discovery.latest.result_asset_id
    assert discovery.latest.alignment_asset_id
    assert discovery.latest.job_id == job.id
