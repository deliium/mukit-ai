"""Fake personal training does not call the full Music Transformer trainer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.db import initialize_database
from app.db.connection import reset_database_initialization_cache
from app.services import personal_composer_service as service
from app.services.project_store import create_project

_FIXTURES = Path(__file__).resolve().parents[1] / "app" / "fixtures"


@pytest.fixture
def studio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "projects.db"
    root = tmp_path / "personal_composers"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.setenv("PERSONAL_COMPOSER_ROOT", str(root))
    monkeypatch.setenv("PERSONAL_COMPOSER_FAKE", "1")
    monkeypatch.setenv("COLLABORATION_ENABLED", "0")
    reset_database_initialization_cache()
    initialize_database()
    ids = []
    for name in ("personal_composer_etude.v2.json", "personal_composer_sketch.v2.json"):
        composition = json.loads((_FIXTURES / name).read_text(encoding="utf-8"))
        ids.append(create_project(name, composition=composition, db_path=db_path).id)
    return {"root": root, "ids": ids}


def test_fake_train_completes_without_full_model_train(studio, monkeypatch: pytest.MonkeyPatch):
    called = {"n": 0}

    def _train_experiment(*_args, **_kwargs):
        called["n"] += 1

    monkeypatch.setattr("app.music_transformer.train.train_experiment", _train_experiment)
    etude, sketch = studio["ids"]
    job = service.start_personal_composer(
        {
            "display_name": "MyComposer-v1",
            "project_ids": [etude, sketch],
            "rights": {
                etude: {"status": "user_owned", "user_owned_attested": True},
                sketch: {"status": "user_owned", "user_owned_attested": True},
            },
            "max_steps": 1,
        }
    )
    assert job.status == "complete"
    assert job.step == 1
    assert job.base_model_id == "fake:symbolic-tiny"
    assert job.manifest.adapter_config.method == "lora"
    artifact = json.loads((studio["root"] / job.adapter_id / "adapter.fake.json").read_text(encoding="utf-8"))
    assert artifact["schema_version"] == "personal.adapter.fake.v1"
    assert artifact["base_model_id"] == "fake:symbolic-tiny"
    assert called["n"] == 0
