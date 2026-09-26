"""Mocked end-to-end autonomous run with the fake symbolic engine."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ai_agents import registry as agent_registry
from app.ai_runtime import registry as model_registry
from app.db import reset_database_initialization_cache
from app.main import app


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PROJECT_DB_PATH", str(tmp_path / "projects.db"))
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    monkeypatch.delenv("FAKE_CRITIC_REVISE_PASSES", raising=False)
    monkeypatch.delenv("MUSIC_TRANSFORMER_CHECKPOINT", raising=False)
    reset_database_initialization_cache()
    agent_registry.clear_registry_for_tests()
    model_registry.clear_registry_for_tests()
    with TestClient(app) as test_client:
        yield test_client
    agent_registry.clear_registry_for_tests()
    model_registry.clear_registry_for_tests()


def _brief() -> dict:
    return {
        "brief": {
            "schema_version": "creative.brief.v1",
            "title": "Cinematic",
            "duration_seconds": 150,
            "narrative": [
                {"intent": "sparse_opening", "text": "cold sparse opening"},
                {"intent": "establish_theme", "text": "introduce Theme A"},
                {"intent": "build", "text": "increase tension"},
                {"intent": "climax", "text": "strong climax"},
                {"intent": "resolve", "text": "quiet transformed ending"},
            ],
            "instrumentation": ["piano", "cello", "strings"],
            "forbidden_instrument_families": ["drums"],
            "opening_key": "F# minor",
            "final_section_key": "F# major",
            "motif_label": "Theme A",
        },
        "include_rendering": False,
        "seed": 0,
    }


def test_example_brief_completes_without_rendering(client: TestClient) -> None:
    response = client.post("/ai/agents/autonomous/runs", json=_brief())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["schema_version"] == "autonomous.run.v1"
    assert body["status"] == "completed"
    assert body["autonomy_mode"] == "autonomous"
    assert body["checkpoint_id"] is None
    assert body["head_revision_id"]
    assert body["project_id"]
    assert "composition" not in body
    assert "brief" not in body
    statuses = {stage["stage_id"]: stage["status"] for stage in body["stages"]}
    assert statuses["symbolic"] == "completed"
    assert statuses["revision"] == "skipped"
    assert statuses["render"] == "skipped"
    assert statuses["expression"] == "completed"


def test_request_agent_cap_stops_before_the_next_agent(client: TestClient) -> None:
    body = _brief()
    body["max_agent_operations"] = 1
    response = client.post("/ai/agents/autonomous/runs", json=body)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "failed"
    assert payload["budget_code"] == "autonomous_agent_operation_budget"
    assert payload["head_revision_id"] is None
    statuses = {stage["stage_id"]: stage["status"] for stage in payload["stages"]}
    assert statuses["plan"] == "completed"
    assert statuses["harmony_plan"] == "pending"


def test_expression_pitch_drift_keeps_the_arrangement_head(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.composition_schemas import CompositionV2
    from app.services.composition_expression import realize_expression_marks

    def _drift(composition, section_bands):
        realized = realize_expression_marks(composition, section_bands)
        data = realized.model_dump(mode="json")
        data["tracks"][0]["events"][0]["pitch"] = "C4"
        return CompositionV2.model_validate(data)

    monkeypatch.setattr(
        "app.ai_agents.agents.performance_expression.realize_expression_marks",
        _drift,
    )
    response = client.post("/ai/agents/autonomous/runs", json=_brief())
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "failed"
    by_id = {stage["stage_id"]: stage for stage in payload["stages"]}
    assert by_id["arrangement"]["status"] == "completed"
    assert by_id["expression"]["status"] == "failed"
    assert payload["head_revision_id"] == by_id["arrangement"]["revision_id"]


def test_render_approval_enqueues_without_changing_the_score(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.ai_runtime.registry import reload_registry
    from app.ai_runtime.runtimes.fake_neural_audio import FAKE_NEURAL_AUDIO_MODEL_ID

    monkeypatch.setenv("NEURAL_AUDIO_RENDER_ROOT", str(tmp_path / "renders"))
    monkeypatch.setenv("NEURAL_AUDIO_FAKE_MODE", "1")
    monkeypatch.setenv("NEURAL_AUDIO_ENGINE", "fake:neural-audio")
    monkeypatch.setenv("AI_OP_AUDIO_RENDER", FAKE_NEURAL_AUDIO_MODEL_ID)
    reload_registry()
    body = _brief()
    body["include_rendering"] = True
    paused = client.post("/ai/agents/autonomous/runs", json=body)
    assert paused.status_code == 200, paused.text
    paused_body = paused.json()
    assert paused_body["status"] == "awaiting_approval"
    render = next(stage for stage in paused_body["stages"] if stage["stage_id"] == "render")
    assert render["status"] == "awaiting_approval"
    fingerprint = paused_body["composition_fingerprint"]
    approved = client.post(
        f"/ai/agents/autonomous/runs/{paused_body['run_id']}/stages/render/approve"
    )
    assert approved.status_code == 200, approved.text
    done = approved.json()
    assert done["status"] == "completed"
    assert done["composition_fingerprint"] == fingerprint
    finished = next(stage for stage in done["stages"] if stage["stage_id"] == "render")
    assert finished["status"] == "completed"
    assert finished["artifact_ids"]


def test_skip_render_completes_a_paused_run(client: TestClient, tmp_path: Path) -> None:
    from app.autonomous_composer_schemas import CreativeBriefV1
    from app.services.autonomous_composer_store import insert_run, update_stage_status
    from app.services.autonomous_project_plan import compile_project_plan
    from app.services.project_store import create_project

    db = tmp_path / "projects.db"
    brief = CreativeBriefV1.model_validate(_brief()["brief"])
    plan = compile_project_plan(brief)
    project = create_project("Skip", db_path=db)
    record = insert_run(
        project_id=project.id,
        branch_id=project.active_branch_id,
        operation_run_id="00000000-0000-4000-8000-000000000021",
        brief=brief.model_dump(mode="json"),
        plan=plan,
        include_rendering=True,
        db_path=db,
    )
    for stage in record.stages:
        if stage.stage_id == "render":
            update_stage_status(record.id, stage.stage_id, "awaiting_approval", db_path=db)
        else:
            update_stage_status(record.id, stage.stage_id, "completed", db_path=db)
    skipped = client.post(f"/ai/agents/autonomous/runs/{record.id}/stages/render/skip")
    assert skipped.status_code == 200, skipped.text
    body = skipped.json()
    assert body["status"] == "completed"
    render = next(stage for stage in body["stages"] if stage["stage_id"] == "render")
    assert render["status"] == "skipped"


def test_cancel_fails_only_the_running_stage(client: TestClient, tmp_path: Path) -> None:
    from app.autonomous_composer_schemas import CreativeBriefV1
    from app.services.autonomous_composer_store import insert_run, update_run_fields, update_stage_status
    from app.services.autonomous_project_plan import compile_project_plan
    from app.services.project_store import create_project

    db = tmp_path / "projects.db"
    brief = CreativeBriefV1.model_validate(_brief()["brief"])
    plan = compile_project_plan(brief)
    project = create_project("Cancel", db_path=db)
    record = insert_run(
        project_id=project.id,
        branch_id=project.active_branch_id,
        operation_run_id="00000000-0000-4000-8000-000000000022",
        brief=brief.model_dump(mode="json"),
        plan=plan,
        db_path=db,
    )
    update_stage_status(record.id, "plan", "completed", db_path=db)
    update_stage_status(record.id, "harmony_plan", "running", db_path=db)
    update_run_fields(record.id, status="running", db_path=db)
    cancelled = client.post(f"/ai/agents/autonomous/runs/{record.id}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    body = cancelled.json()
    assert body["status"] == "cancelled"
    by_id = {stage["stage_id"]: stage for stage in body["stages"]}
    assert by_id["plan"]["status"] == "completed"
    assert by_id["harmony_plan"]["status"] == "failed"
    assert by_id["harmony_plan"]["failure_code"] == "operation_cancelled"


def test_resume_continues_an_interrupted_symbolic_stage(client: TestClient, tmp_path: Path) -> None:
    from app.ai_agents.artifact_schemas import AGENT_HARMONY_PLAN_SCHEMA, AGENT_MOTIF_PLAN_SCHEMA
    from app.ai_agents.agents.typed_emit import harmony_plan_from_project, motif_plan_from_project
    from app.ai_agents.schemas import AgentArtifactKind, AgentArtifactV1
    from app.autonomous_composer_schemas import CreativeBriefV1
    from app.services.agent_artifact_workspace import insert_durable
    from app.services.autonomous_composer_store import insert_run, update_stage_status
    from app.services.autonomous_project_plan import compile_project_plan
    from app.services.project_store import create_project

    db = tmp_path / "projects.db"
    brief = CreativeBriefV1.model_validate(_brief()["brief"])
    plan = compile_project_plan(brief)
    project = create_project("Resume", db_path=db)
    harmony = insert_durable(
        project.id,
        AgentArtifactV1(
            kind=AgentArtifactKind.PLAN,
            producer_agent_id="harmony",
            content_type=AGENT_HARMONY_PLAN_SCHEMA,
            payload=harmony_plan_from_project(plan),
        ),
        db_path=db,
    )
    motif = insert_durable(
        project.id,
        AgentArtifactV1(
            kind=AgentArtifactKind.PLAN,
            producer_agent_id="melody_motif",
            content_type=AGENT_MOTIF_PLAN_SCHEMA,
            payload=motif_plan_from_project(plan),
        ),
        db_path=db,
    )
    record = insert_run(
        project_id=project.id,
        branch_id=project.active_branch_id,
        operation_run_id="00000000-0000-4000-8000-000000000023",
        brief=brief.model_dump(mode="json"),
        plan=plan,
        db_path=db,
    )
    update_stage_status(record.id, "plan", "completed", db_path=db)
    update_stage_status(record.id, "harmony_plan", "completed", artifact_ids=[harmony], db_path=db)
    update_stage_status(record.id, "motif_plan", "completed", artifact_ids=[motif], db_path=db)
    update_stage_status(record.id, "symbolic", "running", db_path=db)
    listed = client.get(f"/ai/agents/autonomous/runs/{record.id}")
    assert listed.status_code == 200, listed.text
    symbolic = next(stage for stage in listed.json()["stages"] if stage["stage_id"] == "symbolic")
    assert symbolic["status"] == "failed"
    assert symbolic["failure_code"] == "autonomous_stage_interrupted"
    resumed = client.post(f"/ai/agents/autonomous/runs/{record.id}/resume")
    assert resumed.status_code == 200, resumed.text
    body = resumed.json()
    assert body["status"] == "completed"
    assert body["head_revision_id"]
