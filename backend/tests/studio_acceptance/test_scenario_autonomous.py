"""Scenario A: guided autonomous composition on one project."""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from tests.studio_acceptance.invariants import assert_playable_v2, event_fingerprint

_BRIEF_TEXT = "cold sparse opening studio-acceptance"
_CHECKPOINTS = ("form", "harmony", "motif", "critique", "arrangement")


def _brief() -> dict:
    return {
        "schema_version": "creative.brief.v1",
        "title": "Cinematic",
        "duration_seconds": 150,
        "narrative": [
            {"intent": "sparse_opening", "text": _BRIEF_TEXT},
            {"intent": "establish_theme", "text": "introduce Theme A"},
            {"intent": "build", "text": "increase tension"},
            {"intent": "climax", "text": "strong climax"},
            {"intent": "resolve", "text": "quiet transformed ending"},
        ],
        "instrumentation": ["piano", "cello", "strings"],
        "forbidden_instrument_families": ["drums"],
        "opening_key": "F# minor",
        "final_section_key": "F# major",
        "motif_label": "Theme A",
    }


def test_guided_run_preserves_the_project(
    studio_client: tuple[TestClient, object],
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, _db_path = studio_client
    caplog.set_level(logging.INFO)
    created = client.post("/projects", json={"name": "Studio A"})
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]

    planned = client.post("/ai/agents/autonomous/plans", json={"brief": _brief()})
    assert planned.status_code == 200, planned.text
    plan = planned.json()
    assert "tracks" not in plan
    assert "events" not in plan

    started = client.post(
        "/ai/agents/autonomous/runs",
        json={
            "brief": _brief(),
            "project_id": project_id,
            "include_rendering": False,
            "autonomy_mode": "guided",
        },
    )
    assert started.status_code == 200, started.text
    body = started.json()
    seen: list[str] = []
    while body["status"] == "awaiting_approval":
        checkpoint = body["checkpoint_id"]
        assert checkpoint in _CHECKPOINTS
        assert checkpoint not in seen
        seen.append(checkpoint)
        approved = client.post(
            f"/ai/agents/autonomous/runs/{body['run_id']}/checkpoints/{checkpoint}/approve"
        )
        assert approved.status_code == 200, approved.text
        body = approved.json()
        assert len(seen) <= len(_CHECKPOINTS)
    assert seen == list(_CHECKPOINTS)
    statuses = {stage["stage_id"]: stage["status"] for stage in body["stages"]}
    assert statuses["symbolic"] == "completed"
    assert statuses["expression"] == "completed"
    assert statuses["render"] == "skipped"

    opened = client.get(f"/projects/{project_id}")
    assert opened.status_code == 200
    composition = opened.json()["composition"]
    assert_playable_v2(composition)
    working = event_fingerprint(composition)
    head_id = opened.json()["current_revision_id"]
    assert head_id

    listed = client.get(f"/projects/{project_id}/revisions")
    assert listed.status_code == 200
    revisions = listed.json()["revisions"]
    assert revisions
    assert all("composition" not in item for item in revisions)
    assert "events" not in listed.text

    detail = client.get(f"/projects/{project_id}/revisions/{head_id}")
    assert detail.status_code == 200
    assert event_fingerprint(detail.json()["composition"]) == working

    preview = client.post(
        "/composition/development/preview",
        json={
            "composition": composition,
            "operation": "continue",
            "output_bars": 4,
            "variation_strength": "balanced",
            "development_intent": "continue",
            "candidate_count": 1,
            "selection": {"provider": "fake"},
        },
    )
    assert preview.status_code == 200, preview.text
    reopened = client.get(f"/projects/{project_id}")
    assert event_fingerprint(reopened.json()["composition"]) == working

    bar_count = int(composition["bar_count"])
    target = next(
        (
            track
            for track in composition["tracks"]
            if track.get("role") in {"harmony", "pad"} or track.get("instrument") == "piano"
        ),
        composition["tracks"][0],
    )
    end_bar = min(4, bar_count)
    edited = client.post(
        "/llm/edit-composition-region",
        json={
            "composition": composition,
            "edit": {
                "instruction": "reshape the opening bars",
                "selection": {
                    "start_bar": 1,
                    "end_bar": end_bar,
                    "track_ids": [target["id"]],
                },
            },
            "selection": {"provider": "fake"},
        },
    )
    assert edited.status_code == 200, edited.text
    ranges = [{"start_bar": 1, "end_bar": end_bar}]
    assert event_fingerprint(composition, ranges=ranges) == event_fingerprint(
        edited.json()["composition"],
        ranges=ranges,
    )
    stored = client.get(f"/projects/{project_id}")
    assert event_fingerprint(stored.json()["composition"]) == working
    assert _BRIEF_TEXT not in caplog.text
