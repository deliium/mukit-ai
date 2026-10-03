"""V5 matrix 11a: adaptive, continuation, context, continuous, cancel/node-fail."""

from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from tests.studio_acceptance.invariants import (
    assert_composition_source_of_truth,
    assert_score_fingerprints_unchanged,
)
from tests.studio_acceptance.v5.helpers import (
    composition_json,
    open_composition,
    seed_adaptive_playback,
)
from tests.test_adaptive_musical_context_api import _locked_mapping


def test_adaptive_game_soundtrack(v5_studio_client) -> None:
    client, _db, _tmp = v5_studio_client
    project_id, score_id, _revision = seed_adaptive_playback(client)
    playback = client.get(f"/projects/{project_id}/adaptive-scores/{score_id}/playback")
    assert playback.status_code == 200
    assert playback.json()["transport"] == "playing"
    assert playback.json()["runtime_state_id"] == "state-exploration"
    open_composition(client, project_id)


def test_runtime_generative_continuation(v5_studio_client) -> None:
    client, db_path, _tmp = v5_studio_client
    project_id, score_id, revision = seed_adaptive_playback(client)
    before = composition_json(db_path, project_id)
    started = client.post(
        f"/projects/{project_id}/adaptive-scores/{score_id}/continuation",
        json={
            "expected_document_revision": revision,
            "mode": "continuation",
            "continuous": False,
        },
    )
    assert started.status_code == 200, started.text
    maintained = client.post(
        f"/projects/{project_id}/adaptive-scores/{score_id}/continuation/maintain"
    )
    assert maintained.status_code == 200, maintained.text
    assert maintained.json()["fallback_kind"]
    assert_score_fingerprints_unchanged(
        db_path, project_id, composition_before=before
    )
    open_composition(client, project_id)


def test_external_context_api(v5_studio_client) -> None:
    client, db_path, _tmp = v5_studio_client
    project_id, score_id, revision = seed_adaptive_playback(client)
    before = composition_json(db_path, project_id)
    armed = client.post(
        f"/projects/{project_id}/adaptive-scores/{score_id}/context",
        json={"expected_document_revision": revision, "mapping": _locked_mapping()},
    )
    assert armed.status_code == 200, armed.text
    sample = client.post(
        f"/projects/{project_id}/adaptive-scores/{score_id}/context/samples",
        json={
            "schema_version": "adaptive.context.external.v1",
            "values": {"danger": 0.2},
        },
    )
    assert sample.status_code == 200, sample.text
    assert_score_fingerprints_unchanged(
        db_path, project_id, composition_before=before
    )
    open_composition(client, project_id)


def test_continuous_music(v5_studio_client) -> None:
    client, db_path, _tmp = v5_studio_client
    project_id, score_id, revision = seed_adaptive_playback(client, loop=False)
    before = composition_json(db_path, project_id)
    with sqlite3.connect(db_path) as conn:
        adaptive_before = conn.execute(
            "SELECT body_json FROM adaptive_scores WHERE project_id = ?",
            (project_id,),
        ).fetchone()[0]
    advance = client.post(
        f"/projects/{project_id}/adaptive-scores/{score_id}/playback/commands",
        json={"op": "advance", "advance_ticks": 15 * 1920},
    )
    assert advance.status_code == 200, advance.text
    started = client.post(
        f"/projects/{project_id}/adaptive-scores/{score_id}/continuation",
        json={
            "expected_document_revision": revision,
            "mode": "continuation",
            "continuous": True,
        },
    )
    assert started.status_code == 200, started.text
    maintained = client.post(
        f"/projects/{project_id}/adaptive-scores/{score_id}/continuation/maintain"
    )
    assert maintained.status_code == 200, maintained.text
    body = maintained.json()
    assert body["continuous"] is True
    assert body["music_state"] is not None
    assert body["music_state"]["virtual_bar"] >= 1
    assert any(
        item["code"] == "continuation_virtual_timeline" for item in body["warnings"]
    )
    playback = client.get(
        f"/projects/{project_id}/adaptive-scores/{score_id}/playback"
    ).json()
    assert playback["transport"] == "playing"
    assert playback["bar"] <= 16
    assert_score_fingerprints_unchanged(
        db_path,
        project_id,
        composition_before=before,
        adaptive_body_before=adaptive_before,
    )
    open_composition(client, project_id)


def test_cancel_continuation_leaves_v2_unchanged(v5_studio_client) -> None:
    client, db_path, _tmp = v5_studio_client
    project_id, score_id, revision = seed_adaptive_playback(client)
    before = composition_json(db_path, project_id)
    started = client.post(
        f"/projects/{project_id}/adaptive-scores/{score_id}/continuation",
        json={
            "expected_document_revision": revision,
            "mode": "continuation",
            "continuous": True,
        },
    )
    assert started.status_code == 200, started.text
    client.post(
        f"/projects/{project_id}/adaptive-scores/{score_id}/continuation/maintain"
    )
    deleted = client.delete(
        f"/projects/{project_id}/adaptive-scores/{score_id}/continuation"
    )
    assert deleted.status_code == 204
    assert_score_fingerprints_unchanged(
        db_path, project_id, composition_before=before
    )
    composition = open_composition(client, project_id)
    assert_composition_source_of_truth(composition)


def test_execution_node_status_without_trust_escalation(v5_studio_client) -> None:
    client, _db, _tmp = v5_studio_client
    project_id, _ = seed_adaptive_playback(client)[:2]
    listed = client.get("/ai/execution-nodes")
    # Flag on may return 200 list; never writes composition.
    assert listed.status_code in {200, 403, 503}
    open_composition(client, project_id)
