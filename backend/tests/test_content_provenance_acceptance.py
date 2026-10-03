"""Acceptance: fake neural with pinned source_revision_id walks provenance chain."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.composition_schemas import CompositionV2
from app.db.connection import reset_database_initialization_cache, run_alembic_upgrade
from app.main import app
from app.ai_runtime.runtimes.fake_neural_audio import FAKE_NEURAL_AUDIO_MODEL_ID
from app.project_history_schemas import DurableCommitRequest, RevisionOperationType
from app.services import project_history as history
from app.services import project_store as project_store_mod
from app.ai_runtime.registry import reload_registry
from tests.test_composition_v2_schema import minimal_v2


@pytest.fixture
def acceptance_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.setenv("NEURAL_AUDIO_RENDER_ROOT", str(tmp_path / "neural_audio_renders"))
    monkeypatch.setenv("NEURAL_AUDIO_FAKE_MODE", "1")
    monkeypatch.setenv("NEURAL_AUDIO_ENGINE", "fake:neural-audio")
    monkeypatch.setenv("AI_OP_AUDIO_RENDER", FAKE_NEURAL_AUDIO_MODEL_ID)
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    monkeypatch.setenv("CONTENT_CREDENTIALS_ENABLED", "0")
    reset_database_initialization_cache()
    run_alembic_upgrade(db_path)
    reload_registry()
    return tmp_path


def test_fake_neural_with_source_revision_chain_and_manifest(
    acceptance_env: Path,
) -> None:
    created = project_store_mod.create_project(
        "Acceptance",
        composition=minimal_v2(),
        project_id="prov-accept",
        db_path=acceptance_env / "projects.db",
    )
    branches = history.list_branches(created.id, db_path=acceptance_env / "projects.db")
    branch = branches.branches[0]
    payload = deepcopy(minimal_v2())
    payload["tracks"][0]["events"] = [
        {"pitch": "G4", "start_tick": 0, "duration_ticks": 480, "velocity": 80}
    ]
    committed = history.commit_revision(
        created.id,
        DurableCommitRequest(
            branch_id=branch.id,
            expected_active_branch_id=branch.id,
            expected_working_version=branch.working_version,
            expected_head_revision_id=branch.head_revision_id,
            expected_source_fingerprint=branch.working_fingerprint,
            composition=CompositionV2.model_validate(payload),
            operation_type=RevisionOperationType.MANUAL_CHECKPOINT,
            name="acceptance-human",
        ),
        db_path=acceptance_env / "projects.db",
    )
    revision_id = committed.current_revision_id
    assert revision_id

    with TestClient(app) as client:
        response = client.post(
            "/neural-audio/renders",
            json={
                "composition": payload,
                "project_id": created.id,
                "source_revision_id": revision_id,
                "instructions": "accept",
                "model_id": FAKE_NEURAL_AUDIO_MODEL_ID,
                "seed": 3,
            },
        )
        assert response.status_code == 200, response.text
        job = response.json()
        assert job["status"] == "complete"
        assert job["source_revision_id"] == revision_id
        render_id = job["id"]

        chain = client.get(
            f"/content-provenance/projects/{created.id}/artifacts/neural_render/{render_id}/chain"
        )
        assert chain.status_code == 200, chain.text
        chain_body = chain.json()
        assert chain_body["schema_version"] == "content.provenance.chain.v1"
        ops = [record["operation"] for record in chain_body["records"]]
        assert "neural_render" in ops
        assert any(op in {"human_edit", "ai_generate"} for op in ops)
        assert len(chain_body["records"]) >= 2
        encoded = chain.text
        assert '"events"' not in encoded
        assert '"notes"' not in encoded

        manifest = client.get(
            f"/content-provenance/projects/{created.id}/artifacts/neural_render/{render_id}/manifest"
        )
        assert manifest.status_code == 200, manifest.text
        man = manifest.json()
        assert man["honesty"]["cryptographic"] is False
        assert len(man["records"]) >= 2

        download = client.get(
            f"/content-provenance/projects/{created.id}/artifacts/neural_render/{render_id}/manifest/download"
        )
        assert download.status_code == 200
        assert "attachment" in download.headers.get("content-disposition", "")
        assert download.json()["honesty"]["cryptographic"] is False

        status = client.get("/content-provenance/status")
        assert status.status_code == 200
        assert status.json()["provenance_enabled"] is True
        assert status.json()["credentials_enabled"] is False

        ready = client.get("/ready")
        assert ready.status_code == 200
        ready_text = ready.text.lower()
        assert "content_credentials" not in ready_text
        assert "credentials_enabled" not in ready_text
