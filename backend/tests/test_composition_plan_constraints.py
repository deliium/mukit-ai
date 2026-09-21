"""Tests for composition plan ↔ GenerationConstraints conformance."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.composition_plan_schemas import CompositionPlanError, parse_composition_plan
from app.schemas import LLMMusicGenerationRequest, LLMPromptParameters
from app.services.composition_plan_constraints import (
    attach_constraints_digest,
    constraints_digest_for,
    ensure_plan_conforms,
    validate_plan_against_constraints,
)
from app.services.generation_constraints import build_generation_constraints


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "composition_plan"


def _plan():
    return parse_composition_plan(json.loads((FIXTURES / "valid_minimal.json").read_text()))


def _constraints(
    *,
    key: str | None = "C major",
    duration_bars: int = 8,
    time_signature: str = "4/4",
    tempo_min: int = 100,
    tempo_max: int = 140,
    instruments: list[str] | None = None,
    sections: list | None = None,
):
    prompt = LLMPromptParameters(
        mood="calm",
        genre="classical",
        key=key,
        time_signature=time_signature,
        tempo_min=tempo_min,
        tempo_max=tempo_max,
        duration_bars=duration_bars,
        instruments=instruments or ["piano", "bass"],
        complexity="moderate",
        sections=sections or [],
    )
    return build_generation_constraints(
        LLMMusicGenerationRequest(prompt=prompt),
        allow_extra_instrument_families=False,
    )


def test_constraints_digest_stable() -> None:
    constraints = _constraints()
    d1 = constraints_digest_for(constraints)
    d2 = constraints_digest_for(constraints)
    assert d1 == d2
    assert d1.startswith("sha256:")


def test_attach_and_validate_matching_plan() -> None:
    plan = _plan()
    constraints = _constraints()
    locked = attach_constraints_digest(plan, constraints)
    assert locked.constraints_digest == constraints_digest_for(constraints)
    diagnostics = validate_plan_against_constraints(
        locked, constraints, require_digest_match=True
    )
    assert [d.code for d in diagnostics if d.severity == "error"] == []


def test_bar_count_mismatch_is_hard_error() -> None:
    plan = _plan()
    constraints = _constraints(duration_bars=16)
    diagnostics = validate_plan_against_constraints(plan, constraints)
    codes = [d.code for d in diagnostics if d.severity == "error"]
    assert "constraint_bar_count_mismatch" in codes


def test_tempo_out_of_range_is_hard_error() -> None:
    plan = _plan()
    constraints = _constraints(tempo_min=40, tempo_max=60)
    diagnostics = validate_plan_against_constraints(plan, constraints)
    codes = [d.code for d in diagnostics if d.severity == "error"]
    assert "constraint_tempo_out_of_range" in codes


def test_ensure_plan_conforms_raises_on_mismatch() -> None:
    plan = _plan()
    constraints = _constraints(duration_bars=16)
    with pytest.raises(CompositionPlanError) as exc_info:
        ensure_plan_conforms(plan, constraints)
    assert exc_info.value.code == "plan_constraint_mismatch"
