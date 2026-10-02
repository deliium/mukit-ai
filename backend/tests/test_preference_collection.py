"""HTTP evaluation for opt-in preference collection."""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.db import initialize_database, reset_database_initialization_cache
from app.main import app
from app.preference_schemas import PreferenceCandidateFeaturesV1, PreferenceContextV1, PreferencePendingBallotV1
from app.services import preference_store as store
from app.services.project_store import create_project
from tests.test_composition_v2_schema import minimal_v2


def _ballot(*, project_id: str | None = None) -> PreferencePendingBallotV1:
    def row(label: str, index: int, density: float) -> PreferenceCandidateFeaturesV1:
        vector = [0.0] * 16
        vector[3] = density
        return PreferenceCandidateFeaturesV1(
            candidate_id=label,
            candidate_fingerprint="f" * 16,
            original_index=index,
            feature_vector=vector,
        )

    return PreferencePendingBallotV1(
        context=PreferenceContextV1(
            surface="development",
            operation="continue",
            project_id=project_id,
            source_fingerprint="s" * 16,
            request_digest="a" * 64,
        ),
        candidates=[
            row("cand_sparse_00001", 0, 1 / 32),
            row("cand_middle_00001", 1, 4 / 32),
            row("cand_dense_000001", 2, 8 / 32),
        ],
    )


def _fingerprint(db_path) -> str:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT working_fingerprint FROM project_branches").fetchone()
    assert row is not None
    return str(row[0])


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    monkeypatch.delenv("PREFERENCE_LEARNING_ENABLED", raising=False)
    monkeypatch.delenv("COLLABORATION_ENABLED", raising=False)
    reset_database_initialization_cache()
    initialize_database()
    create_project("Preference home", composition=minimal_v2(), project_id="proj_pref", db_path=db_path)
    return TestClient(app), db_path


def test_flag_off_inserts_nothing(client) -> None:
    http, db_path = client
    before = _fingerprint(db_path)
    store.stash_pending(_ballot(), db_path=db_path)
    response = http.post(
        "/preferences/choices",
        json={"surface": "development", "chosen_candidate_id": "cand_dense_000001"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "preference_learning_disabled"
    assert store.choice_count(db_path=db_path) == 0
    assert _fingerprint(db_path) == before


def test_user_collection_off_inserts_nothing(client, monkeypatch) -> None:
    http, db_path = client
    monkeypatch.setenv("PREFERENCE_LEARNING_ENABLED", "1")
    before = _fingerprint(db_path)
    saved = http.put(
        "/preferences/settings",
        json={"collection_enabled": False, "ranking_enabled": False},
    )
    assert saved.status_code == 200
    store.stash_pending(_ballot(), db_path=db_path)
    response = http.post(
        "/preferences/choices",
        json={"surface": "development", "chosen_candidate_id": "cand_dense_000001"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "preference_collection_disabled"
    assert store.choice_count(db_path=db_path) == 0
    assert _fingerprint(db_path) == before


def test_both_gates_record_dense_inspect_reset_and_cold_rank(client, monkeypatch) -> None:
    http, db_path = client
    monkeypatch.setenv("PREFERENCE_LEARNING_ENABLED", "1")
    before = _fingerprint(db_path)
    saved = http.put(
        "/preferences/settings",
        json={"collection_enabled": True, "ranking_enabled": True},
    )
    assert saved.status_code == 200
    assert saved.json()["feature_available"] is True
    store.stash_pending(_ballot(), db_path=db_path)
    recorded = http.post(
        "/preferences/choices",
        json={"surface": "development", "chosen_candidate_id": "cand_dense_000001"},
    )
    assert recorded.status_code == 200, recorded.text
    assert recorded.json()["chosen_candidate_id"] == "cand_dense_000001"
    listed = http.get("/preferences/choices")
    assert listed.status_code == 200
    body = listed.json()
    assert len(body) == 1
    assert "feature_vector" not in listed.text
    detail = http.get(f"/preferences/choices/{body[0]['id']}")
    assert detail.status_code == 200
    payload = detail.json()
    assert len(payload["candidates"][0]["feature_vector"]) == 16
    assert "pitch" not in detail.text
    assert "events" not in detail.text
    assert _fingerprint(db_path) == before

    ranked = http.post(
        "/preferences/rank",
        json={
            "surface": "development",
            "candidate_ids": ["cand_sparse_00001", "cand_middle_00001", "cand_dense_000001"],
        },
    )
    # The choice deleted the pending ballot, so scoring the missing ids is a miss
    # only when a ranker exists and ranking is on. Re-stash, then reset.
    assert ranked.status_code == 404
    assert ranked.json()["detail"]["code"] == "preference_ballot_missing"
    store.stash_pending(_ballot(), db_path=db_path)
    reset = http.delete("/preferences/data")
    assert reset.status_code == 200
    empty = http.get("/preferences/choices")
    assert empty.json() == []
    cold = http.post(
        "/preferences/rank",
        json={
            "surface": "development",
            "candidate_ids": ["cand_sparse_00001", "cand_middle_00001", "cand_dense_000001"],
        },
    )
    assert cold.status_code == 200, cold.text
    assert cold.json()["ranking_applied"] is False
    assert cold.json()["ordered_candidate_ids"] == [
        "cand_sparse_00001",
        "cand_middle_00001",
        "cand_dense_000001",
    ]
    assert _fingerprint(db_path) == before


def test_record_and_rank_call_read_when_collaboration_is_on(client, monkeypatch) -> None:
    http, db_path = client
    monkeypatch.setenv("PREFERENCE_LEARNING_ENABLED", "1")
    monkeypatch.setenv("COLLABORATION_ENABLED", "1")
    calls: list[tuple[str, str]] = []

    def _fake(project_id, action):
        calls.append((project_id, action))
        return "local"

    monkeypatch.setattr("app.routers.preferences.enforce_current", _fake)
    store.put_settings(collection_enabled=True, ranking_enabled=True, db_path=db_path)
    store.stash_pending(_ballot(project_id="proj_pref"), db_path=db_path)
    recorded = http.post(
        "/preferences/choices",
        json={"surface": "development", "chosen_candidate_id": "cand_dense_000001"},
    )
    assert recorded.status_code == 200, recorded.text
    store.stash_pending(_ballot(project_id="proj_pref"), db_path=db_path)
    ranked = http.post(
        "/preferences/rank",
        json={
            "surface": "development",
            "candidate_ids": ["cand_dense_000001", "cand_middle_00001", "cand_sparse_00001"],
        },
    )
    assert ranked.status_code == 200, ranked.text
    assert ranked.json()["ranking_applied"] is True
    assert ranked.json()["ordered_candidate_ids"][0] == "cand_dense_000001"
    assert calls == [("proj_pref", "read"), ("proj_pref", "read")]
    assert {action for _project, action in calls} == {"read"}
