"""Tests for reference.conditioning.policy.v1 assembly, generate/edit wire, AC multi-ref."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import initialize_database, reset_database_initialization_cache
from app.embeddings.schemas import EmbedScopeComposition, StyleReferenceRequest
from app.main import app
from app.reference_conditioning_schemas import (
    ReferenceConditioningPolicy,
    collect_borrow_dimension_union,
    validate_motif_reuse_gate,
    validate_policy_partition,
    validate_preserve_requires_source,
)
from app.reference_feature_schemas import ReferenceFeatureError
from app.schemas import (
    LLMCompositionEditRequest,
    LLMMusicGenerationRequest,
    LLMPromptParameters,
)
from app.services.reference_conditioning_policy import assemble_reference_conditioning

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "fixtures"
    / "composition_v2_expressive.json"
)


@pytest.fixture
def composition_dict():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    monkeypatch.setenv("DATASET_ROOT", str(tmp_path / "dataset_should_stay_empty"))
    reset_database_initialization_cache()
    initialize_database()
    return TestClient(app), tmp_path


def test_partition_overlap_raises():
    policy = ReferenceConditioningPolicy(
        preserve_dimensions=["texture"],
        regenerate_dimensions=[],
    )
    with pytest.raises(ReferenceFeatureError) as exc:
        validate_policy_partition(policy, borrow_dimensions=["texture"])
    assert exc.value.code == "reference_conditioning_partition_overlap"


def test_borrow_dimension_conflict_raises():
    with pytest.raises(ReferenceFeatureError) as exc:
        collect_borrow_dimension_union([["texture"], ["texture", "rhythm"]])
    assert exc.value.code == "reference_conditioning_borrow_dimension_conflict"


def test_preserve_without_source_raises():
    with pytest.raises(ReferenceFeatureError) as exc:
        validate_preserve_requires_source(
            preserve_dimensions=["rhythm"],
            has_current_composition=False,
        )
    assert exc.value.code == "reference_conditioning_preserve_without_source"


def test_motif_reuse_forbidden_without_active_project():
    with pytest.raises(ReferenceFeatureError) as exc:
        validate_motif_reuse_gate(
            allow_motif_reuse=True,
            active_project_id=None,
            borrow_binding_project_ids=["proj-a"],
        )
    assert exc.value.code == "reference_conditioning_motif_reuse_forbidden"


def test_ac_multi_ref_texture_a_rhythm_b(composition_dict):
    """Primary AC: borrow texture from A + rhythm from B; regenerate melody/harmony."""
    policy = ReferenceConditioningPolicy(
        preserve_dimensions=[],
        regenerate_dimensions=["melodic_contour", "harmony"],
        dimension_strengths={"texture": "strong", "rhythm": "normal"},
        default_borrow_strength="normal",
    )
    ref_a = StyleReferenceRequest(
        composition=composition_dict,
        scope=EmbedScopeComposition(),
        dimensions=["texture"],
    )
    ref_b = StyleReferenceRequest(
        composition=composition_dict,
        scope=EmbedScopeComposition(),
        dimensions=["rhythm"],
    )
    result = assemble_reference_conditioning(
        style_references=[ref_a, ref_b],
        policy=policy,
        operation="generate",
    )
    soft = result.soft_fragment
    assert "dim=texture" in soft
    assert "dim=rhythm" in soft
    assert "strength=strong" in soft
    assert "melodic_contour" in soft
    assert "harmony" in soft
    # Must not dump A melody / B harmony as borrow fragments.
    assert "dim=melodic_contour] [" not in soft or "[regenerate dim=melodic_contour]" in soft
    assert "BORROW" in soft
    assert "REGENERATE" in soft
    assert result.policy_provenance is not None
    assert result.policy_provenance["policy_digest"]
    assert len(result.provenance_entries) == 2
    dumped = json.dumps(result.policy_provenance)
    assert "events" not in dumped
    assert "vector" not in dumped


def test_strength_off_drops_borrow_dim(composition_dict):
    policy = ReferenceConditioningPolicy(
        regenerate_dimensions=["melodic_contour"],
        dimension_strengths={"texture": "off", "rhythm": "normal"},
    )
    result = assemble_reference_conditioning(
        style_reference=StyleReferenceRequest(
            composition=composition_dict,
            scope=EmbedScopeComposition(),
            dimensions=["texture", "rhythm"],
        ),
        policy=policy,
        operation="generate",
    )
    assert "dim=texture" not in result.soft_fragment
    assert "dim=rhythm" in result.soft_fragment
    assert "reference_conditioning_strength_off_dropped" in result.warning_codes


def test_generate_http_wires_policy(client, composition_dict):
    http, tmp_path = client
    payload = {
        "prompt": {
            "genre": "classical",
            "mood": "calm",
            "time_signature": "4/4",
            "tempo_min": 80,
            "tempo_max": 100,
            "instruments": ["piano", "bass"],
            "complexity": "moderate",
            "duration_bars": 4,
        },
        "selection": {"provider": "fake", "model": "fake-v1"},
        "options": {"pipeline": "llm_only", "max_retries": 0},
        "style_references": [
            {
                "composition": composition_dict,
                "scope": {"kind": "composition"},
                "dimensions": ["texture"],
                "mode": "prompt_features",
            },
            {
                "composition": composition_dict,
                "scope": {"kind": "composition"},
                "dimensions": ["rhythm"],
                "mode": "prompt_features",
            },
        ],
        "reference_conditioning_policy": {
            "schema_version": "reference.conditioning.policy.v1",
            "preserve_dimensions": [],
            "regenerate_dimensions": ["melodic_contour", "harmony"],
            "dimension_strengths": {"texture": "strong", "rhythm": "normal"},
            "default_borrow_strength": "normal",
            "allow_motif_reuse": False,
            "strict_partition": True,
        },
    }
    response = http.post("/llm/generate-music-json", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["music"]["schema_version"] == "composition.v2"
    gp = body.get("generation_parameters") or {}
    assert "reference_features" in gp
    assert "reference_conditioning_policy" in gp
    assert gp["reference_conditioning_policy"]["policy_digest"]
    dataset = tmp_path / "dataset_should_stay_empty"
    assert not any(dataset.rglob("*")) if dataset.exists() else True


def test_generate_preserve_without_source_422(client, composition_dict):
    http, _ = client
    payload = {
        "prompt": {
            "genre": "jazz",
            "mood": "calm",
            "time_signature": "4/4",
            "tempo_min": 90,
            "tempo_max": 100,
            "instruments": ["piano", "bass"],
            "complexity": "moderate",
            "duration_bars": 4,
        },
        "selection": {"provider": "fake", "model": "fake-v1"},
        "options": {"pipeline": "llm_only", "max_retries": 0},
        "style_reference": {
            "composition": composition_dict,
            "scope": {"kind": "composition"},
            "dimensions": ["texture"],
            "mode": "prompt_features",
        },
        "reference_conditioning_policy": {
            "preserve_dimensions": ["rhythm"],
            "regenerate_dimensions": [],
            "dimension_strengths": {},
            "default_borrow_strength": "normal",
            "allow_motif_reuse": False,
            "strict_partition": True,
        },
    }
    response = http.post("/llm/generate-music-json", json=payload)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "reference_conditioning_preserve_without_source"


def test_edit_returns_generation_parameters(composition_dict):
    """Edit soft inject + provenance for Apply CAS (no soft/event dumps)."""
    request = LLMCompositionEditRequest.model_validate(
        {
            "composition": composition_dict,
            "edit": {
                "instruction": "regenerate melody in selection",
                "selection": {"start_bar": 1, "end_bar": 2},
                "allow_harmony_changes": False,
                "allow_added_tracks": False,
            },
            "selection": {"provider": "fake", "model": "fake-v1"},
            "style_reference": {
                "composition": composition_dict,
                "scope": {"kind": "composition"},
                "dimensions": ["texture"],
                "mode": "prompt_features",
            },
            "reference_conditioning_policy": {
                "preserve_dimensions": ["harmony"],
                "regenerate_dimensions": ["melodic_contour"],
                "dimension_strengths": {"texture": "normal"},
                "default_borrow_strength": "normal",
                "allow_motif_reuse": False,
                "strict_partition": True,
            },
        }
    )
    from app.services.llm_composition_editor import _resolve_edit_reference_conditioning

    soft, generation_parameters = _resolve_edit_reference_conditioning(request)
    assert soft
    assert generation_parameters is not None
    assert "reference_features" in generation_parameters
    assert "reference_conditioning_policy" in generation_parameters
    assert "dim=texture" in soft or "texture" in soft
    assert "events" not in json.dumps(generation_parameters)


def test_legacy_dimensions_omitted_still_works(composition_dict):
    result = assemble_reference_conditioning(
        style_reference=StyleReferenceRequest(
            composition=composition_dict,
            scope=EmbedScopeComposition(),
        ),
        policy=None,
        operation="generate",
    )
    assert result.policy_provenance is None
    # Legacy whole summary path may produce soft or legacy fragment.
    assert result.soft_fragment or result.legacy_feature_summary
