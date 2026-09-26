"""Arrangement preserve and expression pitch stability."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.ai_agents import registry as agent_registry
from app.ai_agents.schemas import AgentOperation, AgentRunRequest
from app.ai_agents.workflow import build_initial_context
from app.ai_runtime import registry as model_registry
from app.autonomous_composer_schemas import CreativeBriefV1, NarrativeBeat
from app.composition_schemas import CompositionV2, CompositionV2Track
from app.db import initialize_database, reset_database_initialization_cache
from app.services import project_store
from app.services.autonomous_composer_store import get_run, insert_run
from app.services.autonomous_expression import apply_arrangement_stage, apply_expression_stage
from app.services.autonomous_project_plan import compile_project_plan
from app.services.autonomous_symbolic import commit_autonomous_stage
from app.services.composition_expression import pitch_timing_fingerprint
from tests.test_composition_v2_schema import minimal_v2


@pytest.fixture
def project_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
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
        operation_run_id="op-expr",
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
    return created, run, composition, plan, revision_id


def test_advise_does_not_realize(project_db: Path) -> None:
    composition = _piece()
    agent = agent_registry.get_agent("performance_expression")
    request = AgentRunRequest(
        agent_id="performance_expression",
        operation=AgentOperation.ADVISE,
        context=build_initial_context(composition),
        selection={},
    )
    result = asyncio.run(agent.run(request))
    assert result.working_draft_update is None
    assert result.artifacts[0].content_type == "agent.performance_plan.v1"


def test_expression_changes_velocity_only(project_db: Path) -> None:
    created, run, composition, plan, revision_id = _seed(project_db)
    before = pitch_timing_fingerprint(composition)
    outcome = asyncio.run(
        apply_expression_stage(
            project_id=created.id,
            branch_id=created.active_branch_id or "",
            run_id=run.id,
            composition=composition,
            plan=plan,
            db_path=project_db,
        )
    )
    assert outcome.status == "completed"
    assert outcome.revision_id != revision_id
    stored = get_run(run.id, db_path=project_db)
    assert stored.head_revision_id == outcome.revision_id
    assert before == pitch_timing_fingerprint(composition)


def test_pitch_drift_does_not_commit(project_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    created, run, composition, plan, revision_id = _seed(project_db)

    def _drift(score, _bands):
        track = score.tracks[0]
        note = track.events[0].model_copy(update={"pitch": "D4"})
        drifted = track.model_copy(update={"events": [note, *track.events[1:]]})
        return score.model_copy(update={"tracks": [drifted, *score.tracks[1:]]})

    monkeypatch.setattr(
        "app.ai_agents.agents.performance_expression.realize_expression_marks",
        _drift,
    )
    outcome = asyncio.run(
        apply_expression_stage(
            project_id=created.id,
            branch_id=created.active_branch_id or "",
            run_id=run.id,
            composition=composition,
            plan=plan,
            db_path=project_db,
        )
    )
    assert outcome.status == "failed"
    assert outcome.failure_code == "autonomous_constraint_failed"
    assert get_run(run.id, db_path=project_db).head_revision_id == revision_id


def test_drum_arrangement_does_not_commit(
    project_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    created, run, composition, plan, revision_id = _seed(project_db)
    drum = CompositionV2Track(
        id="kit-1",
        name="Kit",
        instrument="drums",
        role="rhythm",
        midi_program=0,
        channel=10,
        is_drum=True,
        events=[],
    )
    doomed = composition.model_copy(update={"tracks": [*composition.tracks, drum]})

    async def _drums(*_args, **_kwargs):
        context = build_initial_context(doomed)
        return context, {}, []

    monkeypatch.setattr("app.services.autonomous_expression._run_agent_node", _drums)
    outcome = asyncio.run(
        apply_arrangement_stage(
            project_id=created.id,
            branch_id=created.active_branch_id or "",
            run_id=run.id,
            composition=composition,
            plan=plan,
            db_path=project_db,
        )
    )
    assert outcome.status == "failed"
    assert get_run(run.id, db_path=project_db).head_revision_id == revision_id
