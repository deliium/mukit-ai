"""Scenario C: the checked-in Jam take survives save, MIDI export, and undo."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from fastapi.testclient import TestClient

from tests.studio_acceptance.invariants import event_fingerprint

_GOLDEN = Path(__file__).resolve().parents[1] / "fixtures" / "studio" / "jam_committed_take.json"


def _cas(state: dict, composition: dict | None = None) -> dict:
    body = {
        "branch_id": state["active_branch_id"],
        "expected_active_branch_id": state["active_branch_id"],
        "expected_working_version": state["working_version"],
        "expected_head_revision_id": state["current_revision_id"],
        "expected_source_fingerprint": state["working_fingerprint"],
        "operation_type": "manual-checkpoint",
    }
    if composition is not None:
        body["composition"] = composition
    return body


def test_jam_take_round_trip(studio_client: tuple[TestClient, object]) -> None:
    client, _db_path = studio_client
    golden = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    created = client.post("/projects", json={"name": "Jam"})
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    patched = client.patch(f"/projects/{project_id}", json={"composition": golden})
    assert patched.status_code == 200, patched.text
    committed = client.post(
        f"/projects/{project_id}/revisions",
        json=_cas(patched.json(), golden),
    )
    assert committed.status_code == 200, committed.text
    pre_edit = committed.json()
    opened = client.get(f"/projects/{project_id}")
    assert opened.status_code == 200
    composition = opened.json()["composition"]
    assert event_fingerprint(composition) == event_fingerprint(golden)
    before = event_fingerprint(composition)

    midi = client.post("/export/midi", json=composition)
    assert midi.status_code == 200, midi.text
    assert midi.content.startswith(b"MThd")

    predict = client.post(
        "/live/accompaniment/predict",
        json={
            "schema_version": "live.accompaniment.predict.request.v1",
            "session_id": "jam-session",
            "request_id": "jam-req",
            "clock": {"tick": 0, "bar": 1, "beat": 1, "tempo": 120},
            "active_harmony": {"symbol": "C", "start_tick": 0, "duration_ticks": 1920},
            "features": {"density": 0.4, "recent_note_count": 2},
            "horizon": {"bars": 1, "ms": 2000},
            "jam_mode": "user_melody",
        },
    )
    assert predict.status_code == 200, predict.text
    after_predict = client.get(f"/projects/{project_id}")
    assert event_fingerprint(after_predict.json()["composition"]) == before

    edited = deepcopy(composition)
    edited["tracks"][0]["events"][0]["velocity"] = int(edited["tracks"][0]["events"][0]["velocity"]) + 1
    velocity_patch = client.patch(f"/projects/{project_id}", json={"composition": edited})
    assert velocity_patch.status_code == 200, velocity_patch.text
    saved_edit = client.post(
        f"/projects/{project_id}/revisions",
        json=_cas(velocity_patch.json(), edited),
    )
    assert saved_edit.status_code == 200, saved_edit.text
    restored = client.post(
        f"/projects/{project_id}/revisions/{pre_edit['current_revision_id']}/restore",
        json={
            "branch_id": saved_edit.json()["active_branch_id"],
            "expected_active_branch_id": saved_edit.json()["active_branch_id"],
            "expected_working_version": saved_edit.json()["working_version"],
            "expected_head_revision_id": saved_edit.json()["current_revision_id"],
        },
    )
    assert restored.status_code == 200, restored.text
    final = client.get(f"/projects/{project_id}")
    assert event_fingerprint(final.json()["composition"]) == before
