"""Preview stash leaves candidate order untouched and ranks from stored features."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import initialize_database, reset_database_initialization_cache
from app.main import app
from app.services import preference_store as store
from app.services.instrument_catalog import clear_catalog_cache
from app.services.preference_features import project_preference_features
from tests.test_composition_arrangement_routes import _preview_body
from tests.test_composition_development_routes import _body


_PROFILE_ID = "prof_0123456789abcdef"


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    monkeypatch.delenv("PREFERENCE_LEARNING_ENABLED", raising=False)
    reset_database_initialization_cache()
    initialize_database()
    clear_catalog_cache()
    return TestClient(app), db_path


def _enable(monkeypatch, *, collection: bool, ranking: bool, db_path) -> None:
    monkeypatch.setenv("PREFERENCE_LEARNING_ENABLED", "1")
    store.put_settings(collection_enabled=collection, ranking_enabled=ranking, db_path=db_path)


def test_development_preview_stashes_without_reordering(client, monkeypatch) -> None:
    http, db_path = client
    _enable(monkeypatch, collection=True, ranking=True, db_path=db_path)
    response = http.post(
        "/composition/development/preview",
        json=_body(
            candidate_count=3,
            active_project_id="proj_pref",
            profile_id=_PROFILE_ID,
            profile_strength="normal",
        ),
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    success_ids = [item["candidate_id"] for item in payload["candidates"]]
    assert len(success_ids) == 3
    pending = store.get_pending("development", db_path=db_path)
    assert pending is not None
    assert pending.context.project_id == "proj_pref"
    assert pending.context.profile_id == _PROFILE_ID
    assert [item.candidate_id for item in pending.candidates] == success_ids
    ranked = http.post(
        "/preferences/rank",
        json={"surface": "development", "candidate_ids": success_ids},
    )
    assert ranked.status_code == 200, ranked.text
    assert ranked.json()["ranking_applied"] is False
    assert ranked.json()["ordered_candidate_ids"] == success_ids


def test_arrangement_single_success_writes_no_pending_row(client, monkeypatch) -> None:
    http, db_path = client
    _enable(monkeypatch, collection=True, ranking=True, db_path=db_path)
    response = http.post("/composition/arrangement/preview", json=_preview_body(candidate_count=1))
    assert response.status_code == 200, response.text
    assert len(response.json()["candidates"]) == 1
    assert store.get_pending("arrangement", db_path=db_path) is None


def test_flag_off_leaves_the_pending_table_empty(client) -> None:
    http, db_path = client
    response = http.post("/composition/development/preview", json=_body(candidate_count=3))
    assert response.status_code == 200, response.text
    assert store.get_pending("development", db_path=db_path) is None


def test_ranking_on_collection_off_stashes_and_refuses_the_choice(client, monkeypatch) -> None:
    http, db_path = client
    _enable(monkeypatch, collection=False, ranking=True, db_path=db_path)
    response = http.post("/composition/development/preview", json=_body(candidate_count=3))
    assert response.status_code == 200, response.text
    assert store.get_pending("development", db_path=db_path) is not None
    chosen = response.json()["candidates"][0]["candidate_id"]
    recorded = http.post(
        "/preferences/choices",
        json={"surface": "development", "chosen_candidate_id": chosen},
    )
    assert recorded.status_code == 409
    assert recorded.json()["detail"]["code"] == "preference_collection_disabled"
    assert store.choice_count(db_path=db_path) == 0


def test_failed_extraction_is_omitted_and_preview_still_lists_it(client, monkeypatch) -> None:
    http, db_path = client
    _enable(monkeypatch, collection=True, ranking=True, db_path=db_path)
    calls = {"count": 0}
    real = project_preference_features

    def _flaky(composition, *, candidate_id: str):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("extract failed")
        return real(composition, candidate_id=candidate_id)

    monkeypatch.setattr("app.services.preference_capture.project_preference_features", _flaky)
    response = http.post("/composition/development/preview", json=_body(candidate_count=3))
    assert response.status_code == 200, response.text
    success_ids = [item["candidate_id"] for item in response.json()["candidates"]]
    assert len(success_ids) == 3
    pending = store.get_pending("development", db_path=db_path)
    assert pending is not None
    stashed = [item.candidate_id for item in pending.candidates]
    assert len(stashed) == 2
    assert stashed == success_ids[1:]
    assert success_ids[0] not in stashed
