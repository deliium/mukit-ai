"""Tests for deterministic EvaluationEngine (Task 6–7)."""

from __future__ import annotations

import copy

from app.ai_agents.schemas import CritiqueRecommendation
from app.composition_schemas import CompositionV2
from app.services.composition_critique import evaluate_composition
from tests.test_composition_v2_schema import minimal_v2


def _events_for_bars(*, bar_count: int, notes_per_bar: int, velocity: int = 80) -> list[dict]:
    events = []
    for bar in range(bar_count):
        for n in range(notes_per_bar):
            events.append(
                {
                    "pitch": "C4",
                    "start_tick": bar * 1920 + n * 120,
                    "duration_ticks": 100,
                    "velocity": velocity,
                }
            )
    return events


def _two_section_composition(*, climax_notes: int, prior_notes: int) -> CompositionV2:
    """8 bars: prior section bars 1–4, climax-labeled section bars 5–8."""
    prior_events = _events_for_bars(bar_count=4, notes_per_bar=prior_notes, velocity=80)
    climax_events = _events_for_bars(bar_count=4, notes_per_bar=climax_notes, velocity=82)
    # Offset climax events into bars 5–8.
    for ev in climax_events:
        ev["start_tick"] += 4 * 1920
    data = minimal_v2(
        bar_count=8,
        duration_ticks=8 * 1920,
        sections=[
            {
                "type": "verse",
                "label": "build",
                "start_bar": 1,
                "bar_count": 4,
                "start_tick": 0,
                "duration_ticks": 4 * 1920,
            },
            {
                "type": "chorus",
                "label": "climax peak",
                "start_bar": 5,
                "bar_count": 4,
                "start_tick": 4 * 1920,
                "duration_ticks": 4 * 1920,
            },
        ],
        tracks=[
            {
                "id": "piano-1",
                "name": "Piano",
                "instrument": "piano",
                "role": "melody",
                "midi_program": 0,
                "channel": 1,
                "events": prior_events + climax_events,
            }
        ],
    )
    return CompositionV2.model_validate(data)


def test_evaluate_does_not_mutate_composition():
    composition = _two_section_composition(climax_notes=3, prior_notes=3)
    before = composition.model_dump(mode="json")
    raw = copy.deepcopy(before)
    result = evaluate_composition(raw)
    assert raw == before
    assert composition.model_dump(mode="json") == before
    assert result.critique.engine_version.startswith("critique.engine")
    assert result.critique.recommendation in (
        CritiqueRecommendation.APPROVE,
        CritiqueRecommendation.REVISE,
    )


def test_climax_lacks_contrast_finding():
    composition = _two_section_composition(climax_notes=3, prior_notes=3)
    before = composition.model_dump(mode="json")
    result = evaluate_composition(composition)
    after = composition.model_dump(mode="json")
    assert before == after
    codes = [f.code for f in result.critique.findings]
    assert "climax_lacks_contrast" in codes
    climax = next(f for f in result.critique.findings if f.code == "climax_lacks_contrast")
    assert climax.stratum == "stylistic"
    assert climax.severity in ("info", "warning")
    assert climax.affected_range is not None
    assert climax.affected_range.start_bar == 5
    # Stylistic-only must not force revise.
    assert result.critique.recommendation == CritiqueRecommendation.APPROVE


def test_climax_with_contrast_skips_finding():
    composition = _two_section_composition(climax_notes=8, prior_notes=2)
    result = evaluate_composition(composition)
    codes = [f.code for f in result.critique.findings]
    assert "climax_lacks_contrast" not in codes


def test_bars_scope_resolves():
    composition = _two_section_composition(climax_notes=3, prior_notes=3)
    result = evaluate_composition(
        composition, scope={"kind": "bars", "start_bar": 5, "end_bar": 8}
    )
    assert result.resolved_scope.kind == "bars"
    assert result.critique.scope is not None
    assert result.critique.scope.kind == "bars"
