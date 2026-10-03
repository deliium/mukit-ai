"""Backend tests for reference.features.v1 analyzer, mask merge, generate wire, privacy."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import initialize_database, reset_database_initialization_cache
from app.embeddings.features import FEATURE_GROUP_SLICES, feature_group_vector
from app.embeddings.schemas import EmbedScopeComposition, StyleReferenceRequest
from app.main import app
from app.reference_feature_schemas import (
    ReferenceFeatureAnalyzeRequest,
    ReferenceFeatureCompareTarget,
    ReferenceFeatureError,
)
from app.schemas import LLMMusicGenerationRequest, LLMPromptParameters
from app.services.generation_constraints import build_generation_constraints
from app.services.reference_feature_analyze import analyze_reference_features
from app.services.reference_feature_condition import (
    resolve_reference_feature_conditioning,
)

_ATTESTED_RIGHTS = {"status": "user_owned", "user_owned_attested": True}

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


def test_feature_group_vector_slices(composition_dict):
    from app.composition_schemas import CompositionV2
    from app.embeddings.features import extract_symbolic_features_v1

    features, _ = extract_symbolic_features_v1(
        CompositionV2.model_validate(composition_dict), EmbedScopeComposition()
    )
    for group_id, (_start, length) in FEATURE_GROUP_SLICES.items():
        slice_vec = feature_group_vector(features, group_id)
        assert len(slice_vec) == length


def test_analyze_density_texture_ok(composition_dict):
    report = analyze_reference_features(
        ReferenceFeatureAnalyzeRequest(
            composition=composition_dict,
            requested_dimensions=["density", "texture"],
            rights=_ATTESTED_RIGHTS,
        )
    )
    assert set(report.dimensions) == {"density", "texture"}
    assert report.dimensions["density"].status in {"ok", "degraded"}
    assert report.dimensions["texture"].status in {"ok", "degraded"}
    dumped = report.model_dump(mode="json")
    assert "events" not in dumped
    assert "vector" not in json.dumps(dumped)


def test_analyze_http_route(client, composition_dict):
    http, tmp_path = client
    response = http.post(
        "/reference-features/analyze",
        json={
            "composition": composition_dict,
            "requested_dimensions": ["density", "texture"],
            "scope": {"kind": "composition"},
            "rights": _ATTESTED_RIGHTS,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["report"]["schema_version"] == "reference.features.v1"
    assert "density" in body["report"]["dimensions"]
    # No affinity without compare_to
    assert body["report"]["embedding_affinity"] is None
    assert "reference_feature_compare_omitted" in body["report"]["warning_codes"]
    # Dataset root stays empty
    dataset = tmp_path / "dataset_should_stay_empty"
    assert not dataset.exists() or not any(dataset.iterdir())


def test_analyze_with_compare_to_affinity(client, composition_dict):
    http, _ = client
    response = http.post(
        "/reference-features/analyze",
        json={
            "composition": composition_dict,
            "requested_dimensions": ["density", "melodic_contour"],
            "rights": _ATTESTED_RIGHTS,
            "compare_to": {
                "composition": composition_dict,
                "scope": {"kind": "composition"},
            },
        },
    )
    assert response.status_code == 200
    affinity = response.json()["report"]["embedding_affinity"]
    assert affinity is not None
    assert affinity["musical_quality_claim"] is False
    assert "density" in affinity["per_dimension"]
    assert "rhythm" in affinity["per_group"]


def test_mask_empty_dimensions_raises(composition_dict):
    binding = StyleReferenceRequest(
        composition=composition_dict,
        scope=EmbedScopeComposition(),
        dimensions=[],
    )
    with pytest.raises(ReferenceFeatureError) as exc:
        resolve_reference_feature_conditioning(style_reference=binding)
    assert exc.value.code == "reference_feature_mask_empty"


def test_ac_density_texture_only_in_soft_fragment(composition_dict):
    binding = StyleReferenceRequest(
        composition=composition_dict,
        scope=EmbedScopeComposition(),
        dimensions=["density", "texture"],
    )
    result = resolve_reference_feature_conditioning(style_reference=binding)
    frag = result.soft_fragment.lower()
    assert "density" in frag or "rhythmic density" in frag
    assert "texture" in frag or "orchestration" in frag
    assert "harmony" not in frag
    assert "melodic contour" not in frag
    assert "do not copy melodies" in frag
    assert set(result.applied_dimension_ids) == {"density", "texture"}


def test_singular_ignored_when_multi_present(composition_dict):
    singular = StyleReferenceRequest(
        composition=composition_dict,
        scope=EmbedScopeComposition(),
        dimensions=["harmony"],
    )
    multi = [
        StyleReferenceRequest(
            composition=composition_dict,
            scope=EmbedScopeComposition(),
            dimensions=["density"],
        )
    ]
    result = resolve_reference_feature_conditioning(
        style_reference=singular, style_references=multi
    )
    assert "reference_feature_singular_ignored" in result.warning_codes
    assert result.applied_dimension_ids == ["density"]
    assert "harmony" not in result.applied_dimension_ids


def test_generate_wires_masked_reference(client, composition_dict):
    http, _ = client
    # Prompt instruments/key must remain request-owned.
    request = LLMMusicGenerationRequest(
        prompt=LLMPromptParameters(
            genre="jazz",
            mood="calm",
            key="D major",
            instruments=["piano", "bass"],
            duration_bars=4,
        ),
        style_reference=StyleReferenceRequest(
            composition=composition_dict,
            scope=EmbedScopeComposition(),
            dimensions=["density", "texture"],
        ),
    )
    before = request.prompt.model_dump()
    constraints = build_generation_constraints(request)
    assert request.prompt.model_dump() == before
    assert constraints.key == "D major"
    assert request.prompt.instruments == ["piano", "bass"]

    response = http.post(
        "/llm/generate-music-json",
        json={
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
                "dimensions": ["density", "texture"],
            },
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    gp = body.get("generation_parameters") or {}
    refs = gp.get("reference_features")
    assert refs
    assert set(refs[0]["dimensions"]) == {"density", "texture"}
    assert any("reference-feature" in w.lower() for w in body.get("warnings") or [])


def test_composition_unchanged_by_analyze(composition_dict):
    before = json.dumps(composition_dict, sort_keys=True)
    analyze_reference_features(
        ReferenceFeatureAnalyzeRequest(
            composition=composition_dict,
            requested_dimensions=["density", "texture", "melodic_contour"],
            rights=_ATTESTED_RIGHTS,
        )
    )
    after = json.dumps(composition_dict, sort_keys=True)
    assert before == after


def test_missing_project_404(client):
    http, _ = client
    response = http.post(
        "/reference-features/analyze",
        json={"project_id": "missing-project-xyz", "scope": {"kind": "composition"}},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "reference_feature_not_found"
