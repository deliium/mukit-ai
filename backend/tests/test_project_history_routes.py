"""HTTP acceptance for revision/branch history, storage efficiency, and preview hygiene."""

from __future__ import annotations

import json
import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from app.db import reset_database_initialization_cache
from app.db.connection import get_connection
from app.main import app
from app.composition_schemas import CompositionV2
from app.services.composition_snapshot_encoding import encode_composition_snapshot
from tests.test_composition_development_patch import _sixteen_bar_a
from tests.test_composition_v2_schema import minimal_v2


logger = logging.getLogger(__name__)


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "history-routes.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    reset_database_initialization_cache()
    with TestClient(app) as test_client:
        yield test_client, db_path


def _create_project(client: TestClient, name: str = "History Routes", composition=None):
    payload = {"name": name}
    if composition is not None:
        payload["composition"] = composition
    response = client.post("/projects", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _revision_count(db_path, project_id: str) -> int:
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM project_revisions WHERE project_id = ?",
            (project_id,),
        ).fetchone()
    return int(row["c"])


def _snapshot_row(db_path, fingerprint: str):
    with get_connection(db_path) as conn:
        return conn.execute(
            """
            SELECT fingerprint, uncompressed_byte_size, compressed_byte_size, payload_zlib
            FROM composition_snapshots
            WHERE fingerprint = ?
            """,
            (fingerprint,),
        ).fetchone()


def _note_composition(pitch: str = "C4", **overrides) -> dict:
    composition = minimal_v2(**overrides)
    composition["tracks"][0]["events"] = [
        {
            "type": "note",
            "id": "n0",
            "pitch": pitch,
            "start_tick": 0,
            "duration_ticks": 480,
            "velocity": 80,
        }
    ]
    return composition


def _large_composition(note_count: int = 4000) -> dict:
    composition = minimal_v2()
    events = []
    for index in range(note_count):
        events.append(
            {
                "type": "note",
                "id": f"n{index}",
                "pitch": "C4",
                "start_tick": (index % 64) * 120,
                "duration_ticks": 120,
                "velocity": 80 + (index % 20),
            }
        )
    composition["tracks"][0]["events"] = events
    composition["bar_count"] = 16
    composition["duration_ticks"] = 16 * 1920
    composition["sections"] = [
        {
            "type": "verse",
            "start_bar": 1,
            "bar_count": 16,
            "start_tick": 0,
            "duration_ticks": 16 * 1920,
        }
    ]
    return composition


def test_list_revisions_metadata_only_and_secret_instruction_rejected(client):
    http, db_path = client
    created = _create_project(http, composition=minimal_v2())
    project_id = created["id"]
    before = _revision_count(db_path, project_id)

    listed = http.get(f"/projects/{project_id}/revisions")
    assert listed.status_code == 200
    body = listed.json()
    assert "revisions" in body
    assert body["revisions"]
    first = body["revisions"][0]
    assert "composition" not in first
    assert "user_instruction" not in first
    assert "snapshot_json" not in first
    assert "has_user_instruction" in first

    secret_instruction = "sk-abcdefghijklmnopqrstuvwxyz012345"
    rejected = http.post(
        f"/projects/{project_id}/revisions",
        json={
            "branch_id": created["active_branch_id"],
            "expected_active_branch_id": created["active_branch_id"],
            "expected_working_version": created["working_version"],
            "expected_head_revision_id": created["current_revision_id"],
            "expected_source_fingerprint": created["working_fingerprint"],
            "composition": minimal_v2(),
            "operation_type": "manual-checkpoint",
            "ai": {
                "provider": "fake",
                "model": "fake-deterministic",
                "user_instruction": secret_instruction,
            },
        },
    )
    assert rejected.status_code == 422
    detail_text = json.dumps(rejected.json()).lower()
    assert "forbidden_secret_value" in detail_text
    assert _revision_count(db_path, project_id) == before
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM project_revisions WHERE project_id = ? AND user_instruction IS NOT NULL",
            (project_id,),
        ).fetchone()
    assert int(row["c"]) == 0

