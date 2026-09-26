"""Compile a composition plan, write the first score, and commit it.

Theme identity is checked on the rhythmic copy before the outro mode remap.
A failed check does not write a revision.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.ai_agents.agents.typed_emit import tonic_chord_symbol
from app.ai_agents.artifact_schemas import AgentHarmonyPlanV1, AgentMotifPlanV1
from app.autonomous_composer_schemas import AUTONOMOUS_CONSTRAINT_FAILED, ProjectPlanV1
from app.composition_plan_schemas import (
    CompositionPlan,
    CompositionPlanError,
    PlanDensity,
    PlanModulation,
    PlanModulationTarget,
    PlanSectionDensity,
)
from app.composition_schemas import (
    CompositionV2,
    CompositionV2MotifDefinition,
    CompositionV2MotifOccurrence,
    CompositionV2NoteEvent,
    midi_pitch_number,
)
from app.project_history_schemas import RevisionOperationType
from app.services.autonomous_constraints import (
    AutonomousConstraintError,
    NoteRhythm,
    assert_stage_completion,
    constraints_from_project_plan,
    pitch_rhythm,
    scale_pcs,
    section_start_tick,
)
from app.services.autonomous_composer_store import (
    AutonomousStoreError,
    get_run,
    update_run_fields,
)
from app.services.composition_melody_analysis import _midi_to_pitch
from app.services.composition_motif_similarity import verify_motif_identity
from app.services.composition_plan_constraints import ensure_plan_conforms
from app.services.composition_planner import (
    ComposerFormPlan,
    ComposerFormSection,
    ComposerHarmonyEvent,
    ComposerHarmonyPlan,
    ComposerThemePlan,
    ComposerThemeSeedSpec,
)
from app.services.project_history_store import (
    ProjectRevisionConflictError,
    _load_branch_command_state,
    commit_durable_revision,
)
from app.services.symbolic_composition_generate import (
    SYMBOLIC_UNAVAILABLE,
    SymbolicCompositionGenerateError,
    generate_symbolic_composition,
)
from app.db.connection import get_connection

logger = logging.getLogger(__name__)

_CELL_NOTE_COUNT = 3


@dataclass(frozen=True)
class _RelativeNote:
    relative_start_tick: int
    duration_ticks: int
    pitch_semitone_offset: int


@dataclass(frozen=True)
class SymbolicStageResult:
    composition: CompositionV2
    model_id: str
    seed: int
    note_count: int
    bar_count: int


def compile_composition_plan(
    project_plan: ProjectPlanV1,
    harmony_plan: AgentHarmonyPlanV1 | dict[str, Any],
    motif_plan: AgentMotifPlanV1 | dict[str, Any],
) -> CompositionPlan:
    """Build composition.plan.v1. Instrument order stays the brief order."""
    harmony = (
        harmony_plan
        if isinstance(harmony_plan, AgentHarmonyPlanV1)
        else AgentHarmonyPlanV1.model_validate(harmony_plan)
    )
    motif = (
        motif_plan
        if isinstance(motif_plan, AgentMotifPlanV1)
        else AgentMotifPlanV1.model_validate(motif_plan)
    )
    sections = [
        ComposerFormSection(
            type=section.type,
            start_bar=section.start_bar,
            bar_count=section.bar_count,
        )
        for section in project_plan.sections
    ]
    chord_events = list(harmony.chord_events)
    if not chord_events:
        symbol = tonic_chord_symbol(project_plan.constraints.opening_key)
        chord_events_payload = [
            ComposerHarmonyEvent(bar=section.start_bar, chord=symbol, function="tonic")
            for section in project_plan.sections
        ]
    else:
        chord_events_payload = [
            ComposerHarmonyEvent(bar=event.bar, chord=event.chord, function=event.function)
            for event in chord_events
        ]
    motif_section_index = next(
        (
            index
            for index, section in enumerate(project_plan.sections)
            if section.id == project_plan.constraints.motif_section_id
        ),
        0,
    )
    outro_index = next(
        (index for index, section in enumerate(project_plan.sections) if section.type == "outro"),
        len(project_plan.sections) - 1,
    )
    targets = []
    if project_plan.constraints.final_section_key:
        targets.append(
            PlanModulationTarget(
                section_index=outro_index,
                key=project_plan.constraints.final_section_key,
                start_bar=project_plan.sections[outro_index].start_bar,
            )
        )
    label = motif.motifs[0].motif_label if motif.motifs else project_plan.constraints.motif_label
    plan = CompositionPlan(
        form=ComposerFormPlan(
            tempo=project_plan.constraints.opening_tempo,
            key=project_plan.constraints.opening_key,
            time_signature=project_plan.constraints.time_signature,
            bar_count=project_plan.constraints.duration_bars,
            sections=sections,
            instrumentation=list(project_plan.constraints.instruments),
        ),
        modulation=PlanModulation(targets=targets),
        harmony=ComposerHarmonyPlan(events=chord_events_payload),
        motifs_themes=ComposerThemePlan(
            enabled=False,
            motif_label=label,
            seed=ComposerThemeSeedSpec(section_index=motif_section_index),
        ),
        density=PlanDensity(
            per_section=[
                PlanSectionDensity(section_index=index, band=section.density)
                for index, section in enumerate(project_plan.sections)
            ]
        ),
    )
    return ensure_plan_conforms(plan, constraints_from_project_plan(project_plan))


def realize_symbolic_stage(
    project_plan: ProjectPlanV1,
    harmony_plan: AgentHarmonyPlanV1 | dict[str, Any],
    motif_plan: AgentMotifPlanV1 | dict[str, Any],
    *,
    seed: int = 0,
) -> SymbolicStageResult:
    """Generate, bind Theme A, remap the outro, and re-validate. No revision write."""
    logger.info(
        "Autonomous symbolic stage started",
        extra={
            "model_id": "pending",
            "seed": seed,
            "bar_count": project_plan.constraints.duration_bars,
            "note_count": 0,
            "failure_code": None,
        },
    )
    try:
        plan = compile_composition_plan(project_plan, harmony_plan, motif_plan)
        generated = generate_symbolic_composition(plan, seed=seed)
    except CompositionPlanError as exc:
        logger.warning(
            "Autonomous symbolic plan rejected",
            extra={"failure_code": exc.code, "completion_code": "hard_constraints_ok"},
        )
        raise AutonomousConstraintError(
            "composition plan rejected",
            completion_code="hard_constraints_ok",
            recoverable=0,
            code=AUTONOMOUS_CONSTRAINT_FAILED,
        ) from exc
    except SymbolicCompositionGenerateError as exc:
        recoverable = 1 if exc.code == SYMBOLIC_UNAVAILABLE else 0
        logger.warning(
            "Autonomous symbolic generate failed",
            extra={"failure_code": exc.code, "completion_code": None},
        )
        raise AutonomousConstraintError(
            "symbolic generate failed",
            completion_code="composition_v2_ok",
            recoverable=recoverable,
            code=exc.code,
        ) from exc
    composition = generated.composition
    failure_code = None
    try:
        composition, rhythm_before, rhythm_after, pitches = bind_theme_occurrences(
            composition,
            project_plan,
        )
        if project_plan.constraints.final_section_key:
            composition = realize_final_section_mode(composition, project_plan)
        note_count = sum(len(track.events) for track in composition.tracks)
        _assert_symbolic_codes(
            composition,
            project_plan,
            rhythm_before=rhythm_before,
            rhythm_after=rhythm_after,
            outro_pitches=pitches,
        )
    except AutonomousConstraintError as exc:
        failure_code = exc.code
        logger.info(
            "Autonomous symbolic stage finished",
            extra={
                "model_id": generated.model_id,
                "seed": seed,
                "bar_count": project_plan.constraints.duration_bars,
                "note_count": 0,
                "failure_code": failure_code,
            },
        )
        raise
    logger.info(
        "Autonomous symbolic stage finished",
        extra={
            "model_id": generated.model_id,
            "seed": seed,
            "bar_count": composition.bar_count,
            "note_count": note_count,
            "failure_code": None,
        },
    )
    return SymbolicStageResult(
        composition=composition,
        model_id=generated.model_id,
        seed=seed,
        note_count=note_count,
        bar_count=composition.bar_count,
    )


def bind_theme_occurrences(
    composition: CompositionV2,
    project_plan: ProjectPlanV1,
) -> tuple[CompositionV2, tuple[NoteRhythm, ...], tuple[NoteRhythm, ...], tuple[str, ...]]:
    """Copy the theme cell into the outro, then verify identity before any remap."""
    theme_spec = next(
        section
        for section in project_plan.sections
        if section.id == project_plan.constraints.motif_section_id
    )
    outro_spec = next(section for section in project_plan.sections if section.type == "outro")
    theme = _match_section(composition, theme_spec.start_bar, theme_spec.type)
    outro = _match_section(composition, outro_spec.start_bar, outro_spec.type)
    melody = next(track for track in composition.tracks if track.role in {"melody", "lead"})
    theme_notes = _notes_in_section(melody.events, composition, theme)[:_CELL_NOTE_COUNT]
    if len(theme_notes) < _CELL_NOTE_COUNT:
        raise AutonomousConstraintError(
            "motif cell missing",
            completion_code="motif_identity_ok",
            code="autonomous_motif_missing",
        )
    shift = section_start_tick(composition, outro.start_bar) - theme_notes[0].start_tick
    copied = [
        note.model_copy(
            update={
                "id": f"theme-a-outro-{index}",
                "start_tick": note.start_tick + shift,
            }
        )
        for index, note in enumerate(theme_notes)
    ]
    window_end = copied[-1].start_tick + copied[-1].duration_ticks
    window_start = copied[0].start_tick
    kept = [
        note
        for note in melody.events
        if not (window_start <= note.start_tick < window_end)
    ]
    source_rel = _relative_notes(theme_notes)
    target_rel = _relative_notes(copied)
    if project_plan.constraints.motif_must_remain_recognizable:
        verification = verify_motif_identity(
            source_rel,
            target_rel,
            operation="repeat",
            exact_transform_verified=_cells_equal(source_rel, target_rel),
        )
        if not verification.passed:
            logger.warning(
                "Autonomous constraint failed",
                extra={"completion_code": "motif_identity_ok"},
            )
            raise AutonomousConstraintError(
                "motif identity failed",
                completion_code="motif_identity_ok",
            )
    rhythm_before = pitch_rhythm(copied)
    updated_tracks = []
    for track in composition.tracks:
        if track.id != melody.id:
            updated_tracks.append(track)
            continue
        updated_tracks.append(track.model_copy(update={"events": [*kept, *copied]}))
    motif = CompositionV2MotifDefinition(
        id="theme-a",
        label=project_plan.constraints.motif_label,
        occurrences=[
            CompositionV2MotifOccurrence(
                id="theme-a-original",
                track_id=melody.id,
                event_ids=[note.id or "" for note in theme_notes],
                relationship="original",
            ),
            CompositionV2MotifOccurrence(
                id="theme-a-outro",
                track_id=melody.id,
                event_ids=[note.id or "" for note in copied],
                relationship="repeat",
            ),
        ],
    )
    bound = composition.model_copy(update={"tracks": updated_tracks, "motifs": [motif]})
    return bound, rhythm_before, rhythm_before, tuple(note.pitch for note in copied)


def realize_final_section_mode(
    composition: CompositionV2,
    project_plan: ProjectPlanV1,
) -> CompositionV2:
    """Snap the copied outro theme pitches onto the final key. Rhythm stays."""
    final_key = project_plan.constraints.final_section_key
    if not final_key:
        return composition
    allowed = scale_pcs(final_key)
    updated_tracks = []
    for track in composition.tracks:
        events = []
        for note in track.events:
            if note.id and str(note.id).startswith("theme-a-outro-"):
                events.append(
                    note.model_copy(update={"pitch": _snap_pitch(note.pitch, allowed)})
                )
            else:
                events.append(note)
        updated_tracks.append(track.model_copy(update={"events": events}))
    return composition.model_copy(update={"tracks": updated_tracks})


def commit_autonomous_stage(
    *,
    project_id: str,
    branch_id: str,
    composition: CompositionV2,
    run_id: str | None = None,
    db_path: Path | str | None = None,
) -> str:
    """Re-read the branch and commit when the score fingerprint still matches."""
    path = Path(db_path) if db_path is not None else None
    stored_fingerprint = None
    if run_id is not None:
        stored_fingerprint = get_run(run_id, db_path=path).composition_fingerprint
    with get_connection(path) as conn:
        state = _load_branch_command_state(conn, project_id, branch_id)
        if stored_fingerprint and state.working_fingerprint != stored_fingerprint:
            logger.warning(
                "Autonomous stage revision conflict",
                extra={"code": "project_revision_conflict", "run_id": (run_id or "")[:16]},
            )
            raise AutonomousStoreError(
                "revision conflict",
                code="project_revision_conflict",
            )
        try:
            result = commit_durable_revision(
                conn,
                project_id,
                branch_id=branch_id,
                expected_active_branch_id=state.active_branch_id,
                expected_working_version=state.working_version,
                expected_head_revision_id=state.head_revision_id,
                expected_source_fingerprint=state.working_fingerprint,
                composition=composition,
                operation_type=RevisionOperationType.AUTONOMOUS_STAGE.value,
            )
        except ProjectRevisionConflictError as exc:
            raise AutonomousStoreError(
                "revision conflict",
                code="project_revision_conflict",
            ) from exc
    if run_id is not None:
        update_run_fields(
            run_id,
            head_revision_id=result.head_revision_id,
            composition_fingerprint=result.working_fingerprint,
            set_head_revision_id=True,
            set_fingerprint=True,
            db_path=path,
        )
    logger.info(
        "Autonomous stage committed",
        extra={
            "project_id": project_id,
            "run_id": (run_id or "")[:16],
            "revision_id": result.head_revision_id,
        },
    )
    return result.head_revision_id


def _assert_symbolic_codes(
    composition: CompositionV2,
    project_plan: ProjectPlanV1,
    *,
    rhythm_before: tuple[NoteRhythm, ...],
    rhythm_after: tuple[NoteRhythm, ...],
    outro_pitches: tuple[str, ...],
) -> None:
    if project_plan.constraints.final_section_key:
        outro_notes = [
            note
            for track in composition.tracks
            for note in track.events
            if note.id and str(note.id).startswith("theme-a-outro-")
        ]
        rhythm_after = pitch_rhythm(outro_notes)
        outro_pitches = tuple(note.pitch for note in outro_notes)
    codes = [
        "composition_v2_ok",
        "hard_constraints_ok",
        "no_forbidden_instruments",
        "motif_identity_ok",
    ]
    if project_plan.constraints.final_section_key:
        codes.append("final_section_mode_ok")
    for code in codes:
        assert_stage_completion(
            code,
            composition=composition,
            plan=project_plan,
            motif_rows_present=bool(composition.motifs),
            rhythm_before=rhythm_before,
            rhythm_after=rhythm_after,
            outro_pitches=outro_pitches,
        )


def _match_section(composition: CompositionV2, start_bar: int, section_type: str):
    for section in composition.sections:
        if section.start_bar == start_bar and section.type == section_type:
            return section
    raise AutonomousConstraintError(
        "section match failed",
        completion_code="composition_v2_ok",
    )


def _notes_in_section(events, composition: CompositionV2, section) -> list[CompositionV2NoteEvent]:
    start = section_start_tick(composition, section.start_bar)
    end = start + section.duration_ticks
    return [note for note in events if start <= note.start_tick < end]


def _relative_notes(events: list[CompositionV2NoteEvent]) -> list[_RelativeNote]:
    origin = events[0].start_tick
    root = midi_pitch_number(events[0].pitch)
    return [
        _RelativeNote(
            relative_start_tick=note.start_tick - origin,
            duration_ticks=note.duration_ticks,
            pitch_semitone_offset=midi_pitch_number(note.pitch) - root,
        )
        for note in events
    ]


def _cells_equal(left: list[_RelativeNote], right: list[_RelativeNote]) -> bool:
    if len(left) != len(right):
        return False
    return all(
        a.relative_start_tick == b.relative_start_tick
        and a.duration_ticks == b.duration_ticks
        and a.pitch_semitone_offset == b.pitch_semitone_offset
        for a, b in zip(left, right, strict=True)
    )


def _snap_pitch(pitch: str, allowed: set[int]) -> str:
    midi = midi_pitch_number(pitch)
    if midi % 12 in allowed:
        return pitch
    best = midi
    best_distance = 99
    for delta in range(-6, 7):
        candidate = midi + delta
        if 0 <= candidate <= 127 and candidate % 12 in allowed and abs(delta) < best_distance:
            best = candidate
            best_distance = abs(delta)
    return _midi_to_pitch(best)
