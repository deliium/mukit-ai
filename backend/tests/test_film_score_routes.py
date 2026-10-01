"""HTTP film-score preview leaves the picture alone; commit stores the score."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ai_agents.registry import reload_agent_registry
from app.db.connection import reset_database_initialization_cache
from tests.fixtures.video.iso_bmff import write_iso_bmff


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "projects.db"
    asset_root = tmp_path / "video"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.setenv("VIDEO_ASSET_ROOT", str(asset_root))
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    monkeypatch.delenv("COLLABORATION_ENABLED", raising=False)
    monkeypatch.delenv("DATASET_ROOT", raising=False)
    reset_database_initialization_cache()
    reload_agent_registry()
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
    reset_database_initialization_cache()


def _picture(path: Path) -> None:
    write_iso_bmff(
        path,
        mvhd_timescale=600,
        mvhd_duration=108000,
        video={"timescale": 24, "sample_count": 4320, "media_duration": 4320},
    )


def _hits() -> list[dict]:
    return [
        {
            "id": "hit_0000000a",
            "label": "One",
            "importance": "critical",
            "video_seconds": 10,
            "musical_tick": 480,
        },
        {
            "id": "hit_0000000b",
            "label": "Two",
            "importance": "critical",
            "video_seconds": 40,
            "musical_tick": 960,
        },
        {
            "id": "hit_0000000c",
            "label": "Three",
            "importance": "critical",
            "video_seconds": 60,
            "musical_tick": 1440,
        },
    ]


def _setup(client: TestClient, tmp_path: Path, *, hits: list[dict] | None = None, rate: bool = True) -> dict:
    created = client.post("/projects", json={"name": "Scene", "composition": _empty_score()})
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    media = tmp_path / "scene.mp4"
    _picture(media)
    uploaded = client.post(
        f"/projects/{project_id}/video-asset",
        files={"file": ("scene.mp4", media.read_bytes(), "video/mp4")},
    )
    assert uploaded.status_code == 201, uploaded.text
    body = {
        "expected_document_revision": uploaded.json()["scoring"]["document_revision"],
        "timecode_mode": "non_drop",
        "start_timecode": "00:00:00:00",
        "video_origin_seconds": 0,
        "musical_origin_tick": 0,
        "hit_points": hits if hits is not None else _hits(),
    }
    if rate:
        body.update(
            {
                "frame_rate_numerator": 24,
                "frame_rate_denominator": 1,
                "frame_rate_source": "explicit",
            }
        )
    saved = client.put(f"/projects/{project_id}/video-scoring", json=body)
    assert saved.status_code == 200, saved.text
    return {
        "project_id": project_id,
        "asset_sha": uploaded.json()["asset"]["sha256_prefix"],
        "scoring": saved.json(),
    }


def _empty_score() -> dict:
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
        "tempo_changes": [],
        "time_signature_changes": [],
        "key_changes": [],
        "markers": [],
    }


def _preview_body() -> dict:
    return {
        "brief": "A quiet room and three locked hits.",
        "instruments": ["acoustic_grand_piano"],
        "opening_tempo": 120,
        "tempo_min": 96,
        "tempo_max": 132,
        "target_duration_seconds": 180,
    }


def test_preview_does_not_write_and_commit_keeps_cues(client, tmp_path):
    state = _setup(client, tmp_path)
    project_id = state["project_id"]
    before = client.get(f"/projects/{project_id}").json()
    preview = client.post(f"/projects/{project_id}/film-score/preview", json=_preview_body())
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["committed"] is False
    assert body["plan"]["tempo_strategy"]["changes"] == []
    assert body["candidate"]["bar_count"] == 90
    assert "motif_plan" in body["artifact_role_map"]
    after = client.get(f"/projects/{project_id}").json()
    assert after["working_fingerprint"] == before["working_fingerprint"]
    scoring = client.get(f"/projects/{project_id}/video-scoring").json()
    assert [hit["id"] for hit in scoring["hit_points"]] == ["hit_0000000a", "hit_0000000b", "hit_0000000c"]
    assert [hit["musical_tick"] for hit in scoring["hit_points"]] == [480, 960, 1440]
    media = client.get(f"/projects/{project_id}/video-asset")
    assert media.json()["sha256_prefix"] == state["asset_sha"]

    stale = client.post(
        f"/projects/{project_id}/film-score/commit",
        json=_commit_body(before, body, expected_document_revision=99),
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "film_score_conflict"
    still = client.get(f"/projects/{project_id}").json()
    assert still["working_fingerprint"] == before["working_fingerprint"]

    bad = client.post(
        f"/projects/{project_id}/film-score/commit",
        json=_commit_body(before, body, candidate_fingerprint="0" * 64),
    )
    assert bad.status_code == 409

    role = dict(body["artifact_role_map"])
    role.pop("motif_plan", None)
    missing_role = client.post(
        f"/projects/{project_id}/film-score/commit",
        json=_commit_body(before, body, artifact_role_map=role),
    )
    assert missing_role.status_code == 409

    committed = client.post(
        f"/projects/{project_id}/film-score/commit",
        json=_commit_body(before, body, expected_document_revision=scoring["document_revision"]),
    )
    assert committed.status_code == 200, committed.text
    assert committed.json()["committed"] is True
    stored = client.get(f"/projects/{project_id}").json()
    assert stored["composition"]["bar_count"] == 90
    assert stored["composition"]["tracks"][0]["events"]
    kept = client.get(f"/projects/{project_id}/video-scoring").json()
    assert [hit["id"] for hit in kept["hit_points"]] == ["hit_0000000a", "hit_0000000b", "hit_0000000c"]
    assert [hit["musical_tick"] for hit in kept["hit_points"]] == [480, 960, 1440]
    assert [hit["timecode"] for hit in kept["hit_points"]] == [hit["timecode"] for hit in scoring["hit_points"]]
    assert kept["musical_origin_tick"] == 0
    assert kept["video_origin_seconds"] == 0


def test_replace_required_and_missing_picture(client, tmp_path):
    noted = _empty_score()
    noted["tracks"][0]["events"] = [
        {"type": "note", "pitch": "C4", "start_tick": 0, "duration_ticks": 480, "velocity": 80},
    ]
    created = client.post("/projects", json={"name": "Noted", "composition": noted})
    project_id = created.json()["id"]
    media = tmp_path / "noted.mp4"
    _picture(media)
    uploaded = client.post(
        f"/projects/{project_id}/video-asset",
        files={"file": ("noted.mp4", media.read_bytes(), "video/mp4")},
    )
    assert uploaded.status_code == 201, uploaded.text
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
            "hit_points": _hits(),
        },
    )
    assert saved.status_code == 200, saved.text
    detail = client.get(f"/projects/{project_id}").json()
    preview = client.post(f"/projects/{project_id}/film-score/preview", json=_preview_body())
    assert preview.status_code == 200, preview.text
    blocked = client.post(
        f"/projects/{project_id}/film-score/commit",
        json=_commit_body(detail, preview.json(), expected_document_revision=saved.json()["document_revision"]),
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "film_score_replace_required"
    allowed = client.post(
        f"/projects/{project_id}/film-score/commit",
        json=_commit_body(
            detail,
            preview.json(),
            expected_document_revision=saved.json()["document_revision"],
            replace_existing=True,
        ),
    )
    assert allowed.status_code == 200, allowed.text
    bare = client.post("/projects", json={"name": "Bare", "composition": _empty_score()})
    missing = client.post(f"/projects/{bare.json()['id']}/film-score/preview", json=_preview_body())
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "film_asset_missing"


def test_frame_rate_required(client, tmp_path):
    state = _setup(client, tmp_path, rate=False)
    response = client.post(f"/projects/{state['project_id']}/film-score/preview", json=_preview_body())
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "film_frame_rate_required"


def _commit_body(detail: dict, preview: dict, **overrides) -> dict:
    body = {
        "candidate": preview["candidate"],
        "candidate_fingerprint": preview["candidate_fingerprint"],
        "replace_existing": False,
        "expected_document_revision": detail.get("document_revision", 1),
        "artifact_log": preview["artifact_log"],
        "artifact_role_map": preview["artifact_role_map"],
        "branch_id": detail["active_branch_id"],
        "expected_active_branch_id": detail["active_branch_id"],
        "expected_working_version": detail["working_version"],
        "expected_head_revision_id": detail["current_revision_id"],
        "expected_source_fingerprint": detail["working_fingerprint"],
    }
    body.update(overrides)
    return body