def test_large_composition_zlib_dedupe_and_draft_no_revision_explosion(client):
    http, db_path = client
    large = _large_composition(3500)
    encoded = encode_composition_snapshot(CompositionV2.model_validate(large))
    assert encoded.compressed_byte_size < encoded.uncompressed_byte_size
    assert encoded.compressed_byte_size / max(encoded.uncompressed_byte_size, 1) < 0.85
    logger.info(
        "Large snapshot compression measured",
        extra={
            "note_count": 3500,
            "uncompressed": encoded.uncompressed_byte_size,
            "compressed": encoded.compressed_byte_size,
            "fingerprint_prefix": encoded.fingerprint.split(":")[-1][:12],
        },
    )

    created = _create_project(http, name="Large", composition=large)
    project_id = created["id"]
    before_revisions = _revision_count(db_path, project_id)
    assert before_revisions >= 1

    # Repeated identical durable checkpoints must not explode snapshot rows.
    for _ in range(3):
        opened = http.get(f"/projects/{project_id}").json()
        checkpoint = http.post(
            f"/projects/{project_id}/revisions",
            json={
                "branch_id": opened["active_branch_id"],
                "expected_active_branch_id": opened["active_branch_id"],
                "expected_working_version": opened["working_version"],
                "expected_head_revision_id": opened["current_revision_id"],
                "expected_source_fingerprint": opened["working_fingerprint"],
                "composition": large,
                "operation_type": "manual-checkpoint",
            },
        )
        assert checkpoint.status_code == 200, checkpoint.text
        assert checkpoint.json()["revision_created"] is False

    after_noop = _revision_count(db_path, project_id)
    assert after_noop == before_revisions

    snap = _snapshot_row(db_path, created["working_fingerprint"])
    assert snap is not None
    assert int(snap["compressed_byte_size"]) < int(snap["uncompressed_byte_size"])
    assert len(snap["payload_zlib"]) == int(snap["compressed_byte_size"])

    # Draft PATCH autosaves overwrite working draft without new revisions.
    draft = deepcopy(large)
    for index in range(5):
        draft["tracks"][0]["events"][0]["velocity"] = 70 + index
        opened = http.get(f"/projects/{project_id}").json()
        patched = http.patch(
            f"/projects/{project_id}",
            json={
                "composition": draft,
                "branch_id": opened["active_branch_id"],
                "expected_active_branch_id": opened["active_branch_id"],
                "expected_working_version": opened["working_version"],
            },
        )
        assert patched.status_code == 200, patched.text

    assert _revision_count(db_path, project_id) == before_revisions


def test_development_preview_does_not_write_revisions(client):
    http, db_path = client
    composition = _sixteen_bar_a().model_dump(mode="json")
    created = _create_project(http, name="Preview Stateless", composition=composition)
    project_id = created["id"]
    before = _revision_count(db_path, project_id)

    preview = http.post(
        "/composition/development/preview",
        json={
            "composition": composition,
            "operation": "vary_section",
            "source": {"start_bar": 13, "end_bar": 16},
            "variation_strength": "balanced",
            "development_intent": "develop",
            "candidate_count": 3,
            "instruction": "darker harmony",
            "selection": {"provider": "fake", "model": "fake-deterministic"},
            "options": {"max_repairs": 1, "context_budget_chars": 8000},
        },
    )
    assert preview.status_code == 200, preview.text
    assert len(preview.json()["candidates"]) == 3
    assert _revision_count(db_path, project_id) == before


