"""Unit tests for composition.plan.v1 schema contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.composition_plan_schemas import (
    COMPOSITION_PLAN_SCHEMA_VERSION,
    CompositionPlan,
    CompositionPlanError,
    parse_composition_plan,
    summarize_composition_plan,
)


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "composition_plan"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_valid_minimal_plan_parses() -> None:
    payload = _load("valid_minimal.json")
    plan = parse_composition_plan(payload)
    assert plan.schema_version == COMPOSITION_PLAN_SCHEMA_VERSION
    assert plan.form.bar_count == 8
    assert len(plan.form.sections) == 3
    assert plan.motifs_themes.enabled is True
    assert plan.density.global_band == "moderate"
    assert plan.constraints_digest is not None
    # Relative cells are planning aids, not a playable score.
    assert plan.motifs_themes.seed is not None
    assert len(plan.motifs_themes.seed.relative_cell) == 2


def test_model_validate_rejects_extra_fields() -> None:
    payload = _load("valid_minimal.json")
    payload["mystery_score"] = {"notes": []}
    with pytest.raises(ValidationError):
        CompositionPlan.model_validate(payload)


def test_forbidden_tracks_field_raises_plan_error() -> None:
    payload = _load("invalid_with_tracks.json")
    with pytest.raises(CompositionPlanError) as exc_info:
        parse_composition_plan(payload)
    assert exc_info.value.code == "plan_forbidden_playable_fields"


def test_forbidden_top_level_events_raises_plan_error() -> None:
    payload = _load("invalid_top_level_events.json")
    with pytest.raises(CompositionPlanError) as exc_info:
        parse_composition_plan(payload)
    assert exc_info.value.code == "plan_forbidden_playable_fields"


def test_empty_sections_rejected_by_form() -> None:
    payload = _load("valid_minimal.json")
    payload["form"]["sections"] = []
    payload["form"]["bar_count"] = 0
    with pytest.raises(CompositionPlanError) as exc_info:
        parse_composition_plan(payload)
    assert exc_info.value.code in {"plan_empty_sections", "plan_invalid"}


def test_density_section_out_of_bounds() -> None:
    payload = _load("valid_minimal.json")
    payload["density"]["per_section"] = [{"section_index": 99, "band": "dense"}]
    with pytest.raises(CompositionPlanError) as exc_info:
        parse_composition_plan(payload)
    assert exc_info.value.code in {"plan_density_section_oob", "plan_invalid"}


def test_target_range_inverted_rejected() -> None:
    payload = _load("valid_minimal.json")
    payload["target_ranges"] = [{"role": "melody", "midi_min": 80, "midi_max": 60}]
    with pytest.raises(CompositionPlanError) as exc_info:
        parse_composition_plan(payload)
    assert exc_info.value.code in {"plan_target_range_invalid", "plan_invalid"}


def test_summarize_omits_instructions_and_relative_cells() -> None:
    plan = parse_composition_plan(_load("valid_minimal.json"))
    summary = summarize_composition_plan(plan)
    assert summary["present"] is True
    assert summary["schema_version"] == COMPOSITION_PLAN_SCHEMA_VERSION
    assert "stylistic_instructions" not in summary
    assert "relative_cell" not in summary
    assert summary["theme_relative_cell_count"] == 2
    assert summary["has_stylistic_instructions"] is True
    dumped = json.dumps(summary)
    assert "Warm lyrical" not in dumped
    assert "pitch_semitone_offset" not in dumped


def test_harmony_chord_events_are_allowed() -> None:
    """Nested harmony.events are chord/bar planning spans, not note events."""
    plan = parse_composition_plan(_load("valid_minimal.json"))
    assert len(plan.harmony.events) == 4
    assert plan.harmony.events[0].chord == "C"
