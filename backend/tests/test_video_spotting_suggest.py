"""Explicit spotting suggestion preview. Fake mode does not persist cues."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ai_runtime.operations import AiOperation, creative_chat_operations, operation_env_key
from app.db.connection import reset_database_initialization_cache
from app.main import app
from tests.test_video_scoring_routes import _bytes, _composition


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("PROJECT_DB_PATH", str(tmp_path / "projects.db"))
    monkeypatch.setenv("VIDEO_ASSET_ROOT", str(tmp_path / "video_assets"))
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    monkeypatch.delenv("DATASET_ROOT", raising=False)
    monkeypatch.delenv("COLLABORATION_ENABLED", raising=False)
    reset_database_initialization_cache()
    with TestClient(app) as test_client:
        yield test_client


def test_operation_catalog_includes_spotting_suggest() -> None:
    assert AiOperation.SPOTTING_SUGGEST in creative_chat_operations()
    assert operation_env_key(AiOperation.SPOTTING_SUGGEST) == "AI_OP_SPOTTING_SUGGEST"


def test_agents_do_not_import_video_modules() -> None:
    root = Path(__file__).resolve().parents[1] / "app" / "ai_agents"
    blob = "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.py"))
    assert "video_spotting" not in blob
    assert "llm_video_spotting" not in blob
    assert "video_scoring" not in blob


def test_fake_suggest_does_not_persist(client: TestClient, tmp_path: Path) -> None:
    created = client.post("/projects", json={"name": "Suggest", "composition": _composition()})
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    events_before = created.json()["composition"]["tracks"][0]["events"]
    payload = _bytes(tmp_path / "silent.mp4", ntsc=False)
    uploaded = client.post(
        f"/projects/{project_id}/video-asset",
        files={"file": ("silent.mp4", payload, "video/mp4")},
    )
    assert uploaded.status_code == 201, uploaded.text
    hits_before = client.get(f"/projects/{project_id}/video-scoring").json()["hit_points"]
    suggested = client.post(
        f"/projects/{project_id}/video-scoring/spotting/suggest",
        json={"brief": "door opens"},
    )
    assert suggested.status_code == 200, suggested.text
    body = suggested.json()
    assert body["schema_version"] == "video.spotting.suggestion.v1"
    assert body["persisted"] is False
    assert body["suggestions"] == [
        {
            "kind": "hit_point",
            "label": "Suggested hit",
            "timecode": "00:00:01:00",
            "tolerance_frames": 2,
            "importance": "high",
            "instruction": "",
        }
    ]
    assert client.get(f"/projects/{project_id}/video-scoring").json()["hit_points"] == hits_before
    project = client.get(f"/projects/{project_id}")
    assert project.json()["composition"]["tracks"][0]["events"] == events_before


def test_brief_over_500_is_invalid(client: TestClient, tmp_path: Path) -> None:
    created = client.post("/projects", json={"name": "Brief", "composition": _composition()})
    project_id = created.json()["id"]
    client.post(
        f"/projects/{project_id}/video-asset",
        files={"file": ("silent.mp4", _bytes(tmp_path / "silent.mp4", ntsc=False), "video/mp4")},
    )
    response = client.post(
        f"/projects/{project_id}/video-scoring/spotting/suggest",
        json={"brief": "x" * 501},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "video_spotting_brief_invalid"


def test_missing_picture_and_null_rate(client: TestClient, tmp_path: Path) -> None:
    created = client.post("/projects", json={"name": "Bare", "composition": _composition()})
    project_id = created.json()["id"]
    missing = client.post(
        f"/projects/{project_id}/video-scoring/spotting/suggest",
        json={"brief": ""},
    )
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "video_asset_missing"

    uploaded = client.post(
        f"/projects/{project_id}/video-asset",
        files={"file": ("silent.mp4", _bytes(tmp_path / "silent.mp4", ntsc=False), "video/mp4")},
    )
    assert uploaded.status_code == 201, uploaded.text
    cleared = client.put(
        f"/projects/{project_id}/video-scoring",
        json={
            "expected_document_revision": uploaded.json()["scoring"]["document_revision"],
            "frame_rate_numerator": None,
            "frame_rate_denominator": None,
            "frame_rate_source": None,
            "hit_points": [],
        },
    )
    assert cleared.status_code == 200, cleared.text
    unrated = client.post(
        f"/projects/{project_id}/video-scoring/spotting/suggest",
        json={"brief": "cut"},
    )
    assert unrated.status_code == 422
    assert unrated.json()["detail"]["code"] == "video_frame_rate_required"
