"""HTTP coverage for one project picture, scoring, and the tempo map."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db.connection import reset_database_initialization_cache
from app.main import app
from tests.fixtures.video.iso_bmff import write_iso_bmff


def _composition() -> dict:
    return {
        "schema_version": "composition.v2",
        "tempo": 120,
        "key": "C major",
        "time_signature": "4/4",
        "ticks_per_quarter": 480,
        "bar_count": 4,
        "duration_ticks": 7680,
        "sections": [
            {
                "id": "section-main",
                "type": "verse",
                "start_bar": 1,
                "bar_count": 4,
                "start_tick": 0,
                "duration_ticks": 7680,
            }
        ],
        "tracks": [
            {
                "id": "track-melody",
                "name": "Melody",
                "instrument": "piano",
                "role": "melody",
                "midi_program": 0,
                "channel": 1,
                "events": [],
            }
        ],
        "harmony": [],
        "tempo_changes": [{"tick": 3840, "bpm": 60}],
        "time_signature_changes": [],
        "key_changes": [],
        "markers": [],
    }


def _bytes(path: Path, *, ntsc: bool) -> bytes:
    if ntsc:
        write_iso_bmff(
            path,
            mvhd_timescale=30000,
            mvhd_duration=240000,
            video={
                "sample_count": 60,
                "timescale": 30000,
                "media_duration": 60060,
                "width": 640,
                "height": 360,
            },
            audio={"sample_count": 8, "timescale": 48000, "media_duration": 48000},
        )
    else:
        write_iso_bmff(
            path,
            mvhd_timescale=600,
            mvhd_duration=4800,
            video={
                "sample_count": 48,
                "timescale": 24,
                "media_duration": 48,
                "width": 320,
                "height": 180,
            },
        )
    return path.read_bytes()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("PROJECT_DB_PATH", str(tmp_path / "projects.db"))
    monkeypatch.setenv("VIDEO_ASSET_ROOT", str(tmp_path / "video_assets"))
    monkeypatch.delenv("DATASET_ROOT", raising=False)
    monkeypatch.delenv("COLLABORATION_ENABLED", raising=False)
    reset_database_initialization_cache()
    with TestClient(app) as test_client:
        yield test_client


def test_video_scoring_routes(client: TestClient, tmp_path: Path) -> None:
    created = client.post("/projects", json={"name": "Picture", "composition": _composition()})
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    before_events = created.json()["composition"]["tracks"][0]["events"]

    empty = client.get(f"/projects/{project_id}/video-scoring")
    assert empty.status_code == 200, empty.text
    assert empty.json()["document_revision"] == 0
    assert empty.json()["asset_id"] is None

    payload = _bytes(tmp_path / "silent.mp4", ntsc=False)
    digest = hashlib.sha256(payload).hexdigest()
    uploaded = client.post(
        f"/projects/{project_id}/video-asset",
        files={"file": ("silent.mp4", payload, "video/mp4")},
    )
    assert uploaded.status_code == 201, uploaded.text
    first_id = uploaded.json()["asset"]["asset_id"]
    assert uploaded.json()["asset"]["sha256_prefix"] == digest[:16]
    assert uploaded.json()["scoring"]["frame_rate_source"] == "probed"

    media = client.get(f"/projects/{project_id}/video-asset/media")
    assert media.status_code == 200
    assert media.headers["content-length"] == str(len(payload))
    assert hashlib.sha256(media.content).hexdigest() == digest
    partial = client.get(
        f"/projects/{project_id}/video-asset/media",
        headers={"Range": "bytes=0-3"},
    )
    assert partial.status_code == 206
    assert partial.content == payload[:4]

    saved = client.put(
        f"/projects/{project_id}/video-scoring",
        json={
            "expected_document_revision": uploaded.json()["scoring"]["document_revision"],
            "frame_rate_numerator": 24,
            "frame_rate_denominator": 1,
            "frame_rate_source": "explicit",
            "timecode_mode": "non_drop",
            "start_timecode": "00:00:00:00",
            "video_origin_seconds": 0,
            "musical_origin_tick": 0,
            "hit_points": [
                {
                    "id": "hit_0123abcd",
                    "label": "Hit",
                    "video_seconds": 1.0,
                    "musical_tick": 480,
                }
            ],
        },
    )
    assert saved.status_code == 200, saved.text
    media_again = client.get(f"/projects/{project_id}/video-asset/media")
    assert hashlib.sha256(media_again.content).hexdigest() == digest

    mapped = client.get(f"/projects/{project_id}/video-scoring/map", params={"video_seconds": 6})
    assert mapped.status_code == 200, mapped.text
    body = mapped.json()
    assert body["tick"] == 4800
    assert body["tick"] != 5760
    assert body["timecode"] == "00:00:06:00"

    both = client.get(
        f"/projects/{project_id}/video-scoring/map",
        params={"video_seconds": 1, "tick": 10},
    )
    assert both.status_code == 422
    assert both.json()["detail"]["code"] == "video_map_selector_invalid"

    deleted = client.delete(f"/projects/{project_id}/video-asset")
    assert deleted.status_code == 204
    missing = client.get(f"/projects/{project_id}/video-asset")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "video_asset_missing"
    kept = client.get(f"/projects/{project_id}/video-scoring")
    assert kept.json()["frame_rate_numerator"] == 24
    assert kept.json()["frame_rate_source"] == "explicit"
    assert kept.json()["hit_points"] == []
    assert kept.json()["asset_id"] is None

    ntsc = _bytes(tmp_path / "ntsc.mp4", ntsc=True)
    replaced = client.post(
        f"/projects/{project_id}/video-asset",
        files={"file": ("ntsc.mp4", ntsc, "video/mp4")},
    )
    assert replaced.status_code == 201, replaced.text
    assert replaced.json()["asset"]["asset_id"] != first_id
    assert replaced.json()["scoring"]["frame_rate_source"] == "explicit"
    assert replaced.json()["asset"]["has_audio"] is True

    project = client.get(f"/projects/{project_id}")
    assert project.status_code == 200
    assert project.json()["composition"]["tracks"][0]["events"] == before_events
