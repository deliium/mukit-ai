"""HTTP membership, commands, validate, and mechanical theme reuse."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import initialize_database
from app.db.connection import get_connection, reset_database_initialization_cache
from app.services.collaboration_permissions import revision_origin
from app.services.composition_snapshot_encoding import composition_snapshot_fingerprint
from app.services.project_store import create_project, get_project
from tests.test_musical_universe_bind import vector_a_score, vector_b_score


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.delenv("COLLABORATION_ENABLED", raising=False)
    monkeypatch.delenv("DATASET_ROOT", raising=False)
    reset_database_initialization_cache()
    initialize_database()
    score_a = vector_a_score()
    score_b = vector_b_score()
    create_project("Cue A", project_id="project-a", composition=score_a, db_path=db_path)
    create_project("Cue B", project_id="project-b", composition=score_b, db_path=db_path)
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client, db_path, composition_snapshot_fingerprint(score_a)
    reset_database_initialization_cache()


def _detail(response) -> dict:
    body = response.json()
    detail = body.get("detail", body)
    assert isinstance(detail, dict)
    return detail


def _branch(db_path: Path, project_id: str) -> dict[str, object]:
    with get_connection(db_path) as conn:
        row = conn.execute(
            """
            SELECT b.id, b.working_version, b.head_revision_id, b.working_fingerprint,
                   p.active_branch_id
            FROM project_branches AS b
            JOIN projects AS p ON p.id = b.project_id AND p.active_branch_id = b.id
            WHERE b.project_id = ?
            """,
            (project_id,),
        ).fetchone()
    assert row is not None
    return {
        "branch_id": row["id"],
        "expected_active_branch_id": row["active_branch_id"],
        "expected_working_version": int(row["working_version"]),
        "expected_head_revision_id": row["head_revision_id"],
        "expected_source_fingerprint": row["working_fingerprint"],
    }


def _franchise(client: TestClient, fingerprint: str) -> dict:
    created = client.post(
        "/musical-universes",
        json={"name": "Franchise", "project_id": "project-a"},
    )
    assert created.status_code == 201, created.text
    document = created.json()
    universe_id = document["universe"]["id"]
    entity = client.post(
        f"/musical-universes/{universe_id}/commands",
        json={
            "expected_document_revision": document["document_revision"],
            "command": {"op": "create_entity", "kind": "character", "label": "Ada"},
        },
    )
    assert entity.status_code == 200, entity.text
    themed_request = entity.json()
    theme = client.post(
        f"/musical-universes/{universe_id}/commands",
        json={
            "expected_document_revision": themed_request["document_revision"],
            "command": {
                "op": "create_theme",
                "entity_id": themed_request["universe"]["entities"][0]["id"],
                "label": "Theme A",
                "source": {
                    "project_id": "project-a",
                    "motif_id": "motif_theme_a",
                    "occurrence_id": "occ_original",
                },
                "source_fingerprint": fingerprint,
                "source_checked": True,
            },
        },
    )
    assert theme.status_code == 200, theme.text
    joined = client.post(
        f"/musical-universes/{universe_id}/members",
        json={"project_id": "project-b"},
    )
    assert joined.status_code == 200, joined.text
    return joined.json()


def test_create_get_join_validate_and_list_omit_body(client) -> None:
    http, _db_path, fingerprint = client
    document = _franchise(http, fingerprint)
    universe_id = document["universe"]["id"]
    by_project = http.get("/projects/project-a/musical-universe")
    assert by_project.status_code == 200
    assert by_project.json()["universe"]["themes"][0]["label"] == "Theme A"
    listed = http.get("/musical-universes")
    assert listed.status_code == 200
    assert "body_json" not in listed.text
    validated = http.post(f"/musical-universes/{universe_id}/validate")
    assert validated.status_code == 200
    assert validated.json()["findings"] == []
    assert revision_origin("musical-universe-theme-apply") == "ai"


def test_vector_g_busy_project(client) -> None:
    http, db_path, fingerprint = client
    document = _franchise(http, fingerprint)
    create_project("Cue C", project_id="project-c", composition=vector_b_score(), db_path=db_path)
    other = http.post("/musical-universes", json={"name": "Other", "project_id": "project-c"})
    assert other.status_code == 201, other.text
    busy = http.post(
        f"/musical-universes/{other.json()['universe']['id']}/members",
        json={"project_id": "project-b"},
    )
    assert busy.status_code == 409
    assert _detail(busy)["code"] == "universe_project_busy"
    assert document["universe"]["id"]


def test_vector_f_rejects_embedded_events(client) -> None:
    http, _db_path, fingerprint = client
    document = _franchise(http, fingerprint)
    revision = document["document_revision"]
    rejected = http.post(
        f"/musical-universes/{document['universe']['id']}/commands",
        json={
            "expected_document_revision": revision,
            "command": {"op": "create_entity", "kind": "character", "label": "Bea", "events": []},
        },
    )
    assert rejected.status_code == 422
    assert _detail(rejected)["code"] == "embedded_note_material"
    stored = http.get(f"/musical-universes/{document['universe']['id']}")
    assert stored.json()["document_revision"] == revision


def test_vector_b_reuse_and_stale_fingerprint_and_delete(client) -> None:
    http, db_path, fingerprint = client
    document = _franchise(http, fingerprint)
    universe_id = document["universe"]["id"]
    theme_id = document["universe"]["themes"][0]["id"]
    branch = _branch(db_path, "project-b")
    stale = dict(branch)
    stale["expected_source_fingerprint"] = "0" * 64
    stale_body = {
        **stale,
        "destination_project_id": "project-b",
        "destination_track_id": "track_melody",
        "destination_start_bar": 2,
        "operation": "transpose",
        "parameters": {"transpose_semitones": 2},
        "expected_universe_revision": document["document_revision"],
    }
    assert "composition" not in stale_body
    conflict = http.post(f"/musical-universes/{universe_id}/themes/{theme_id}/reuse", json=stale_body)
    assert conflict.status_code == 409
    assert _detail(conflict)["code"] == "universe_destination_conflict"
    assert http.get(f"/musical-universes/{universe_id}").json()["universe"]["themes"][0]["usages"] == []
    before = get_project("project-b", db_path=db_path).composition_json
    reuse_body = {
        **branch,
        "destination_project_id": "project-b",
        "destination_track_id": "track_melody",
        "destination_start_bar": 2,
        "operation": "transpose",
        "parameters": {"transpose_semitones": 2},
        "expected_universe_revision": document["document_revision"],
    }
    assert "composition" not in reuse_body
    applied = http.post(f"/musical-universes/{universe_id}/themes/{theme_id}/reuse", json=reuse_body)
    assert applied.status_code == 200, applied.text
    reused = applied.json()
    usage = reused["universe"]["themes"][0]["usages"][0]
    assert usage["operation"] == "transpose"
    assert usage["parameters"]["transpose_semitones"] == 2
    after = json.loads(get_project("project-b", db_path=db_path).composition_json or "{}")
    pitches = [
        (event["pitch"], event["start_tick"])
        for track in after["tracks"]
        for event in track["events"]
    ]
    assert ("D4", 1920) in pitches
    assert ("E4", 2400) in pitches
    assert ("F#4", 2880) in pitches
    assert ("G4", 3360) in pitches
    assert get_project("project-b", db_path=db_path).composition_json != before
    revisions = http.get("/projects/project-b/revisions")
    assert revisions.status_code == 200
    assert any(item["operation_type"] == "musical-universe-theme-apply" for item in revisions.json()["revisions"])
    deleted = http.delete(f"/musical-universes/{universe_id}")
    assert deleted.status_code == 204
    assert http.get("/projects/project-a").status_code == 200
    assert http.get("/projects/project-b").status_code == 200
