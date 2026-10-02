"""Stop, resume, evaluate, delete, and the startup sweep."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.db import initialize_database
from app.db.connection import reset_database_initialization_cache
from app.personal_composer_schemas import PersonalComposerError, PersonalTrainingManifestV1
from app.personal_composer.trainer import run_personal_training
from app.services import personal_composer_service as service
from app.services.personal_composer_store import insert_adapter, update_adapter
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
    return {"db": db_path, "root": root, "ids": ids}


def _body(ids: list[str], *, max_steps: int, name: str = "MyComposer-v1") -> dict:
    return {
        "display_name": name,
        "project_ids": ids,
        "rights": {project_id: {"status": "user_owned", "user_owned_attested": True} for project_id in ids},
        "max_steps": max_steps,
    }


def test_stop_after_one_step_then_resume_eval_and_delete(studio, monkeypatch: pytest.MonkeyPatch):
    real_spawn = service._spawn
    monkeypatch.setattr(service, "_spawn", lambda *args, **kwargs: None)
    job = service.start_personal_composer(_body(studio["ids"], max_steps=2))
    monkeypatch.setattr(service, "_spawn", real_spawn)
    assert job.status == "running"
    calls = {"n": 0}

    def read_status() -> str:
        calls["n"] += 1
        if calls["n"] > 1:
            update_adapter(job.adapter_id, status="stopped", db_path=studio["db"])
            return "stopped"
        return "running"

    result = run_personal_training(
        job.adapter_id,
        root=studio["root"],
        db_path=studio["db"],
        read_status=read_status,
    )
    assert result == "stopped"
    loaded = service.get_personal_composer(job.adapter_id)
    assert loaded.step == 1
    assert (studio["root"] / job.adapter_id / "snapshot" / "index.json").is_file()

    resumed = service.resume_personal_composer(job.adapter_id)
    assert resumed.step == 2
    assert resumed.status == "complete"

    report = service.evaluate_personal_composer(job.adapter_id)
    assert report.engine == "fake"
    assert report.loss is None

    service.delete_personal_composer(job.adapter_id)
    assert not (studio["root"] / job.adapter_id).exists()
    with pytest.raises(PersonalComposerError) as missing:
        service.get_personal_composer(job.adapter_id)
    assert missing.value.code == "personal_not_found"


def test_resume_of_complete_is_unavailable(studio):
    job = service.start_personal_composer(_body(studio["ids"], max_steps=1, name="DoneComposer-v1"))
    assert job.status == "complete"
    with pytest.raises(PersonalComposerError) as exc:
        service.resume_personal_composer(job.adapter_id)
    assert exc.value.code == "personal_resume_unavailable"


def test_startup_sweep_interrupts_running_and_list_does_not(studio):
    manifest = PersonalTrainingManifestV1(
        adapter_id="pcomp_" + "11" * 8,
        display_name="Orphan-v1",
        registry_model_id="personal:pcomp_" + "11" * 8,
        project_ids=["etude"],
        rights={"etude": {"status": "user_owned", "user_owned_attested": True}},
        snapshot_version="ab" * 32,
        base_model_id="fake:symbolic-tiny",
        adapter_config={"method": "lora", "rank": 4, "target_modules": ["qkv", "out_proj"], "freeze_base": True, "max_steps": 1},
        engine="fake",
        created_at="2026-10-02T12:00:00Z",
    )
    insert_adapter(
        adapter_id=manifest.adapter_id,
        display_name=manifest.display_name,
        registry_model_id=manifest.registry_model_id,
        engine="fake",
        base_model_id="fake:symbolic-tiny",
        snapshot_version=manifest.snapshot_version,
        max_steps=1,
        manifest=manifest,
        owner_actor_id=None,
        db_path=studio["db"],
    )
    listed = service.list_personal_composers()
    found = next(item for item in listed if item.adapter_id == manifest.adapter_id)
    assert found.status == "running"
    service.sweep_orphaned_personal_jobs()
    interrupted = service.get_personal_composer(manifest.adapter_id)
    assert interrupted.status == "failed"
    assert interrupted.error_code == "personal_interrupted"
    again = service.list_personal_composers()
    again_row = next(item for item in again if item.adapter_id == manifest.adapter_id)
    assert again_row.status == "failed"
    assert again_row.error_code == "personal_interrupted"
