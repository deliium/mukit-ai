"""Export/import envelope size and missing-source warning tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import initialize_database, reset_database_initialization_cache
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    reset_database_initialization_cache()
    initialize_database()
    return TestClient(app)


def test_import_warns_on_missing_source_projects(client):
    created = client.post("/composer-profiles", json={"name": "Export Me"}).json()
    # Inject a stale source via update
    updated = client.put(
        f"/composer-profiles/{created['id']}",
        json={
            "expected_updated_at": created["updated_at"],
            "source_projects": [
                {
                    "project_id": "missing_proj_abc",
                    "fingerprint_prefix": "deadbeef",
                    "included_at": "2026-09-22T12:00:00Z",
                }
            ],
        },
    ).json()
    exported = client.get(f"/composer-profiles/{updated['id']}/export").json()
    imported = client.post(
        "/composer-profiles/import",
        json={"envelope": exported, "name": "With Missing Source"},
    )
    assert imported.status_code == 200
    warnings = imported.json()["warnings"]
    assert any("source_project_missing" in w for w in warnings)
