"""Adaptation preview writes nothing; commit stores only the local repair."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db.connection import reset_database_initialization_cache
from app.services.collaboration_permissions import revision_origin
from tests.film_score_adapt_fixture import base_previous, base_score, delete_span
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
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
    reset_database_initialization_cache()


def _picture(path: Path, seconds: int) -> None:
    write_iso_bmff(
        path,
        mvhd_timescale=600,
        mvhd_duration=seconds * 600,
        video={"timescale": 24, "sample_count": seconds * 24, "media_duration": seconds * 24},
    )


def _hits(later_seconds: float) -> list[dict]:
    return [
        {
            "id": "hit_aaaa0001",
            "label": "Open",
            "importance": "critical",
            "kind": "hit_point",
            "video_seconds": 6,
            "musical_tick": 5760,
            "tolerance_frames": 0,
        },
        {
            "id": "hit_bbbb0002",
            "label": "Later",
            "importance": "critical",
            "kind": "hit_point",
            "video_seconds": later_seconds,
            "musical_tick": 34560,
            "tolerance_frames": 0,
        },
    ]


def _setup(client: TestClient, tmp_path: Path, *, seconds: int, later: float) -> dict:
    created = client.post(
        "/projects",
        json={"name": "Adapt", "composition": base_score().model_dump(mode="json")},
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    media = tmp_path / "scene.mp4"
    _picture(media, seconds)
    uploaded = client.post(
        f"/projects/{project_id}/video-asset",
        files={"file": ("scene.mp4", media.read_bytes(), "video/mp4")},
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
            "hit_points": _hits(later),
        },
    )
    assert saved.status_code == 200, saved.text
    return {"project_id": project_id, "scoring": saved.json()}


def _preview_body(detail: dict) -> dict:
    return {
        "previous": base_previous().model_dump(mode="json"),
        "edits": [delete_span(16, 22).model_dump(mode="json")],
        "expected_source_fingerprint": detail["working_fingerprint"],
    }


def _commit_body(detail: dict, preview: dict, **overrides) -> dict:
    body = {
        "previous": base_previous().model_dump(mode="json"),
        "edits": [delete_span(16, 22).model_dump(mode="json")],
        "candidate": preview["candidate"],
        "candidate_fingerprint": preview["candidate_fingerprint"],
        "expected_source_fingerprint": detail["working_fingerprint"],
        "expected_document_revision": preview["proposal"]["scoring_document_revision"],
        "branch_id": detail["active_branch_id"],
        "expected_active_branch_id": detail["active_branch_id"],
        "expected_working_version": detail["working_version"],
        "expected_head_revision_id": detail["current_revision_id"],
    }
    body.update(overrides)
    return body


def _snapshot(client: TestClient, project_id: str) -> dict:
    scoring = client.get(f"/projects/{project_id}/video-scoring").json()
    asset = client.get(f"/projects/{project_id}/video-asset").json()
    return {
        "ids": [hit["id"] for hit in scoring["hit_points"]],
        "ticks": [hit["musical_tick"] for hit in scoring["hit_points"]],
        "origin_seconds": scoring["video_origin_seconds"],
        "origin_tick": scoring["musical_origin_tick"],
        "asset": asset,
    }


def test_vector_a_preview_then_commit_keeps_the_picture(client, tmp_path) -> None:
    state = _setup(client, tmp_path, seconds=58, later=30)
    project_id = state["project_id"]
    detail = client.get(f"/projects/{project_id}").json()
    picture = _snapshot(client, project_id)
    preview = client.post(
        f"/projects/{project_id}/film-score/adapt/preview",
        json=_preview_body(detail),
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["committed"] is False
    assert body["proposal"]["operations"][0]["strategy"] == "phrase_contract"
    early = [
        event["id"]
        for event in body["candidate"]["tracks"][0]["events"]
        if event["id"] in {"note_bar_01", "note_bar_02"}
    ]
    stored_events = [
        event["id"]
        for event in detail["composition"]["tracks"][0]["events"]
        if event["id"] in {"note_bar_01", "note_bar_02"}
    ]
    assert early == stored_events
    assert client.get(f"/projects/{project_id}").json()["working_fingerprint"] == detail["working_fingerprint"]
    assert _snapshot(client, project_id) == picture

    other = dict(body["candidate"])
    other["tempo"] = 96
    conflict = client.post(
        f"/projects/{project_id}/film-score/adapt/commit",
        json=_commit_body(detail, body, candidate=other, candidate_fingerprint="a" * 64),
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "film_adapt_conflict"
    assert client.get(f"/projects/{project_id}").json()["working_fingerprint"] == detail["working_fingerprint"]

    stale = client.post(
        f"/projects/{project_id}/film-score/adapt/commit",
        json=_commit_body(detail, body, expected_document_revision=99),
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "film_adapt_conflict"

    committed = client.post(
        f"/projects/{project_id}/film-score/adapt/commit",
        json=_commit_body(detail, body),
    )
    assert committed.status_code == 200, committed.text
    assert committed.json()["committed"] is True
    assert "film_origin_unchanged" in committed.json()["warnings"]
    stored = client.get(f"/projects/{project_id}").json()
    assert stored["composition"]["bar_count"] == 29
    kept = _snapshot(client, project_id)
    assert kept["ids"] == picture["ids"]
    assert kept["ticks"] == picture["ticks"]
    assert kept["origin_seconds"] == picture["origin_seconds"]
    assert kept["origin_tick"] == picture["origin_tick"]
    assert kept["asset"] == picture["asset"]


def test_vector_f_route_refuses_without_rewriting(client, tmp_path, monkeypatch) -> None:
    calls: list[str] = []

    def _blocked(*_args, **_kwargs):
        calls.append("workflow")
        raise AssertionError("film score workflow")

    monkeypatch.setattr("app.routers.film_score.run_film_score_preview", _blocked)
    state = _setup(client, tmp_path, seconds=64, later=36)
    project_id = state["project_id"]
    detail = client.get(f"/projects/{project_id}").json()
    previous = base_previous().model_dump(mode="json")
    response = client.post(
        f"/projects/{project_id}/film-score/adapt/preview",
        json={
            "previous": previous,
            "edits": [delete_span(0, 64).model_dump(mode="json")],
            "expected_source_fingerprint": detail["working_fingerprint"],
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "film_adapt_span_too_large"
    assert response.json()["detail"].get("candidate") is None
    assert client.get(f"/projects/{project_id}").json()["working_fingerprint"] == detail["working_fingerprint"]
    assert calls == []


def test_missing_picture(client) -> None:
    created = client.post(
        "/projects",
        json={"name": "Bare", "composition": base_score().model_dump(mode="json")},
    )
    detail = client.get(f"/projects/{created.json()['id']}").json()
    response = client.post(
        f"/projects/{created.json()['id']}/film-score/adapt/preview",
        json=_preview_body(detail),
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "film_adapt_asset_missing"


def test_revision_origin_is_ai() -> None:
    assert revision_origin("film-score-adapt-apply") == "ai"
