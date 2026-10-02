"""HTTP impact, graph, and edge refresh. These routes do not rewrite notes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.composition_schemas import CompositionV2, CompositionV2NoteEvent
from app.db.connection import get_connection, reset_database_initialization_cache
from app.db import initialize_database
from app.main import app
from app.musical_dependency_schemas import FORBIDDEN_NOTE_KEYS
from app.services import musical_dependency_store as edge_store
from app.services.collaboration_store import create_actor, grant_member
from app.services.composition_snapshot_encoding import (
    composition_snapshot_fingerprint,
    motif_occurrence_fingerprint,
)
from app.services.musical_universe_store import add_member, get_universe, update_universe_document
from app.services.project_store import create_project, get_project, update_project
from tests.test_musical_universe_bind import vector_a_score, vector_b_score
from tests.test_musical_universe_reuse import _franchise, _reuse

_LABELS = {
    "project-b": "Exploration variation",
    "project-c": "Combat variation",
    "project-d": "Finale transformation",
}


@pytest.fixture
def project_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.delenv("COLLABORATION_ENABLED", raising=False)
    reset_database_initialization_cache()
    initialize_database()
    return db_path


@pytest.fixture
def client(project_db: Path):
    with TestClient(app) as http:
        yield http


def _keys(value, found: set[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            found.add(key)
            _keys(item, found)
    elif isinstance(value, list):
        for item in value:
            _keys(item, found)


def _assert_no_note_keys(payload: dict) -> None:
    found: set[str] = set()
    _keys(payload, found)
    assert found.isdisjoint(FORBIDDEN_NOTE_KEYS)


def _fingerprints(db_path: Path) -> dict[str, str]:
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT project_id, working_fingerprint
            FROM project_branches
            WHERE project_id IN ('project-b', 'project-c', 'project-d')
            """
        ).fetchall()
    return {row["project_id"]: row["working_fingerprint"] for row in rows}


def _seed(db_path: Path):
    record = _franchise(db_path)
    create_project("Cue C", project_id="project-c", composition=vector_b_score(), db_path=db_path)
    create_project("Cue D", project_id="project-d", composition=vector_b_score(), db_path=db_path)
    add_member(record.id, "project-c", db_path=db_path)
    add_member(record.id, "project-d", db_path=db_path)
    theme_id = record.universe.themes[0].id
    for project_id in ("project-b", "project-c", "project-d"):
        latest = get_universe(record.id, db_path=db_path)
        _reuse(db_path, record.id, theme_id, project_id=project_id, revision=latest.document_revision)
    latest = get_universe(record.id, db_path=db_path)
    theme = latest.universe.themes[0]
    renamed = []
    for variant in theme.variants:
        usage = next(item for item in theme.usages if item.variant_id == variant.id)
        renamed.append(variant.model_copy(update={"label": _LABELS[usage.destination_project_id]}))
    universe = latest.universe.model_copy(
        update={"themes": [theme.model_copy(update={"variants": renamed})]}
    )
    stored = update_universe_document(
        universe,
        expected_revision=latest.document_revision,
        db_path=db_path,
    )
    return stored


def _events(db_path: Path, project_id: str) -> list:
    record = get_project(project_id, db_path=db_path)
    return json.loads(record.composition_json)["tracks"][0]["events"]


def test_impact_and_graph_leave_scores_unchanged(client: TestClient, project_db: Path) -> None:
    record = _seed(project_db)
    theme_id = record.universe.themes[0].id
    before_revision = record.document_revision
    before_fp = _fingerprints(project_db)
    before_events = {project_id: _events(project_db, project_id) for project_id in _LABELS}

    fresh = client.get(f"/musical-universes/{record.id}/themes/{theme_id}/dependents")
    assert fresh.status_code == 200, fresh.text
    body = fresh.json()
    _assert_no_note_keys(body)
    assert {item["status"] for item in body["dependents"]} == {"fresh"}
    assert get_universe(record.id, db_path=project_db).document_revision == before_revision
    assert _fingerprints(project_db) == before_fp

    graph = client.get(f"/musical-universes/{record.id}/dependency-graph")
    assert graph.status_code == 200, graph.text
    graph_body = graph.json()
    _assert_no_note_keys(graph_body)
    labels = {node["label"] for node in graph_body["nodes"]}
    assert "Theme A" in labels
    assert set(_LABELS.values()).issubset(labels)
    theme_key = f"theme:{record.id}:{theme_id}"
    children = [
        edge["downstream_node_key"]
        for edge in graph_body["edges"]
        if edge["upstream_node_key"] == theme_key and edge["dependency_type"] == "variation_of"
    ]
    assert len(children) == 3
    assert len(set(children)) == 3

    edited = vector_a_score().model_dump(mode="json")
    edited["tracks"][0]["events"][3]["pitch"] = "G4"
    update_project("project-a", composition=edited, db_path=project_db)
    stale = client.get(f"/musical-universes/{record.id}/themes/{theme_id}/dependents")
    assert stale.status_code == 200, stale.text
    by_label = {item["label"]: item["status"] for item in stale.json()["dependents"]}
    assert by_label["Exploration variation"] == "stale"
    assert by_label["Combat variation"] == "stale"
    assert by_label["Finale transformation"] == "stale"
    for project_id, events in before_events.items():
        assert _events(project_db, project_id) == events


