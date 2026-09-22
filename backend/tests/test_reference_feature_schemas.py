"""Unit tests for reference.features.v1 schema validation and forbid gates."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.reference_feature_schemas import (
    DIMENSION_IDS,
    DIMENSION_UI_LABELS,
    REFERENCE_FEATURES_SCHEMA,
    ReferenceFeatureAnalyzeRequest,
    ReferenceFeatureError,
    ReferenceFeaturesV1,
    dimension_ui_label,
    normalize_requested_dimensions,
    parse_dimension_id,
)


def _minimal_report(**overrides):
    base = {
        "source": {
            "kind": "inline",
            "source_fingerprint": "a" * 32,
            "import_origin": "generated",
        },
        "scope": {"kind": "composition"},
        "scope_digest": "b" * 16,
        "requested_dimensions": ["density", "texture"],
        "dimensions": {
            "density": {
                "status": "ok",
                "summary": "Moderate rhythmic density.",
                "soft_fragment": "density: moderate notes-per-bar",
                "evidence": {"note_count": 12},
            },
            "texture": {
                "status": "ok",
                "summary": "Sparse orchestration texture.",
                "soft_fragment": "texture: low concurrent voices",
                "evidence": {"note_count": 12, "track_count": 2},
            },
        },
    }
    base.update(overrides)
    return ReferenceFeaturesV1.model_validate(base)


def test_minimal_report_validates():
    report = _minimal_report()
    assert report.schema_version == REFERENCE_FEATURES_SCHEMA
    assert report.algorithm_version == "reference.features.v1.0"
    assert set(report.dimensions) == {"density", "texture"}
    assert report.embedding_affinity is None


def test_ui_label_map_ac():
    assert DIMENSION_UI_LABELS["density"] == "Rhythmic density"
    assert DIMENSION_UI_LABELS["texture"] == "Orchestration texture"
    assert dimension_ui_label("density") == "Rhythmic density"
    assert set(DIMENSION_UI_LABELS) == set(DIMENSION_IDS)


def test_forbid_events_and_tracks_at_root():
    with pytest.raises(ValidationError) as exc:
        ReferenceFeaturesV1.model_validate(
            {
                "source": {
                    "kind": "inline",
                    "source_fingerprint": "a" * 32,
                },
                "scope": {"kind": "composition"},
                "scope_digest": "b" * 16,
                "events": [{"pitch": 60}],
                "tracks": [],
            }
        )
    assert "forbidden" in str(exc.value).lower()


def test_forbid_embedding_vector_and_artist():
    with pytest.raises(ValidationError):
        _minimal_report(
            dimensions={
                "density": {
                    "status": "ok",
                    "summary": "x",
                    "soft_fragment": "y",
                    "embedding": {"vector": [0.1]},
                }
            }
        )
    with pytest.raises(ValidationError):
        ReferenceFeatureAnalyzeRequest.model_validate(
            {
                "composition": {"schema": "composition.v2"},
                "artist": "Someone",
            }
        )


def test_forbid_extra_unknown_keys():
    with pytest.raises(ValidationError):
        _minimal_report(secret_sauce="nope")


def test_parse_and_normalize_dimensions():
    assert parse_dimension_id("density") == "density"
    with pytest.raises(ReferenceFeatureError) as exc:
        parse_dimension_id("melody_copy")
    assert exc.value.code == "reference_feature_unknown_dimension"

    all_dims = normalize_requested_dimensions(None, default_all=True)
    assert all_dims == list(DIMENSION_IDS)
    all_dims_empty = normalize_requested_dimensions([], default_all=True)
    assert all_dims_empty == list(DIMENSION_IDS)

    with pytest.raises(ReferenceFeatureError) as empty_exc:
        normalize_requested_dimensions([], default_all=False)
    assert empty_exc.value.code == "reference_feature_mask_empty"

    masked = normalize_requested_dimensions(
        ["density", "texture", "density"], default_all=False
    )
    assert masked == ["density", "texture"]


def test_analyze_request_requires_source():
    with pytest.raises(ValidationError):
        ReferenceFeatureAnalyzeRequest.model_validate({"scope": {"kind": "composition"}})

    req = ReferenceFeatureAnalyzeRequest.model_validate(
        {
            "project_id": "proj_1",
            "requested_dimensions": ["density", "texture"],
            "scope": {"kind": "bar_range", "start_bar": 1, "end_bar": 4},
        }
    )
    assert req.project_id == "proj_1"
    assert req.requested_dimensions == ["density", "texture"]
