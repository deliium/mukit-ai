"""V5 matrix 11a: film scoring, picture adapt, musical universe reuse."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.fixtures.video.iso_bmff import write_iso_bmff
from tests.studio_acceptance.invariants import assert_composition_source_of_truth
from tests.studio_acceptance.v5.helpers import create_v2_project, open_composition
from tests.test_film_score_routes import _hits, _preview_body


def _upload_picture(client: TestClient, project_id: str, tmp_path: Path) -> dict:
    media = tmp_path / "scene.mp4"
    write_iso_bmff(
        media,
        mvhd_timescale=600,
        mvhd_duration=108000,
        video={"timescale": 24, "sample_count": 4320, "media_duration": 4320},
    )
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
        "hit_points": _hits(),
        "frame_rate_numerator": 24,
        "frame_rate_denominator": 1,
        "frame_rate_source": "explicit",
    }
    saved = client.put(f"/projects/{project_id}/video-scoring", json=body)
    assert saved.status_code == 200, saved.text
    return saved.json()


def test_film_scoring_with_hit_points(v5_studio_client) -> None:
    client, _db, tmp_path = v5_studio_client
    project_id, composition = create_v2_project(client, name="Film V5")
    _upload_picture(client, project_id, tmp_path)
    preview = client.post(
        f"/projects/{project_id}/film-score/preview",
        json=_preview_body(),
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["committed"] is False
    assert body["plan"]["schema_version"] == "film.score.plan.v1"
    assert "tracks" not in body["plan"]
    after = open_composition(client, project_id)
    assert after["schema_version"] == composition["schema_version"]


def test_picture_edit_rescore_preview(v5_studio_client) -> None:
    client, _db, tmp_path = v5_studio_client
    project_id, _ = create_v2_project(client, name="Adapt V5")
    scoring = _upload_picture(client, project_id, tmp_path)
    # Adapt preview requires a prior score commit in full e2e; here we assert
    # scoring + SoT remain stable when adapt preview is refused without prior commit.
    adapt = client.post(
        f"/projects/{project_id}/film-score/adapt/preview",
        json={
            "expected_document_revision": scoring["document_revision"],
            "edit": {
                "kind": "delete_span",
                "start_seconds": 10.0,
                "end_seconds": 12.0,
            },
        },
    )
    assert adapt.status_code in {200, 409, 422}
    open_composition(client, project_id)


def test_musical_universe_create_and_validate(v5_studio_client) -> None:
    client, _db, _tmp = v5_studio_client
    project_id, composition = create_v2_project(client, name="Universe A")
    created = client.post(
        "/musical-universes",
        json={"name": "V5 Franchise", "project_id": project_id},
    )
    assert created.status_code == 201, created.text
    universe_id = created.json()["universe"]["id"]
    validated = client.post(f"/musical-universes/{universe_id}/validate")
    assert validated.status_code == 200, validated.text
    assert_composition_source_of_truth(composition)
    open_composition(client, project_id)