def test_accept_current_refreshes_one_edge(client: TestClient, project_db: Path) -> None:
    record = _seed(project_db)
    theme_id = record.universe.themes[0].id
    edited = vector_a_score().model_dump(mode="json")
    edited["tracks"][0]["events"][3]["pitch"] = "G4"
    update_project("project-a", composition=edited, db_path=project_db)
    before = _events(project_db, "project-b")
    report = client.get(f"/musical-universes/{record.id}/themes/{theme_id}/dependents")
    exploration = next(item for item in report.json()["dependents"] if item["label"] == "Exploration variation")
    accepted = client.post(f"/musical-dependency/edges/{exploration['edge_id']}/accept-current")
    assert accepted.status_code == 200, accepted.text
    _assert_no_note_keys(accepted.json())
    again = client.get(f"/musical-universes/{record.id}/themes/{theme_id}/dependents")
    statuses = {item["label"]: item["status"] for item in again.json()["dependents"]}
    assert statuses["Exploration variation"] == "fresh"
    assert statuses["Combat variation"] == "stale"
    assert statuses["Finale transformation"] == "stale"
    assert _events(project_db, "project-b") == before

    observed = "ab" * 32
    conflict = client.post(
        f"/musical-dependency/edges/{exploration['edge_id']}/record-refresh",
        json={"observed_downstream_fingerprint": observed},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "dependency_refresh_conflict"
    stored = edge_store.get_dependency_edge(exploration["edge_id"], db_path=project_db)
    assert stored.upstream_fingerprint == accepted.json()["upstream_fingerprint"]

    destination = get_project("project-b", db_path=project_db)
    composition = json.loads(destination.composition_json)
    motif = composition["motifs"][0]
    occurrence = motif["occurrences"][0]
    by_id = {event["id"]: event for event in composition["tracks"][0]["events"]}
    events = [CompositionV2NoteEvent.model_validate(by_id[event_id]) for event_id in occurrence["event_ids"]]
    digest = motif_occurrence_fingerprint(events)
    refreshed = client.post(
        f"/musical-dependency/edges/{exploration['edge_id']}/record-refresh",
        json={"observed_downstream_fingerprint": digest},
    )
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["id"] == exploration["edge_id"]
    live = composition_snapshot_fingerprint(CompositionV2.model_validate(edited))
    assert refreshed.json()["upstream_fingerprint"] == live


def test_viewer_read_is_not_read_score(
    client: TestClient,
    project_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _seed(project_db)
    theme_id = record.universe.themes[0].id
    monkeypatch.setenv("COLLABORATION_ENABLED", "1")
    actor = create_actor("Viewer", db_path=project_db)
    for project_id in ("project-a", "project-b", "project-c", "project-d"):
        grant_member(project_id, actor.id, "viewer", db_path=project_db)
    actions: list[str] = []
    from app.routers import musical_dependency as routes

    original = routes.enforce_current

    def _spy(project_id: str | None, action: str):
        actions.append(action)
        return original(project_id, action)

    monkeypatch.setattr(routes, "enforce_current", _spy)
    allowed = client.get(
        f"/musical-universes/{record.id}/themes/{theme_id}/dependents",
        headers={"X-Mukit-Actor": actor.id},
    )
    assert allowed.status_code == 200, allowed.text
    assert actions
    assert set(actions) == {"read"}
    denied = client.post(
        f"/musical-dependency/edges/{allowed.json()['dependents'][0]['edge_id']}/accept-current",
        headers={"X-Mukit-Actor": actor.id},
    )
    assert denied.status_code == 403
    assert "read_score" not in actions
