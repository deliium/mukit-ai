"""Ready personal adapters are optional hybrid composers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ai_runtime.routing import default_operation_routes
from app.db import initialize_database
from app.db.connection import reset_database_initialization_cache
from app.main import app
from app.schemas import LLMGenerationOptions, LLMMusicGenerationRequest, LLMPromptParameters
from app.services.llm_music_generator import _resolve_hybrid_stage_models
from app.services.personal_composer_store import update_adapter
from app.services.project_store import create_project
from app.llm_settings import load_llm_settings

_FIXTURES = Path(__file__).resolve().parents[1] / "app" / "fixtures"


def _prompt() -> dict:
    return {
        "genre": "classical",
        "mood": "calm",
        "time_signature": "4/4",
        "tempo_min": 90,
        "tempo_max": 120,
        "key": "C major",
        "instruments": ["piano", "bass"],
        "complexity": "simple",
        "duration_bars": 8,
    }


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
    ):
        composition = json.loads((_FIXTURES / name).read_text(encoding="utf-8"))
        ids.append(create_project(label, composition=composition, db_path=db_path).id)
    with TestClient(app) as http:
        yield http, db_path, ids


def _train(http: TestClient, ids: list[str]) -> dict:
    etude, sketch = ids
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
    assert created.status_code == 200, created.text
    return created.json()


def test_ready_adapter_is_selectable_and_omitted_choice_is_empty(client):
    http, db_path, ids = client
    job = _train(http, ids)
    registry_id = job["registry_model_id"]
    models = http.get("/ai/models", params={"capability": "symbolic_composer", "status": "ready"})
    assert models.status_code == 200
    match = next(item for item in models.json()["models"] if item["id"] == registry_id)
    assert match["display_name"] == "MyComposer-v1"
    assert match["primary_capability"] == "symbolic_composer"

    generated = http.post(
        "/llm/generate-music-json",
        json={
            "prompt": _prompt(),
            "selection": {"provider": "fake", "model": "fake-v1"},
            "options": {
                "pipeline": "hybrid_plan_symbolic",
                "max_retries": 0,
                "composer_model_id": registry_id,
            },
        },
    )
    assert generated.status_code == 200, generated.text
    stages = generated.json()["stages"]
    composer = next(stage for stage in stages if stage["operation"] == "generate_composer")
    assert composer["model_id"] == registry_id
    assert generated.json()["music"]["schema_version"] == "composition.v2"

    request = LLMMusicGenerationRequest(
        prompt=LLMPromptParameters(**_prompt()),
        options=LLMGenerationOptions(pipeline="hybrid_plan_symbolic", max_retries=0),
    )
    assert request.options.composer_model_id is None
    _provider, composer_model_id = _resolve_hybrid_stage_models(request, load_llm_settings())
    routes = default_operation_routes()
    assert routes.get("generate_composer") != registry_id
    assert composer_model_id != registry_id

    update_adapter(job["adapter_id"], status="stopped", db_path=db_path)
    again = http.get("/ai/models", params={"capability": "symbolic_composer", "status": "ready"})
    ids_ready = {item["id"] for item in again.json()["models"]}
    assert registry_id not in ids_ready


def test_missing_table_keeps_fake_composer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    empty = tmp_path / "empty.db"
    empty.write_bytes(b"")
    monkeypatch.setenv("PROJECT_DB_PATH", str(empty))
    from app.ai_runtime.capabilities import ModelCapability
    from app.ai_runtime.registry import list_models, reload_registry

    reload_registry()
    models = list_models(capability=ModelCapability.SYMBOLIC_COMPOSER, status="ready")
    assert any(item.id == "fake:symbolic-tiny" for item in models)
