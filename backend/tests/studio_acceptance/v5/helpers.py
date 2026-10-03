"""Shared project/adaptive helpers for the V5 studio matrix."""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi.testclient import TestClient

from tests.studio_acceptance.invariants import assert_composition_source_of_truth
from tests.test_adaptive_playback import scenario_score
from tests.test_adaptive_playback_api import _composition


def _playable_composition() -> dict[str, Any]:
    """Adaptive fixture plus one seed note so SoT invariants hold."""
    composition = _composition()
    tracks = composition.get("tracks") or []
    assert tracks
    tracks[0] = {
        **tracks[0],
        "events": [
            {
                "id": "v5-seed-note",
                "pitch": "C4",
                "start_tick": 0,
                "duration_ticks": 480,
                "velocity": 80,
            }
        ],
    }
    return composition


def create_v2_project(client: TestClient, *, name: str = "V5 Studio") -> tuple[str, dict[str, Any]]:
    created = client.post(
        "/projects",
        json={"name": name, "composition": _playable_composition()},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    composition = body["composition"]
    assert_composition_source_of_truth(composition)
    return body["id"], composition


def open_composition(client: TestClient, project_id: str) -> dict[str, Any]:
    opened = client.get(f"/projects/{project_id}")
    assert opened.status_code == 200, opened.text
    composition = opened.json()["composition"]
    assert_composition_source_of_truth(composition)
    return composition


def seed_adaptive_playback(
    client: TestClient,
    *,
    loop: bool = True,
) -> tuple[str, str, int]:
    project_id, _ = create_v2_project(client, name="Adaptive V5")
    score = scenario_score()
    if not loop:
        from copy import deepcopy

        score = deepcopy(score)
        score["states"][0]["loop"]["enabled"] = False
        for transition in score["transitions"]:
            if (
                transition["quantization"] == "loop_end"
                and transition["from_state_id"] == "state-exploration"
            ):
                transition["quantization"] = "bar"
    posted = client.post(
        f"/projects/{project_id}/adaptive-scores",
        json={"is_default": True, "score": score},
    )
    assert posted.status_code == 201, posted.text
    body = posted.json()
    score_id = body["score"]["id"]
    revision = body["document_revision"]
    started = client.post(
        f"/projects/{project_id}/adaptive-scores/{score_id}/playback",
        json={"expected_document_revision": revision, "mode": "simulation"},
    )
    assert started.status_code == 200, started.text
    assert started.json()["transport"] == "playing"
    return project_id, score_id, revision


def composition_json(db_path, project_id: str) -> str:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT composition_json FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()
    assert row is not None
    return row[0]
