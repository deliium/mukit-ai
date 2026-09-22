"""Acceptance: spine preview → Apply → revision exposes role artifact ids."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path

import pytest

from app.ai_agents import registry as agent_registry
from app.ai_agents.workflow import run_spine_workflow
from app.ai_runtime import registry as model_registry
from app.composition_schemas import CompositionV2
from app.project_history_schemas import (
    AiProvenance,
    DurableCommitRequest,
    RevisionOperationType,
)
from app.services import project_history as history
from app.services import project_store as store
from app.services.composition_edit_fingerprint import composition_edit_fingerprint
from tests.test_composition_v2_schema import minimal_v2


@pytest.fixture
def project_db(tmp_path, monkeypatch):
    db_path = tmp_path / "artifact-acceptance.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    agent_registry.clear_registry_for_tests()
    model_registry.clear_registry_for_tests()
    env = dict(**{k: v for k, v in __import__("os").environ.items()})
    model_registry.reload_registry(env)
    agent_registry.reload_agent_registry(env)
    yield db_path
    agent_registry.clear_registry_for_tests()
    model_registry.clear_registry_for_tests()


def _source() -> CompositionV2:
    return CompositionV2.model_validate(
        minimal_v2(
            tracks=[
                {
                    "id": "piano-1",
                    "name": "Piano",
                    "instrument": "piano",
                    "role": "melody",
                    "midi_program": 0,
                    "channel": 1,
                    "events": [
                        {
                            "pitch": "C4",
                            "start_tick": 0,
                            "duration_ticks": 480,
                            "velocity": 80,
                        }
                    ],
                }
            ]
        )
    )


def _role_map_from_log(artifact_log) -> dict:
    wanted = {
        "brief": "agent.brief.v1",
        "harmony_plan": "agent.harmony_plan.v1",
        "motif_plan": "agent.motif_plan.v1",
        "arrangement_plan": "agent.arrangement_plan.v1",
        "critique": "agent.critique.v1",
        "revision_plan": "agent.revision_plan.v1",
    }
    by_type = {}
    for art in artifact_log:
        by_type[art.content_type] = {
            "artifact_id": art.artifact_id,
            "content_type": art.content_type,
        }
    return {role: by_type.get(ctype) for role, ctype in wanted.items()}


def test_spine_apply_binds_artifact_roles(project_db):
    source = _source()
    created = store.create_project(
        "Accept",
        composition=source.model_dump(mode="json"),
        db_path=project_db,
    )
    record = store.get_project(created.id, db_path=project_db)
    from app.db.connection import get_connection

    with get_connection(project_db) as conn:
        branch = conn.execute(
            "SELECT * FROM project_branches WHERE project_id = ? AND id = ?",
            (created.id, record.active_branch_id),
        ).fetchone()

    result = asyncio.run(run_spine_workflow(source, max_revisions=0))
    role_map = _role_map_from_log(result.artifact_log)
    assert role_map["brief"] is not None
    assert role_map["harmony_plan"] is not None
    assert role_map["motif_plan"] is not None
    assert role_map["arrangement_plan"] is not None
    assert role_map["critique"] is not None
    assert role_map["revision_plan"] is None

    # Payloads non-playable (no score tracks / absolute note events)
    for art in result.artifact_log:
        payload = art.payload or {}
        assert "tracks" not in payload
        assert "note_events" not in payload
        assert "musicxml" not in payload
        if art.content_type.startswith("agent.") and art.content_type.endswith("_plan.v1"):
            assert "events" not in payload

    envelopes = [a.model_dump(mode="json") for a in result.artifact_log]
    candidate = result.candidate
    assert candidate is not None
    commit = history.commit_revision(
        created.id,
        DurableCommitRequest(
            branch_id=branch["id"],
            expected_active_branch_id=branch["id"],
            expected_working_version=int(branch["working_version"]),
            expected_head_revision_id=record.current_revision_id,
            expected_source_fingerprint=branch["working_fingerprint"],
            composition=candidate,
            operation_type=RevisionOperationType.MULTI_AGENT_APPLY,
            checkpoint_dirty_draft=True,
            ai=AiProvenance(
                provider="fake",
                model="fake",
                generation_parameters={
                    "pipeline_id": "agent_spine_v1",
                    "artifact_role_map": role_map,
                    "artifact_envelopes": envelopes,
                },
            ),
        ),
        db_path=project_db,
    )
    assert commit.revision_created is True
    detail = history.get_revision_detail(
        created.id, commit.current_revision_id, db_path=project_db
    )
    ai_artifacts = detail.revision.summary.get("ai_artifacts") or {}
    roles = ai_artifacts.get("roles") or {}
    assert roles["brief"]["artifact_id"] == role_map["brief"]["artifact_id"]
    assert roles["harmony_plan"]["artifact_id"] == role_map["harmony_plan"]["artifact_id"]
    assert roles["motif_plan"]["artifact_id"] == role_map["motif_plan"]["artifact_id"]
    assert roles["arrangement_plan"]["artifact_id"] == role_map["arrangement_plan"]["artifact_id"]
    assert roles["critique"]["artifact_id"] == role_map["critique"]["artifact_id"]
    assert roles["revision_plan"] is None

    # Extended critique payloads remain promote-valid (findings optional / structured).
    critique_art = next(a for a in result.artifact_log if a.content_type == "agent.critique.v1")
    critique_payload = critique_art.payload or {}
    assert critique_payload.get("schema_version") == "agent.critique.v1"
    assert "tracks" not in critique_payload
    findings = critique_payload.get("findings") or []
    assert isinstance(findings, list)
    for finding in findings:
        assert "tracks" not in finding
        assert "events" not in finding


def test_ai_agents_forbid_workspace_imports():
    package = Path(__file__).resolve().parents[1] / "app" / "ai_agents"
    forbidden = (
        "agent_artifact_workspace",
        "agent_artifact_settings",
        "from app.services.project_store",
        "from app.services.project_history_store",
        'os.environ.get("PROJECT_DB_PATH"',
        "os.environ['PROJECT_DB_PATH']",
    )
    for path in package.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{path.name} contains {token}"
