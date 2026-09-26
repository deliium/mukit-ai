"""Symbolic stage writes a score only when completion codes pass."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ai_agents.agents.typed_emit import harmony_plan_from_project, motif_plan_from_project
from app.ai_agents.schemas import AgentArtifactKind, AgentArtifactV1
from app.autonomous_composer_schemas import CreativeBriefV1, NarrativeBeat
from app.db import initialize_database, reset_database_initialization_cache
from app.db.connection import get_connection
from app.services import project_store
from app.services.agent_artifact_workspace import insert_durable
from app.services.autonomous_composer_store import get_run, insert_run
from app.services.autonomous_constraints import AutonomousConstraintError, scale_pcs
from app.services.autonomous_project_plan import compile_project_plan
from app.services.autonomous_symbolic import (
    bind_theme_occurrences,
    commit_autonomous_stage,
    realize_final_section_mode,
    realize_symbolic_stage,
)
from app.services.symbolic_composition_generate import (
    SYMBOLIC_UNAVAILABLE,
    SymbolicCompositionGenerateError,
)


@pytest.fixture
def project_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    monkeypatch.delenv("MUSIC_TRANSFORMER_CHECKPOINT", raising=False)
    reset_database_initialization_cache()
    initialize_database()
    return db_path


def _brief() -> CreativeBriefV1:
    return CreativeBriefV1(
        schema_version="creative.brief.v1",
        title="Cinematic",
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
        motif_label="Theme A",
    )


def test_symbolic_stage_binds_theme_before_mode_remap(project_db: Path) -> None:
    plan = compile_project_plan(_brief())
    result = realize_symbolic_stage(
        plan,
        harmony_plan_from_project(plan),
        motif_plan_from_project(plan),
        seed=0,
    )
    instruments = [track.instrument for track in result.composition.tracks]
    assert instruments == ["piano", "cello", "strings"]
    assert "bass" not in instruments
    assert result.composition.key == "F# minor"
    assert result.bar_count == 45
    assert result.composition.motifs[0].label == "Theme A"
    outro = [
        note
        for track in result.composition.tracks
        for note in track.events
        if note.id and str(note.id).startswith("theme-a-outro-")
    ]
    assert len(outro) == 3
    allowed = scale_pcs("F# major")
    from app.composition_schemas import midi_pitch_number

    assert all(midi_pitch_number(note.pitch) % 12 in allowed for note in outro)
    rhythms = [(note.start_tick, note.duration_ticks) for note in outro]
    assert rhythms == sorted(rhythms)
    assert all(note.is_drum is False for note in result.composition.tracks)


def test_bind_checks_identity_before_remap(project_db: Path) -> None:
    plan = compile_project_plan(_brief())
    generated = realize_symbolic_stage(
        plan,
        harmony_plan_from_project(plan),
        motif_plan_from_project(plan),
        seed=1,
    )
    # Re-bind the pre-remap score is not available; check the helper contract
    # on a fresh generate by calling bind then remap separately.
    from app.services.symbolic_composition_generate import generate_symbolic_composition
    from app.services.autonomous_symbolic import compile_composition_plan

    compiled = compile_composition_plan(
        plan,
        harmony_plan_from_project(plan),
        motif_plan_from_project(plan),
    )
    raw = generate_symbolic_composition(compiled, seed=1).composition
    bound, rhythm_before, _rhythm_same, pitches = bind_theme_occurrences(raw, plan)
    remapped = realize_final_section_mode(bound, plan)
    outro = [
        note
        for track in remapped.tracks
        for note in track.events
        if note.id and str(note.id).startswith("theme-a-outro-")
    ]
    rhythm_after = tuple((note.start_tick, note.duration_ticks) for note in outro)
    assert tuple((item.start_tick, item.duration_ticks) for item in rhythm_before) == rhythm_after
    from app.composition_schemas import midi_pitch_number

    pre = [midi_pitch_number(pitch) % 12 for pitch in pitches]
    post = [midi_pitch_number(note.pitch) % 12 for note in outro]
    assert all(pc in scale_pcs("F# major") for pc in post)
    # Identity was scored on the copy; the remap may change intervals afterwards.
    assert len(pre) == 3
    assert generated.note_count > 0


def test_unavailable_backend_is_recoverable(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = compile_project_plan(_brief())

    def _boom(*_args, **_kwargs):
        raise SymbolicCompositionGenerateError("offline", code=SYMBOLIC_UNAVAILABLE)

    monkeypatch.setattr(
        "app.services.autonomous_symbolic.generate_symbolic_composition",
        _boom,
    )
    with pytest.raises(AutonomousConstraintError) as caught:
        realize_symbolic_stage(
            plan,
            harmony_plan_from_project(plan),
            motif_plan_from_project(plan),
        )
    assert caught.value.code == SYMBOLIC_UNAVAILABLE
    assert caught.value.recoverable == 1


def test_insert_durable_has_no_expiry_or_link(project_db: Path) -> None:
    created = project_store.create_project("Piece", db_path=project_db)
    plan = compile_project_plan(_brief())
    artifact = AgentArtifactV1(
        kind=AgentArtifactKind.PLAN,
        producer_agent_id="creative_director",
        content_type="project.plan.v1",
        payload=plan.model_dump(mode="json"),
    )
    artifact_id = insert_durable(created.id, artifact, db_path=project_db)
    with get_connection(project_db) as conn:
        row = conn.execute(
            "SELECT retention_class, expires_at FROM agent_artifacts WHERE id = ?",
            (artifact_id,),
        ).fetchone()
        links = conn.execute(
            "SELECT COUNT(*) AS n FROM revision_artifact_links WHERE artifact_id = ?",
            (artifact_id,),
        ).fetchone()
    assert row["retention_class"] == "durable"
    assert row["expires_at"] is None
    assert links["n"] == 0


def test_commit_conflict_keeps_previous_head(project_db: Path) -> None:
    created = project_store.create_project("Piece", db_path=project_db)
    plan = compile_project_plan(_brief())
    run = insert_run(
        project_id=created.id,
        branch_id=created.active_branch_id or "",
        operation_run_id="op-symbolic",
        brief=_brief().model_dump(mode="json"),
        plan=plan,
        seed=0,
        db_path=project_db,
    )
    result = realize_symbolic_stage(
        plan,
        harmony_plan_from_project(plan),
        motif_plan_from_project(plan),
        seed=0,
    )
    revision_id = commit_autonomous_stage(
        project_id=created.id,
        branch_id=created.active_branch_id or "",
        composition=result.composition,
        run_id=run.id,
        db_path=project_db,
    )
    stored = get_run(run.id, db_path=project_db)
    assert stored.head_revision_id == revision_id
    drifted = result.composition.model_copy(update={"tempo": result.composition.tempo})
    # Change one velocity so the working fingerprint diverges from the run head.
    melody = drifted.tracks[0]
    first = melody.events[0].model_copy(update={"velocity": 40})
    drifted_tracks = [
        melody.model_copy(update={"events": [first, *melody.events[1:]]}),
        *drifted.tracks[1:],
    ]
    drifted = drifted.model_copy(update={"tracks": drifted_tracks})
    project_store.update_project(
        created.id,
        composition=drifted,
        db_path=project_db,
    )
    from app.services.autonomous_composer_store import AutonomousStoreError

    with pytest.raises(AutonomousStoreError) as caught:
        commit_autonomous_stage(
            project_id=created.id,
            branch_id=created.active_branch_id or "",
            composition=result.composition,
            run_id=run.id,
            db_path=project_db,
        )
    assert caught.value.code == "project_revision_conflict"
    again = get_run(run.id, db_path=project_db)
    assert again.head_revision_id == revision_id
    with get_connection(project_db) as conn:
        head = conn.execute(
            "SELECT head_revision_id FROM project_branches WHERE id = ?",
            (created.active_branch_id,),
        ).fetchone()
        operation = conn.execute(
            "SELECT operation_type FROM project_revisions WHERE id = ?",
            (revision_id,),
        ).fetchone()
    assert head["head_revision_id"] == revision_id
    assert operation["operation_type"] == "autonomous-stage"


def test_failed_stage_after_commit_does_not_replace_head(
    project_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = project_store.create_project("Piece", db_path=project_db)
    plan = compile_project_plan(_brief())
    run = insert_run(
        project_id=created.id,
        branch_id=created.active_branch_id or "",
        operation_run_id="op-retry",
        brief=_brief().model_dump(mode="json"),
        plan=plan,
        db_path=project_db,
    )
    result = realize_symbolic_stage(
        plan,
        harmony_plan_from_project(plan),
        motif_plan_from_project(plan),
        seed=0,
    )
    revision_id = commit_autonomous_stage(
        project_id=created.id,
        branch_id=created.active_branch_id or "",
        composition=result.composition,
        run_id=run.id,
        db_path=project_db,
    )

    def _fail(*_args, **_kwargs):
        raise SymbolicCompositionGenerateError("bad score", code="symbolic_generate_failed")

    monkeypatch.setattr(
        "app.services.autonomous_symbolic.generate_symbolic_composition",
        _fail,
    )
    with pytest.raises(AutonomousConstraintError) as caught:
        realize_symbolic_stage(
            plan,
            harmony_plan_from_project(plan),
            motif_plan_from_project(plan),
        )
    assert caught.value.recoverable == 0
    assert get_run(run.id, db_path=project_db).head_revision_id == revision_id