def test_concurrent_durable_commits_one_wins(client):
    http, db_path = client
    created = _create_project(http, composition=_note_composition("C4"))
    project_id = created["id"]
    base = http.get(f"/projects/{project_id}").json()

    def attempt(pitch: str):
        return http.post(
            f"/projects/{project_id}/revisions",
            json={
                "branch_id": base["active_branch_id"],
                "expected_active_branch_id": base["active_branch_id"],
                "expected_working_version": base["working_version"],
                "expected_head_revision_id": base["current_revision_id"],
                "expected_source_fingerprint": base["working_fingerprint"],
                "composition": _note_composition(pitch),
                "operation_type": "manual-checkpoint",
            },
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(attempt, "D4")
        second = pool.submit(attempt, "E4")
        results = [first.result(), second.result()]

    statuses = sorted(item.status_code for item in results)
    assert statuses == [200, 409]
    winners = [item for item in results if item.status_code == 200]
    conflicts = [item for item in results if item.status_code == 409]
    assert winners and conflicts
    conflict_detail = conflicts[0].json()["detail"]
    assert conflict_detail["code"] == "project_revision_conflict"
    assert "composition" not in conflict_detail
    assert "events" not in json.dumps(conflict_detail)
    assert _revision_count(db_path, project_id) == 2


def test_restart_simulation_preserves_branches_and_restore(client, caplog):
    http, _db_path = client
    created = _create_project(http, composition=_note_composition("C4"))
    project_id = created["id"]
    root_revision = created["current_revision_id"]

    edited = _note_composition("D4", tempo=111)
    committed = http.post(
        f"/projects/{project_id}/revisions",
        json={
            "branch_id": created["active_branch_id"],
            "expected_active_branch_id": created["active_branch_id"],
            "expected_working_version": created["working_version"],
            "expected_head_revision_id": created["current_revision_id"],
            "expected_source_fingerprint": created["working_fingerprint"],
            "composition": edited,
            "operation_type": "manual-checkpoint",
        },
    )
    assert committed.status_code == 200
    mid = committed.json()

    branched = http.post(
        f"/projects/{project_id}/branches/apply-as-branch",
        json={
            "name": "Darker harmony",
            "source_branch_id": mid["active_branch_id"],
            "expected_active_branch_id": mid["active_branch_id"],
            "expected_working_version": mid["working_version"],
            "expected_head_revision_id": mid["current_revision_id"],
            "expected_source_fingerprint": mid["working_fingerprint"],
            "composition": _note_composition("E4", tempo=90),
            "operation_type": "development-apply",
        },
    )
    assert branched.status_code == 200, branched.text
    darker = branched.json()
    assert darker["active_branch_name"] == "Darker harmony"

    # Simulate process restart: re-open DB connection path via cache reset.
    reset_database_initialization_cache()
    with caplog.at_level(logging.INFO):
        reopened = http.get(f"/projects/{project_id}")
    assert reopened.status_code == 200
    body = reopened.json()
    assert body["active_branch_name"] == "Darker harmony"
    assert body["composition"]["tempo"] == 90
    assert body["current_revision_id"] == darker["current_revision_id"]

    branches = http.get(f"/projects/{project_id}/branches").json()["branches"]
    original = next(item for item in branches if item["name"] == "Original")
    assert original["head_revision_id"] == mid["current_revision_id"]

    checked = http.post(
        f"/projects/{project_id}/branches/{original['id']}/checkout",
        json={
            "expected_active_branch_id": body["active_branch_id"],
            "expected_working_version": body["working_version"],
            "expected_head_revision_id": body["current_revision_id"],
        },
    )
    assert checked.status_code == 200
    assert checked.json()["active_branch_name"] == "Original"
    assert checked.json()["composition"]["tempo"] == 111

    restored = http.post(
        f"/projects/{project_id}/revisions/{root_revision}/restore",
        json={
            "branch_id": checked.json()["active_branch_id"],
            "expected_active_branch_id": checked.json()["active_branch_id"],
            "expected_working_version": checked.json()["working_version"],
            "expected_head_revision_id": checked.json()["current_revision_id"],
        },
    )
    assert restored.status_code == 200
    assert restored.json()["operation_type"] == "revision-restore"
    assert restored.json()["composition"]["tempo"] == minimal_v2()["tempo"]

    joined = " ".join(record.message for record in caplog.records)
    assert "sk-" not in joined
    assert "payload_zlib" not in joined
    assert '"events"' not in joined
