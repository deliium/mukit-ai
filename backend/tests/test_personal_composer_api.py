"""HTTP acceptance for personal composer jobs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import initialize_database
from app.db.connection import get_connection, reset_database_initialization_cache
from app.main import app
from app.services.project_store import create_project

_FIXTURES = Path(__file__).resolve().parents[1] / "app" / "fixtures"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "projects.db"
    root = tmp_path / "personal_composers"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.setenv("PERSONAL_COMPOSER_ROOT", str(root))
    monkeypatch.setenv("PERSONAL_COMPOSER_FAKE", "1")
    monkeypatch.setenv("COLLABORATION_ENABLED", "0")
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    reset_database_initialization_cache()
    initialize_database()
    ids = []
    for label, name in (
        ("Etude", "personal_composer_etude.v2.json"),
        ("Sketch", "personal_composer_sketch.v2.json"),
        ("Other", "personal_composer_etude.v2.json"),
    ):
        composition = json.loads((_FIXTURES / name).read_text(encoding="utf-8"))
        ids.append(create_project(label, composition=composition, db_path=db_path).id)
    with TestClient(app) as http:
        yield http, root, db_path, ids


def _count(db_path: Path) -> int:
    with get_connection(db_path) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM personal_composer_adapters").fetchone()[0])


def test_fake_post_completes_and_unknown_rights_do_not_snapshot(client):
    http, root, db_path, (etude, sketch, other) = client
    listed = http.get("/personal-composers")
    assert listed.status_code == 200
    assert listed.json() == []
    assert _count(db_path) == 0

    created = http.post(
        "/personal-composers",
        json={
            "display_name": "MyComposer-v1",
            "project_ids": [etude, sketch],
            "rights": {
                etude: {"status": "user_owned", "user_owned_attested": True},
                sketch: {"status": "user_owned", "user_owned_attested": True},
            },
        },
    )
    assert created.status_code == 200
    body = created.json()
    assert body["status"] == "complete"
    assert body["display_name"] == "MyComposer-v1"
    assert body["base_model_id"] == "fake:symbolic-tiny"
    assert body["manifest"]["adapter_config"]["method"] == "lora"
    assert len(body["snapshot_version"]) == 64
    assert body["registry_model_id"].startswith("personal:pcomp_")
    assert len(body["registry_model_id"].removeprefix("personal:pcomp_")) == 16

    before = list(root.glob("*/snapshot"))
    refused = http.post(
        "/personal-composers",
        json={
            "display_name": "OtherComposer-v1",
            "project_ids": [other],
            "rights": {other: {"status": "unknown"}},
        },
    )
    assert refused.status_code == 422
    assert refused.json()["detail"]["code"] == "personal_rights_refused"
    assert list(root.glob("*/snapshot")) == before

    stopped = http.post(f"/personal-composers/{body['adapter_id']}/stop")
    assert stopped.status_code == 409
    assert stopped.json()["detail"]["code"] == "personal_not_stoppable"
