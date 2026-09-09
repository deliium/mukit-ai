"""Focused tests for structured theme planning and realization."""

from __future__ import annotations

from app.services.composition_planner import (
    ComposerDraftNote,
    ComposerFormPlan,
    ComposerThemeDeployment,
    ComposerThemeParameters,
    ComposerThemePlan,
    ComposerThemeSeedSpec,
    ComposerTrackDraft,
    bounded_instructions_for_prompt,
    summarize_theme_plan,
)
from app.services.composition_theme import (
    default_theme_plan_for_form,
    empty_theme_plan,
    realize_theme_plan,
    validate_theme_plan_against_form,
)


TICKS = 480
BAR = 1920


def _form(sections: list[tuple[str, int, int]]) -> ComposerFormPlan:
    total = sum(bar_count for _, _, bar_count in sections)
    return ComposerFormPlan.model_validate(
        {
            "tempo": 80,
            "key": "A minor",
            "time_signature": "4/4",
            "bar_count": total,
            "sections": [
                {"type": section_type, "start_bar": start, "bar_count": bars}
                for section_type, start, bars in sections
            ],
            "instrumentation": ["piano"],
        }
    )


def test_bounded_instructions_truncate_without_logging_contract():
    text = "x" * 600
    bounded = bounded_instructions_for_prompt(text)
    assert bounded is not None
    assert len(bounded) <= 500
    assert bounded.endswith("…")


def test_single_section_theme_plan_disabled():
    form = _form([("verse", 1, 8)])
    plan = default_theme_plan_for_form(form)
    assert plan.enabled is False
    corrected, diagnostics = validate_theme_plan_against_form(
        ComposerThemePlan(
            enabled=True,
            seed=ComposerThemeSeedSpec(section_index=0),
            deployments=[
                ComposerThemeDeployment(
                    id="dep-1",
                    target_section_index=0,
                    start_bar_offset=2,
                    operation="repeat",
                )
            ],
        ),
        form,
    )
    assert corrected.enabled is False
    assert any(item.code == "theme_target_missing" for item in diagnostics)


def test_realize_transpose_builds_canonical_motif_refs():
    form = _form([("intro", 1, 4), ("outro", 5, 4)])
    seed_events = [
        ComposerDraftNote(pitch="A4", start_tick=0, duration_ticks=TICKS, velocity=80, id="s1"),
        ComposerDraftNote(pitch="B4", start_tick=TICKS, duration_ticks=TICKS, velocity=80, id="s2"),
        ComposerDraftNote(pitch="C5", start_tick=2 * TICKS, duration_ticks=TICKS, velocity=80, id="s3"),
        ComposerDraftNote(pitch="E5", start_tick=3 * TICKS, duration_ticks=TICKS, velocity=80, id="s4"),
    ]
    later = [
        ComposerDraftNote(
            pitch="A4",
            start_tick=bar * BAR,
            duration_ticks=TICKS,
            velocity=70,
            id=f"l{bar}",
        )
        for bar in range(1, 8)
    ]
    draft = ComposerTrackDraft(
        id="melody-1",
        name="Melody",
        instrument="piano",
        role="melody",
        events=[*seed_events, *later],
    )
    plan = ComposerThemePlan(
        enabled=True,
        motif_id="motif-a",
        motif_label="Motif A",
        seed=ComposerThemeSeedSpec(section_index=0, bar_span=1),
        deployments=[
            ComposerThemeDeployment(
                id="dep-1",
                target_section_index=1,
                operation="transpose",
                parameters=ComposerThemeParameters(transpose_semitones=5),
            )
        ],
    )
    result = realize_theme_plan(theme_plan=plan, melody_draft=draft, form=form)
    assert not any(item.severity == "error" for item in result.diagnostics)
    assert len(result.motifs) == 1
    motif = result.motifs[0]
    assert motif.label == "Motif A"
    assert any(occ.relationship == "original" for occ in motif.occurrences)
    transpose = next(occ for occ in motif.occurrences if occ.relationship == "transpose")
    event_ids = {event.id for event in result.melody_draft.events if event.id}
    assert all(event_id in event_ids for event_id in transpose.event_ids)
    assert result.outcomes[0].status == "realized"
    assert result.outcomes[0].identity_score == 1.0
    summary = summarize_theme_plan(result.theme_plan)
    assert "relative_cell" not in summary or summary["relative_cell_count"] >= 3
    assert empty_theme_plan().enabled is False
