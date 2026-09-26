"""Revision stage skips approve, commits a contained pass, and contains crashes."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ai_agents import registry as agent_registry
from app.ai_agents.revision_loop import _run_agent_node
from app.ai_agents.schemas import AgentArtifactKind, AgentArtifactV1
from app.ai_agents.workflow import build_initial_context
from app.ai_runtime import registry as model_registry
from app.autonomous_composer_schemas import CreativeBriefV1, NarrativeBeat
from app.composition_schemas import CompositionV2
from app.db import initialize_database, reset_database_initialization_cache
from app.db.connection import get_connection
from app.main import app
from app.services import project_store
from app.services.autonomous_composer_store import get_run, insert_run
from app.services.autonomous_project_plan import compile_project_plan
from app.services.autonomous_revision import apply_revision_stage
from app.services.autonomous_symbolic import commit_autonomous_stage
from tests.test_composition_v2_schema import minimal_v2


@pytest.fixture
def project_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    monkeypatch.delenv("FAKE_CRITIC_REVISE_PASSES", raising=False)
    reset_database_initialization_cache()
    initialize_database()
    agent_registry.clear_registry_for_tests()
    model_registry.clear_registry_for_tests()
    env = dict(**{k: v for k, v in __import__("os").environ.items()})
    model_registry.reload_registry(env)
    agent_registry.reload_agent_registry(env)
    return db_path


def _brief() -> CreativeBriefV1:
    return CreativeBriefV1(
        schema_version="creative.brief.v1",
        duration_seconds=150,
        narrative=[
            NarrativeBeat(intent="sparse_opening", text="cold sparse opening"),
            NarrativeBeat(intent="establish_theme", text="introduce Theme A"),
            NarrativeBeat(intent="build", text="increase tension"),
            NarrativeBeat(intent="climax", text="strong climax"),
            NarrativeBeat(intent="resolve", text="quiet transformed ending"),
        ],
        instrumentation=["piano", "cello", "strings"],
        forbidden_instrument_families=["drums"],
        opening_key="F# minor",
        final_section_key="F# major",
    )


def _piece() -> CompositionV2:
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
                        {"pitch": "C4", "start_tick": 0, "duration_ticks": 480, "velocity": 80},
                        {"pitch": "E4", "start_tick": 1920, "duration_ticks": 480, "velocity": 80},
                    ],
                }
            ]
        )
    )


def _seed(db_path: Path):
    created = project_store.create_project("Piece", db_path=db_path)
    plan = compile_project_plan(_brief())
    run = insert_run(
        project_id=created.id,
        branch_id=created.active_branch_id or "",
        operation_run_id="op-revision",
        brief=_brief().model_dump(mode="json"),
        plan=plan,
        db_path=db_path,
    )
    composition = _piece()
    revision_id = commit_autonomous_stage(
        project_id=created.id,
        branch_id=created.active_branch_id or "",
        composition=composition,
        run_id=run.id,
        db_path=db_path,
    )
    return created, run, composition, revision_id


def _approve_critique() -> AgentArtifactV1:
    return AgentArtifactV1(
        kind=AgentArtifactKind.CRITIQUE,
        producer_agent_id="critic",
        content_type="agent.critique.v1",
        payload={
            "schema_version": "agent.critique.v1",
            "recommendation": "approve",
            "summary": "stylistic note only",
            "findings": [
                {
                    "stratum": "stylistic",
                    "category": "contrast",
                    "code": "climax_lacks_contrast",
                    "severity": "info",
                    "explanation": "subjective",
                }
            ],
        },
    )


def test_approve_skips_revision_and_keeps_head(project_db: Path) -> None:
    created, run, composition, revision_id = _seed(project_db)
    outcome = asyncio.run(
        apply_revision_stage(
            project_id=created.id,
            branch_id=created.active_branch_id or "",
            run_id=run.id,
            composition=composition,
            critique=_approve_critique(),
            db_path=project_db,
        )
    )
    assert outcome.status == "skipped"
    assert outcome.revision_id is None
    stored = get_run(run.id, db_path=project_db)
    assert stored.head_revision_id == revision_id
    revision = next(stage for stage in stored.stages if stage.stage_id == "revision")
    assert revision.status == "skipped"


def test_scripted_revise_commits_when_preserve_holds(
    project_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CRITIC_REVISE_PASSES", "1")
    created, run, composition, revision_id = _seed(project_db)

    async def _critique():
        context = build_initial_context(composition)
        context, _stage, _codes = await _run_agent_node(
            "critic",
            context,
            agent_model_overrides=None,
            selection=None,
            parameters={"fake_revise_hard_finding": True, "fake_revise_passes": 1},
        )
        return context.critique

    critique = asyncio.run(_critique())
    assert critique is not None
    assert critique.payload.get("recommendation") == "revise"
    outcome = asyncio.run(
        apply_revision_stage(
            project_id=created.id,
            branch_id=created.active_branch_id or "",
            run_id=run.id,
            composition=composition,
            critique=critique,
            critic_parameters={"fake_revise_hard_finding": True, "fake_revise_passes": 1},
            db_path=project_db,
        )
    )
    assert outcome.status == "completed"
    assert outcome.revision_id is not None
    assert outcome.revision_id != revision_id
    assert get_run(run.id, db_path=project_db).head_revision_id == outcome.revision_id


def test_preserve_failure_keeps_symbolic_head(
    project_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CRITIC_REVISE_PASSES", "1")
    created, run, composition, revision_id = _seed(project_db)

    async def _critique():
        context = build_initial_context(composition)
        context, _stage, _codes = await _run_agent_node(
            "critic",
            context,
            agent_model_overrides=None,
            selection=None,
            parameters={"fake_revise_hard_finding": True, "fake_revise_passes": 1},
        )
        return context.critique

    critique = asyncio.run(_critique())

    def _violate(*_args, **_kwargs) -> bool:
        return False

    monkeypatch.setattr(
        "app.ai_agents.revision_loop.assert_preserve_outside_targets",
        _violate,
    )
    outcome = asyncio.run(
        apply_revision_stage(
            project_id=created.id,
            branch_id=created.active_branch_id or "",
            run_id=run.id,
            composition=composition,
            critique=critique,
            critic_parameters={"fake_revise_hard_finding": True, "fake_revise_passes": 1},
            db_path=project_db,
        )
    )
    assert outcome.revision_id is None
    assert outcome.stop_reason == "validation_failed_kept_last_valid"
    assert get_run(run.id, db_path=project_db).head_revision_id == revision_id
    with get_connection(project_db) as conn:
        head = conn.execute(
            "SELECT head_revision_id FROM project_branches WHERE id = ?",
            (created.active_branch_id,),
        ).fetchone()
    assert head["head_revision_id"] == revision_id


def test_system_exit_fails_stage_and_ready_stays_up(
    project_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    created, run, composition, revision_id = _seed(project_db)
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

    real_get = agent_registry.get_agent

    def _get(agent_id: str):
        if agent_id == "harmony":
            return _Boom()
        return real_get(agent_id)

    monkeypatch.setattr("app.ai_agents.revision_loop.get_agent", _get)
    outcome = asyncio.run(
        apply_revision_stage(
            project_id=created.id,
            branch_id=created.active_branch_id or "",
            run_id=run.id,
            composition=composition,
            critique=critique,
            db_path=project_db,
        )
    )
    assert outcome.status == "failed"
    assert outcome.failure_code == "operation_model_crashed"
    assert get_run(run.id, db_path=project_db).head_revision_id == revision_id
    with TestClient(app) as client:
        ready = client.get("/ready")
    assert ready.status_code == 200
