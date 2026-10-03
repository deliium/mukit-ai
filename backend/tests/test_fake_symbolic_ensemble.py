"""Deterministic sparse/dense fake composers and registry discovery."""

from __future__ import annotations

import json
from pathlib import Path

from app.ai_runtime.capabilities import ModelCapability
from app.ai_runtime.registry import list_models, reload_registry
from app.composition_plan_schemas import parse_composition_plan
from app.ensemble_arbitration_schemas import FAKE_ENSEMBLE_MODEL_IDS
from app.services.composition_validator import validate_composition_integrity
from app.services.fake_symbolic_composer import generate_fake_symbolic_composition
from app.services.symbolic_composition_generate import generate_symbolic_composition

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "composition_plan"


def _shared_plan():
    payload = json.loads((FIXTURES / "valid_minimal.json").read_text(encoding="utf-8"))
    return parse_composition_plan(payload)


def _event_count(music) -> int:
    return sum(len(track.events) for track in music.tracks)


def test_three_fake_models_validate_and_differ():
    plan = _shared_plan()
    results = {}
    for model_id in FAKE_ENSEMBLE_MODEL_IDS:
        music, report = generate_fake_symbolic_composition(
            plan, seed=11, model_id=model_id
        )
        integrity = validate_composition_integrity(
            music, complexity="simple", profile="generation"
        )
        assert integrity.ok, model_id
        assert report["model_id"] == model_id
        results[model_id] = (_event_count(music), music)

    counts = {mid: count for mid, (count, _) in results.items()}
    tiny, sparse, dense = FAKE_ENSEMBLE_MODEL_IDS
    assert counts[sparse] < counts[tiny] < counts[dense]
    # Pairwise distinct note material fingerprints (event counts differ).
    assert len(set(counts.values())) == 3


def test_generate_symbolic_composition_echoes_requested_fake_id():
    plan = _shared_plan()
    for model_id in FAKE_ENSEMBLE_MODEL_IDS:
        result = generate_symbolic_composition(
            plan, seed=3, prefer_fake=True, model_id=model_id
        )
        assert result.model_id == model_id
        assert result.report["model_id"] == model_id
        assert result.backend == "fake"


def test_discovery_lists_all_three_fake_symbolic_composers(monkeypatch):
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    reload_registry()
    models = list_models(capability=ModelCapability.SYMBOLIC_COMPOSER, status="ready")
    ids = {item.id for item in models}
    for model_id in FAKE_ENSEMBLE_MODEL_IDS:
        assert model_id in ids, model_id
