"""HTTP coverage for one project picture, scoring, and the tempo map."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.composition_schemas import round_half_away_from_zero
from app.db.connection import reset_database_initialization_cache
from app.main import app
from app.services.composition_timeline import compile_timeline
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
    assert uploaded.json()["scoring"]["hit_points"] == []
    assert "suggestions" not in uploaded.json()

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
    kept_hit = saved.json()["hit_points"][0]
    assert kept_hit["video_seconds"] == 1.0
    assert kept_hit["musical_tick"] == 480
    assert kept_hit["timecode"] == "00:00:01:00"
    kept_null = client.put(
        f"/projects/{project_id}/video-scoring",
        json={
            "expected_document_revision": saved.json()["document_revision"],
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
                    "timecode": None,
                    "video_seconds": 1.0,
                    "musical_tick": 480,
                }
            ],
        },
    )
    assert kept_null.status_code == 200, kept_null.text
    assert kept_null.json()["hit_points"][0]["video_seconds"] == 1.0
    assert kept_null.json()["hit_points"][0]["musical_tick"] == 480
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


def _long_picture(path: Path) -> bytes:
    write_iso_bmff(
        path,
        mvhd_timescale=24,
        mvhd_duration=9600,
        video={
            "sample_count": 24,
            "timescale": 24,
            "media_duration": 9600,
            "width": 320,
            "height": 180,
        },
    )
    return path.read_bytes()


def _score_at(start_tick: int, note_duration: int) -> dict:
    bar_count = 112
    duration_ticks = bar_count * 1920
    return {
        "schema_version": "composition.v2",
        "tempo": 120,
        "key": "C major",
        "time_signature": "4/4",
        "ticks_per_quarter": 480,
        "bar_count": bar_count,
        "duration_ticks": duration_ticks,
        "sections": [
            {
                "id": "section-main",
                "type": "verse",
                "start_bar": 1,
                "bar_count": bar_count,
                "start_tick": 0,
                "duration_ticks": duration_ticks,
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
                "events": [
                    {
                        "type": "note",
                        "pitch": "C4",
                        "start_tick": start_tick,
                        "duration_ticks": note_duration,
                        "velocity": 80,
                    }
                ],
            }
        ],
        "harmony": [],
        "tempo_changes": [],
        "time_signature_changes": [],
        "key_changes": [],
        "markers": [],
    }


def _tick_at(seconds: float) -> int:
    timeline = compile_timeline(_score_at(0, 120))
    return round_half_away_from_zero(timeline.seconds_to_tick(seconds))


def test_spotting_put_keeps_unclamped_address_and_verify_does_not_write(
    client: TestClient,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    attack = _tick_at(222.5)
    created = client.post("/projects", json={"name": "Spot", "composition": _composition()})
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    payload = _long_picture(tmp_path / "long.mp4")
    digest = hashlib.sha256(payload).hexdigest()
    uploaded = client.post(
        f"/projects/{project_id}/video-asset",
        files={"file": ("long.mp4", payload, "video/mp4")},
    )
    assert uploaded.status_code == 201, uploaded.text
    short = client.put(
        f"/projects/{project_id}/video-scoring",
        json={
            "expected_document_revision": uploaded.json()["scoring"]["document_revision"],
            "frame_rate_numerator": 24,
            "frame_rate_denominator": 1,
            "frame_rate_source": "explicit",
            "start_timecode": "00:00:00:00",
            "hit_points": [
                {
                    "id": "hit_00aa00aa",
                    "label": "Late",
                    "timecode": "00:03:42:12",
                    "video_seconds": 1.0,
                    "musical_tick": 0,
                    "tolerance_frames": 0,
                }
            ],
        },
    )
    assert short.status_code == 200, short.text
    stored = short.json()["hit_points"][0]
    assert stored["video_seconds"] == 222.5
    assert stored["musical_tick"] == created.json()["composition"]["duration_ticks"]
    missing_cue = client.post(
        f"/projects/{project_id}/video-scoring/spotting/verify",
        json={"cue_id": "hit_0123abcd"},
    )
    assert missing_cue.status_code == 422
    assert missing_cue.json()["detail"]["code"] == "video_scoring_invalid"

    scored = client.post("/projects", json={"name": "Land", "composition": _score_at(attack, 120)})
    assert scored.status_code == 201, scored.text
    land_id = scored.json()["id"]
    land_events = scored.json()["composition"]["tracks"][0]["events"]
    land_upload = client.post(
        f"/projects/{land_id}/video-asset",
        files={"file": ("long.mp4", payload, "video/mp4")},
    )
    assert land_upload.status_code == 201, land_upload.text
    with caplog.at_level(logging.INFO):
        placed = client.put(
            f"/projects/{land_id}/video-scoring",
            json={
                "expected_document_revision": land_upload.json()["scoring"]["document_revision"],
                "frame_rate_numerator": 24,
                "frame_rate_denominator": 1,
                "frame_rate_source": "explicit",
                "start_timecode": "00:00:00:00",
                "hit_points": [
                    {
                        "id": "hit_0123abcd",
                        "label": "Door",
                        "timecode": "00:03:42:12",
                        "video_seconds": 0,
                        "musical_tick": 0,
                        "tolerance_frames": 0,
                    }
                ],
            },
        )
        assert placed.status_code == 200, placed.text
        assert placed.json()["hit_points"][0]["video_seconds"] == 222.5
        assert placed.json()["hit_points"][0]["musical_tick"] == attack
        verified = client.post(f"/projects/{land_id}/video-scoring/spotting/verify", json={})
    assert verified.status_code == 200, verified.text
    cue = verified.json()["cues"][0]
    assert cue["status"] == "landed"
    assert cue["delta_frames"] == 0
    assert any(record.message == "spotting verify finished" for record in caplog.records)
    media = client.get(f"/projects/{land_id}/video-asset/media")
    assert hashlib.sha256(media.content).hexdigest() == digest
    reread = client.get(f"/projects/{land_id}")
    assert reread.json()["composition"]["tracks"][0]["events"] == land_events

    early = _tick_at(220.0)
    sustain = client.post(
        "/projects",
        json={"name": "Sustain", "composition": _score_at(early, attack - early + 960)},
    )
    assert sustain.status_code == 201, sustain.text
    sustain_id = sustain.json()["id"]
    sustain_events = sustain.json()["composition"]["tracks"][0]["events"]
    sustain_upload = client.post(
        f"/projects/{sustain_id}/video-asset",
        files={"file": ("long.mp4", payload, "video/mp4")},
    )
    saved = client.put(
        f"/projects/{sustain_id}/video-scoring",
        json={
            "expected_document_revision": sustain_upload.json()["scoring"]["document_revision"],
            "frame_rate_numerator": 24,
            "frame_rate_denominator": 1,
            "frame_rate_source": "explicit",
            "start_timecode": "00:00:00:00",
            "hit_points": [
                {
                    "id": "hit_0123abcd",
                    "label": "Pad",
                    "timecode": "00:03:42:12",
                    "video_seconds": 222.5,
                    "musical_tick": attack,
                    "tolerance_frames": 0,
                }
            ],
        },
    )
    assert saved.status_code == 200, saved.text
    missed = client.post(f"/projects/{sustain_id}/video-scoring/spotting/verify", json={})
    assert missed.status_code == 200, missed.text
    assert missed.json()["cues"][0]["status"] == "missed"
    after = client.get(f"/projects/{sustain_id}")
    assert after.json()["composition"]["tracks"][0]["events"] == sustain_events
