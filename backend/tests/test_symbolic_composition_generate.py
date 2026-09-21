"""Tests for fake symbolic composer and symbolic generate adapter."""

from __future__ import annotations

import json
import os
from pathlib import Path

from app.composition_plan_schemas import parse_composition_plan
from app.services.fake_symbolic_composer import (
    FAKE_SYMBOLIC_MODEL_ID,
    generate_fake_symbolic_composition,
)
from app.services.symbolic_composition_generate import (
    generate_symbolic_composition,
    plan_to_tokenizer_conditioning,
    resolve_symbolic_backend,
)


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "composition_plan"


def _plan():
    return parse_composition_plan(json.loads((FIXTURES / "valid_minimal.json").read_text()))


def test_fake_symbolic_deterministic_for_seed() -> None:
    plan = _plan()
    a, _ = generate_fake_symbolic_composition(plan, seed=11)
    b, _ = generate_fake_symbolic_composition(plan, seed=11)
    assert a.model_dump(mode="json") == b.model_dump(mode="json")
    c, _ = generate_fake_symbolic_composition(plan, seed=12)
    assert a.model_dump(mode="json") != c.model_dump(mode="json")


def test_fake_symbolic_has_required_roles_and_notes() -> None:
    music, report = generate_fake_symbolic_composition(_plan(), seed=0)
    roles = {track.role for track in music.tracks}
    assert "melody" in roles or "lead" in roles
    assert "bass" in roles
    assert roles.intersection({"harmony", "pad", "accompaniment", "rhythm"})
    assert sum(len(track.events) for track in music.tracks) > 0
    assert report["model_id"] == FAKE_SYMBOLIC_MODEL_ID
    assert report["fallback_applied"] is False


def test_adapter_prefer_fake() -> None:
    result = generate_symbolic_composition(_plan(), seed=3, prefer_fake=True)
    assert result.backend == "fake"
    assert result.model_id == FAKE_SYMBOLIC_MODEL_ID
    assert result.composition.schema_version == "composition.v2"
    assert result.conditioning is not None
    assert result.conditioning.key == "C major"


def test_plan_to_conditioning() -> None:
    conditioning = plan_to_tokenizer_conditioning(_plan(), genre="classical", mood="calm")
    assert conditioning.key == "C major"
    assert conditioning.genre == "classical"
    assert conditioning.mood == "calm"


def test_resolve_backend_fake_mode(monkeypatch) -> None:
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    monkeypatch.delenv("MUSIC_TRANSFORMER_CHECKPOINT", raising=False)
    assert resolve_symbolic_backend(env=os.environ) == "fake"
