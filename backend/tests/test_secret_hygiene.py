"""Secret-exposure acceptance checks across health, LLM discovery, projects, and config files."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from app.db import reset_database_initialization_cache
from app.main import app, get_llm_models, readiness_check
from tests.fixtures.load_fixture import load_minimal
from tests.secret_hygiene import assert_no_secret_leakage


ROOT = Path(__file__).resolve().parents[2]


def test_ready_and_models_omit_secrets(monkeypatch, tmp_path):
    monkeypatch.setenv("PROJECT_DB_PATH", str(tmp_path / "sec.db"))
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-this-must-never-leak-1234567890")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    reset_database_initialization_cache()

    ready = asyncio.run(readiness_check())
    assert_no_secret_leakage(ready, context="/ready")
    assert "sk-this-must-never-leak" not in str(ready)

    models = asyncio.run(get_llm_models())
    assert_no_secret_leakage(models.model_dump(mode="json"), context="/llm/models")
    assert "sk-this-must-never-leak" not in models.model_dump_json()


def test_project_crud_omits_secrets(monkeypatch, tmp_path):
    monkeypatch.setenv("PROJECT_DB_PATH", str(tmp_path / "proj.db"))
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-project-leak-check-abcdefghij")
    reset_database_initialization_cache()

    client = TestClient(app)
    created = client.post(
        "/projects",
        json={
            "name": "Secret Check",
            "generation": {
                "provider": "fake",
                "model": "fake-deterministic",
                "prompt": {"genre": "ambient", "mood": "calm", "instruments": ["piano"]},
            },
            "composition": load_minimal().model_dump(mode="json"),
        },
    )
    assert created.status_code == 201
    assert_no_secret_leakage(created.json(), context="POST /projects")
    assert "sk-project-leak-check" not in created.text

    listed = client.get("/projects")
    assert listed.status_code == 200
    assert_no_secret_leakage(listed.json(), context="GET /projects")

    opened = client.get(f"/projects/{created.json()['id']}")
    assert opened.status_code == 200
    assert_no_secret_leakage(opened.json(), context="GET /projects/{id}")


def test_env_example_and_compose_have_no_literal_keys():
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert_no_secret_leakage(env_example, context=".env.example")
    assert_no_secret_leakage(compose, context="docker-compose.yml")
    # Placeholders / empty assignments are fine; sk- literals are not.
    assert "sk-" not in env_example
    assert "sk-" not in compose


def test_import_routes_omit_source_sentinels_from_errors_and_logs(monkeypatch, tmp_path, caplog):
    from tests.fixtures.build_import_fixtures import SENTINEL_FILENAME

    fixture_dir = Path(__file__).resolve().parent / "fixtures" / "import"
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    client = TestClient(app)
    with caplog.at_level("DEBUG"):
        response = client.post(
            "/imports/midi",
            files={
                "file": (
                    SENTINEL_FILENAME,
                    (fixture_dir / "truncated.mid").read_bytes(),
                    "audio/midi",
                )
            },
        )
    assert response.status_code == 422
    assert SENTINEL_FILENAME not in response.text
    assert_no_secret_leakage(response.json(), context="POST /imports/midi error")
    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert SENTINEL_FILENAME not in joined
    assert "MTrk" not in joined
