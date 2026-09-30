"""Video asset store, scoring CAS, and project-delete cleanup."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app.db.connection import get_connection, reset_database_initialization_cache
from app.services import project_store as project_store_mod
from app.services import video_scoring_store as store
from app.storage_root_policy import StorageRootError
from app.video_scoring_schemas import VideoScoringError, VideoScoringUpdateV1
from app.video_scoring_settings import load_video_scoring_settings
from tests.fixtures.video.iso_bmff import write_iso_bmff


@pytest.fixture
def video_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.setenv("VIDEO_ASSET_ROOT", str(tmp_path / "video_assets"))
    monkeypatch.delenv("DATASET_ROOT", raising=False)
    reset_database_initialization_cache()
    return tmp_path


def _clip(path: Path, **video) -> bytes:
    spec = {
        "sample_count": 48,
        "timescale": 24,
        "media_duration": 48,
        "width": 320,
        "height": 180,
    }
    spec.update(video)
    write_iso_bmff(path, mvhd_timescale=600, mvhd_duration=1200, video=spec)
    return path.read_bytes()


def _ntsc(path: Path) -> bytes:
    write_iso_bmff(
        path,
        mvhd_timescale=30000,
        mvhd_duration=60060,
        video={
            "sample_count": 60,
            "timescale": 30000,
            "media_duration": 60060,
            "width": 640,
            "height": 360,
        },
        audio={"sample_count": 8, "timescale": 48000, "media_duration": 48000},
    )
    return path.read_bytes()


def _count_scoring(db_path: Path, project_id: str) -> int:
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM video_scoring WHERE project_id = ?",
            (project_id,),
        ).fetchone()
    return int(row["n"])


def test_default_root_beside_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db = tmp_path / "data" / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db))
    monkeypatch.delenv("VIDEO_ASSET_ROOT", raising=False)
    settings = load_video_scoring_settings()
    assert settings.asset_root == db.parent / "video_assets"
    assert settings.max_upload_bytes == 256 * 1024 * 1024


def test_upload_cap_is_clamped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PROJECT_DB_PATH", str(tmp_path / "projects.db"))
    monkeypatch.setenv("VIDEO_ASSET_ROOT", str(tmp_path / "video_assets"))
    monkeypatch.setenv("VIDEO_ASSET_MAX_UPLOAD_BYTES", "10")
    settings = load_video_scoring_settings()
    assert settings.max_upload_bytes == 1024 * 1024


def test_dataset_root_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    shared = tmp_path / "datasets"
    shared.mkdir()
    monkeypatch.setenv("PROJECT_DB_PATH", str(tmp_path / "projects.db"))
    monkeypatch.setenv("DATASET_ROOT", str(shared))
    monkeypatch.setenv("VIDEO_ASSET_ROOT", str(shared))
    with pytest.raises(StorageRootError):
        load_video_scoring_settings()


def test_get_without_row_is_revision_zero(video_env: Path) -> None:
    db_path = video_env / "projects.db"
    project = project_store_mod.create_project(name="Picture", db_path=db_path)
    document = store.get_video_scoring(project.id, db_path=db_path)
    assert document.document_revision == 0
    assert document.asset_id is None
    assert _count_scoring(db_path, project.id) == 0


def test_replace_keeps_explicit_rate_and_hash(video_env: Path) -> None:
    db_path = video_env / "projects.db"
    project = project_store_mod.create_project(name="Picture", db_path=db_path)
    first = _clip(video_env / "a.mp4")
    asset, scoring = store.ingest_video_bytes(first, project_id=project.id, db_path=db_path)
    stored = store.video_media_path(project.id, db_path=db_path)[1]
    digest = hashlib.sha256(stored.read_bytes()).hexdigest()
    updated = store.put_video_scoring(
        project.id,
        VideoScoringUpdateV1.model_validate(
            {
                "expected_document_revision": scoring.document_revision,
                "frame_rate_numerator": 25,
                "frame_rate_denominator": 1,
                "frame_rate_source": "explicit",
                "timecode_mode": "non_drop",
                "start_timecode": "01:00:00:00",
                "video_origin_seconds": 0.5,
                "musical_origin_tick": 480,
                "hit_points": [
                    {
                        "id": "hit_0123abcd",
                        "label": "Cut",
                        "video_seconds": 1.0,
                        "musical_tick": 960,
                    }
                ],
            }
        ),
        db_path=db_path,
    )
    assert hashlib.sha256(stored.read_bytes()).hexdigest() == digest
    with pytest.raises(VideoScoringError) as conflict:
        store.put_video_scoring(
            project.id,
            VideoScoringUpdateV1.model_validate(
                {
                    "expected_document_revision": scoring.document_revision,
                    "frame_rate_numerator": 25,
                    "frame_rate_denominator": 1,
                    "frame_rate_source": "explicit",
                }
            ),
            db_path=db_path,
        )
    assert conflict.value.code == "video_scoring_conflict"

    second = _ntsc(video_env / "b.mp4")
    replaced, scoring_after = store.ingest_video_bytes(second, project_id=project.id, db_path=db_path)
    assert replaced.asset_id != asset.asset_id
    assert scoring_after.frame_rate_numerator == 25
    assert scoring_after.frame_rate_source == "explicit"
    assert scoring_after.start_timecode == "01:00:00:00"
    assert not stored.exists()
    assert store.video_media_path(project.id, db_path=db_path)[1].is_file()
    directory = video_env / "video_assets" / project.id
    assert len(list(directory.iterdir())) == 1


def test_failed_commit_keeps_previous_file(video_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = video_env / "projects.db"
    project = project_store_mod.create_project(name="Picture", db_path=db_path)
    store.ingest_video_bytes(_clip(video_env / "a.mp4"), project_id=project.id, db_path=db_path)
    previous = store.video_media_path(project.id, db_path=db_path)[1]

    def _boom(*_args, **_kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(store, "get_connection", _boom)
    with pytest.raises(RuntimeError):
        store.ingest_video_bytes(_ntsc(video_env / "b.mp4"), project_id=project.id, db_path=db_path)
    assert previous.is_file()
    directory = video_env / "video_assets" / project.id
    names = {path.name for path in directory.iterdir()}
    assert previous.name in names
    assert all(not name.endswith(".part") for name in names)


def test_failed_probe_leaves_previous(video_env: Path) -> None:
    db_path = video_env / "projects.db"
    project = project_store_mod.create_project(name="Picture", db_path=db_path)
    asset, _scoring = store.ingest_video_bytes(
        _clip(video_env / "a.mp4"), project_id=project.id, db_path=db_path
    )
    with pytest.raises(VideoScoringError):
        store.ingest_video_bytes(b"not-a-movie", project_id=project.id, db_path=db_path)
    current = store.get_video_asset(project.id, db_path=db_path)
    assert current.asset_id == asset.asset_id


def test_delete_project_removes_directory(video_env: Path) -> None:
    db_path = video_env / "projects.db"
    project = project_store_mod.create_project(name="Picture", db_path=db_path)
    store.ingest_video_bytes(_clip(video_env / "a.mp4"), project_id=project.id, db_path=db_path)
    directory = video_env / "video_assets" / project.id
    assert directory.is_dir()
    project_store_mod.delete_project(project.id, db_path=db_path)
    assert not directory.exists()
