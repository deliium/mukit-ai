"""Scenario G: cancel, crash, reopen, and SQLite backup keep the head."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ai_agents.schemas import AgentArtifactKind, AgentArtifactV1
from app.db.backup import main as backup_main
from app.db.connection import reset_database_initialization_cache
from app.services.autonomous_composer_store import (
    get_run,
    insert_run,
    update_run_fields,
    update_stage_status,
)
from app.services.autonomous_revision import apply_revision_stage
from app.services.project_store import get_project
from tests.studio_acceptance.invariants import assert_playable_v2, event_fingerprint
from tests.test_autonomous_revision import _brief as _revision_brief
from tests.test_autonomous_revision import _piece, _seed
from tests.test_mix_analysis import _multi_stem_composition

_BRIEF_TOKEN = "studio-recovery-brief-token"


def _brief() -> dict:
    body = _revision_brief().model_dump(mode="json")
    body["narrative"][0]["text"] = _BRIEF_TOKEN
    return body


def test_cancel_leaves_a_valid_head(
    studio_client: tuple[TestClient, Path],
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, _db_path = studio_client
    caplog.set_level(logging.INFO)
    created = client.post(
        "/projects",
        json={"name": "Recover", "composition": _multi_stem_composition()},
    )
    assert created.status_code == 201, created.text
    project = created.json()
    before = event_fingerprint(project["composition"])
    started = client.post(
        "/ai/agents/autonomous/runs",
        json={
            "brief": _brief(),
            "project_id": project["id"],
            "include_rendering": False,
            "autonomy_mode": "guided",
        },
    )
    assert started.status_code == 200, started.text
    run_id = started.json()["run_id"]
    update_stage_status(run_id, "arrangement", "running")
    update_run_fields(run_id, status="running")
    cancelled = client.post(f"/ai/agents/autonomous/runs/{run_id}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    stage = next(item for item in cancelled.json()["stages"] if item["stage_id"] == "arrangement")
    assert stage["status"] != "completed"
    assert stage["failure_code"] == "operation_cancelled"
    opened = client.get(f"/projects/{project['id']}")
    assert_playable_v2(opened.json()["composition"])
    assert event_fingerprint(opened.json()["composition"]) == before
    assert _BRIEF_TOKEN not in caplog.text


def test_system_exit_keeps_the_head_revision(
    studio_client: tuple[TestClient, Path],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, db_path = studio_client
    created, run, composition, revision_id = _seed(db_path)
    critique = AgentArtifactV1(
        kind=AgentArtifactKind.CRITIQUE,
        producer_agent_id="critic",
        content_type="agent.critique.v1",
        payload={
            "schema_version": "agent.critique.v1",
            "recommendation": "revise",
            "findings": [
                {
                    "stratum": "hard_constraint",
                    "category": "structure",
                    "code": "requested_structure_mismatch",
                    "severity": "error",
                    "explanation": "driver",
                    "affected_range": {"start_bar": 1, "end_bar": 2},
                    "affected_tracks": ["piano-1"],
                }
            ],
        },
    )

    class _Boom:
        async def run(self, request):  # noqa: ANN001
            raise SystemExit(3)

    real_get = __import__("app.ai_agents.registry", fromlist=["get_agent"]).get_agent

    def _get(agent_id: str):
        if agent_id == "harmony":
            return _Boom()
        return real_get(agent_id)

    monkeypatch.setattr("app.ai_agents.revision_loop.get_agent", _get)
    caplog.set_level(logging.ERROR)
    outcome = asyncio.run(
        apply_revision_stage(
            project_id=created.id,
            branch_id=created.active_branch_id or "",
            run_id=run.id,
            composition=composition,
            critique=critique,
            db_path=db_path,
        )
    )
    assert outcome.status == "failed"
    assert outcome.failure_code == "operation_model_crashed"
    assert get_run(run.id, db_path=db_path).head_revision_id == revision_id
    ready = client.get("/ready")
    assert ready.status_code == 200
    opened = client.get(f"/projects/{created.id}")
    assert_playable_v2(opened.json()["composition"])
    assert opened.json()["current_revision_id"] == revision_id
    assert any(getattr(record, "failure_code", None) == "operation_model_crashed" for record in caplog.records)
    assert _BRIEF_TOKEN not in caplog.text


def test_reopen_resumes_and_backup_matches_fingerprint(
    studio_client: tuple[TestClient, Path],
    tmp_path: Path,
) -> None:
    client, db_path = studio_client
    from app.ai_agents.artifact_schemas import AGENT_HARMONY_PLAN_SCHEMA, AGENT_MOTIF_PLAN_SCHEMA
    from app.ai_agents.agents.typed_emit import harmony_plan_from_project, motif_plan_from_project
    from app.autonomous_composer_schemas import CreativeBriefV1
    from app.services.agent_artifact_workspace import insert_durable
    from app.services.autonomous_project_plan import compile_project_plan

    brief = CreativeBriefV1.model_validate(_brief())
    plan = compile_project_plan(brief)
    created = client.post("/projects", json={"name": "Resume"})
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    branch_id = created.json()["active_branch_id"]
    harmony = insert_durable(
        project_id,
        AgentArtifactV1(
            kind=AgentArtifactKind.PLAN,
            producer_agent_id="harmony",
            content_type=AGENT_HARMONY_PLAN_SCHEMA,
            payload=harmony_plan_from_project(plan),
        ),
        db_path=db_path,
    )
    motif = insert_durable(
        project_id,
        AgentArtifactV1(
            kind=AgentArtifactKind.PLAN,
            producer_agent_id="melody_motif",
            content_type=AGENT_MOTIF_PLAN_SCHEMA,
            payload=motif_plan_from_project(plan),
        ),
        db_path=db_path,
    )
    record = insert_run(
        project_id=project_id,
        branch_id=branch_id,
        operation_run_id="00000000-0000-4000-8000-0000000000aa",
        brief=brief.model_dump(mode="json"),
        plan=plan,
        db_path=db_path,
    )
    update_stage_status(record.id, "plan", "completed", db_path=db_path)
    update_stage_status(record.id, "harmony_plan", "completed", artifact_ids=[harmony], db_path=db_path)
    update_stage_status(record.id, "motif_plan", "completed", artifact_ids=[motif], db_path=db_path)
    update_stage_status(record.id, "symbolic", "running", db_path=db_path)
    reset_database_initialization_cache()
    listed = client.get(f"/ai/agents/autonomous/runs/{record.id}")
    assert listed.status_code == 200, listed.text
    symbolic = next(stage for stage in listed.json()["stages"] if stage["stage_id"] == "symbolic")
    assert symbolic["status"] == "failed"
    assert symbolic.get("revision_id") in {None, ""}
    revisions_before = client.get(f"/projects/{project_id}/revisions")
    assert revisions_before.status_code == 200, revisions_before.text
    before_ids = [item["id"] for item in revisions_before.json()["revisions"]]
    resumed = client.post(f"/ai/agents/autonomous/runs/{record.id}/resume")
    assert resumed.status_code == 200, resumed.text
    body = resumed.json()
    symbolic_after = next(stage for stage in body["stages"] if stage["stage_id"] == "symbolic")
    assert symbolic_after["revision_id"]
    assert symbolic_after["revision_id"] not in before_ids
    revisions_after = client.get(f"/projects/{project_id}/revisions")
    assert revisions_after.status_code == 200
    new_ids = [item["id"] for item in revisions_after.json()["revisions"] if item["id"] not in before_ids]
    assert new_ids.count(symbolic_after["revision_id"]) == 1
    opened = client.get(f"/projects/{project_id}")
    assert_playable_v2(opened.json()["composition"])
    fingerprint = event_fingerprint(opened.json()["composition"])

    backup_path = tmp_path / "var" / "backups" / "projects.db"
    assert backup_main(["backup", "--dest", str(backup_path)]) == 0
    mutated = client.patch(
        f"/projects/{project_id}",
        json={"composition": _piece().model_dump(mode="json")},
    )
    assert mutated.status_code == 200, mutated.text
    assert event_fingerprint(mutated.json()["composition"]) != fingerprint
    restored = tmp_path / "var" / "restore" / "projects.db"
    assert backup_main(["restore", "--from", str(backup_path), "--dest", str(restored)]) == 0
    recovered = get_project(project_id, db_path=restored)
    assert event_fingerprint(json.loads(recovered.composition_json or "{}")) == fingerprint
