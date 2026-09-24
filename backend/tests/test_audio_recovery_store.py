"""Unit tests for audio recovery asset/job store and path confinement."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from app.audio_recovery_schemas import AudioRecoveryError
from app.audio_recovery_settings import load_audio_recovery_settings
from app.db.connection import get_connection, reset_database_initialization_cache, run_alembic_upgrade
from app.services import audio_recovery_store as store
from app.services import project_store as project_store_mod


@pytest.fixture
def recovery_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    db_path = tmp_path / "projects.db"
    asset_root = tmp_path / "audio_recovery_assets"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.setenv("AUDIO_RECOVERY_ASSET_ROOT", str(asset_root))
    monkeypatch.setenv("AUDIO_RECOVERY_FAKE_MODE", "1")
    monkeypatch.setenv("AUDIO_RECOVERY_MAX_JOBS_PER_PROJECT", "2")
    monkeypatch.setenv("AUDIO_RECOVERY_MAX_ASSETS_PER_PROJECT", "5")
    monkeypatch.setenv("AUDIO_RECOVERY_MAX_TOTAL_BYTES", "1048576")
    reset_database_initialization_cache()
    run_alembic_upgrade(db_path)
    return tmp_path


def test_settings_default_root_beside_db(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = tmp_path / "data" / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db))
    monkeypatch.delenv("AUDIO_RECOVERY_ASSET_ROOT", raising=False)
    settings = load_audio_recovery_settings()
    assert settings.asset_root == db.parent / "audio_recovery_assets"
    assert "datasets" not in str(settings.asset_root)


def test_settings_clamp(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setenv("AUDIO_RECOVERY_MAX_ASSETS_PER_PROJECT", "9999")
    with caplog.at_level(logging.WARNING):
        settings = load_audio_recovery_settings()
    assert settings.max_assets_per_project == 200


def test_path_confinement_rejects_escape(recovery_env: Path) -> None:
    settings = load_audio_recovery_settings()
    with pytest.raises(AudioRecoveryError) as exc:
        store.absolute_under_asset_root(settings, "../outside.wav")
    assert exc.value.code == "audio_recovery_internal_error"


def test_job_workdir_write_and_delete(recovery_env: Path) -> None:
    settings = load_audio_recovery_settings()
    job_id = store.allocate_job_id()
    store.ensure_job_workdir(settings, job_id)
    relpath, sha_prefix, byte_size = store.write_job_bytes(
        settings,
        job_id=job_id,
        relative_name="source.wav",
        payload=b"RIFF....WAVE....fake",
    )
    assert relpath.endswith("source.wav")
    assert byte_size == len(b"RIFF....WAVE....fake")
    assert len(sha_prefix) == 16
    abs_path = store.absolute_under_asset_root(settings, relpath)
    assert abs_path.is_file()
    store.delete_job_workdir(settings, job_id)
    assert not store.job_work_dir(settings, job_id).exists()


def test_insert_job_quota_and_status(recovery_env: Path) -> None:
    settings = load_audio_recovery_settings()
    db_path = recovery_env / "projects.db"
    project = project_store_mod.create_project(name="Recovery Test", db_path=db_path)

    with get_connection(db_path) as conn:
        for _ in range(2):
            job_id = store.allocate_job_id()
            store.insert_queued_job(
                conn,
                job_id=job_id,
                project_id=project.id,
                engine_id="fake:audio-recovery",
                separation_engine_id="fake:stems",
                fake=True,
                work_relpath=store.job_work_relpath(job_id),
            )
        with pytest.raises(AudioRecoveryError) as exc:
            store.assert_job_quota(conn, settings, project.id)
        assert exc.value.code == "audio_recovery_quota_exceeded"

        job_id = store.allocate_job_id()
        store.insert_queued_job(
            conn,
            job_id=job_id,
            project_id=None,
            engine_id="fake:audio-recovery",
            separation_engine_id=None,
            fake=True,
            work_relpath=store.job_work_relpath(job_id),
        )
        row = store.update_job_status(
            conn,
            job_id,
            status="running",
            source_byte_size=12,
            source_sha256_prefix="abcd1234abcd1234",
        )
        assert row["status"] == "running"
        assert row["started_at"]
        complete = store.update_job_status(
            conn,
            job_id,
            status="complete",
            preview_fingerprint="fp_deadbeef01",
        )
        assert complete["status"] == "complete"
        assert complete["completed_at"]


def test_durable_asset_requires_project_and_project_delete_gc(
    recovery_env: Path, caplog: pytest.LogCaptureFixture
) -> None:
    settings = load_audio_recovery_settings()
    db_path = recovery_env / "projects.db"
    project = project_store_mod.create_project(name="Bind GC", db_path=db_path)
    job_id = store.allocate_job_id()

    with pytest.raises(AudioRecoveryError) as exc:
        store.write_durable_asset_bytes(
            settings,
            project_id="",
            job_id=job_id,
            kind="source_audio",
            payload=b"RIFF....WAVE....",
            content_type="audio/wav",
            ext="wav",
        )
    assert exc.value.code == "audio_recovery_project_required"

    with get_connection(db_path) as conn:
        store.insert_queued_job(
            conn,
            job_id=job_id,
            project_id=project.id,
            engine_id="fake:audio-recovery",
            separation_engine_id="fake:stems",
            fake=True,
            work_relpath=store.job_work_relpath(job_id),
        )
        written = store.write_durable_asset_bytes(
            settings,
            project_id=project.id,
            job_id=job_id,
            kind="source_audio",
            payload=b"RIFF....WAVE....src",
            content_type="audio/wav",
            ext="wav",
        )
        store.insert_asset_row(
            conn,
            asset_id=written.asset_id,
            project_id=project.id,
            job_id=job_id,
            kind=written.kind,
            content_type=written.content_type,
            byte_size=written.byte_size,
            sha256_prefix=written.sha256_prefix,
            relpath=written.relpath,
        )
        assert written.absolute_path.is_file()
        project_dir = store.project_asset_dir(settings, project.id)
        assert project_dir.is_dir()

    with caplog.at_level(logging.INFO):
        project_store_mod.delete_project(project.id, db_path=db_path)
    assert not project_dir.exists()
    with get_connection(db_path) as conn:
        assert store.get_job_row(conn, job_id) is None
        assert store.get_asset_row(conn, written.asset_id) is None


def test_alignment_json_kind_and_project_gc(recovery_env: Path) -> None:
    settings = load_audio_recovery_settings()
    db_path = recovery_env / "projects.db"
    project = project_store_mod.create_project(name="Align GC", db_path=db_path)
    job_id = store.allocate_job_id()
    payload = b'{"schema_version":"audio.alignment.v1","playable":false}'

    with get_connection(db_path) as conn:
        store.insert_queued_job(
            conn,
            job_id=job_id,
            project_id=project.id,
            engine_id="fake:audio-recovery",
            separation_engine_id=None,
            fake=True,
            work_relpath=store.job_work_relpath(job_id),
        )
        written = store.write_durable_asset_bytes(
            settings,
            project_id=project.id,
            job_id=job_id,
            kind="alignment_json",
            payload=payload,
            content_type="application/json",
            ext="alignment.json",
        )
        store.insert_asset_row(
            conn,
            asset_id=written.asset_id,
            project_id=project.id,
            job_id=job_id,
            kind=written.kind,
            content_type=written.content_type,
            byte_size=written.byte_size,
            sha256_prefix=written.sha256_prefix,
            relpath=written.relpath,
        )
        assert written.kind == "alignment_json"
        assert written.absolute_path.is_file()
        project_dir = store.project_asset_dir(settings, project.id)

    project_store_mod.delete_project(project.id, db_path=db_path)
    assert not project_dir.exists()
    with get_connection(db_path) as conn:
        assert store.get_asset_row(conn, written.asset_id) is None
